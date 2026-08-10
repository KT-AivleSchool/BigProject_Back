import json
import datetime
import asyncio
import logging
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.schemas.simulations import SimulationResultResponse, StreamRequest
from app.core.sim_ai.graph import build_discussion_graph
from app.core.sim_ai.vector_db import get_vector_db
from app.api.deps import get_db, get_redis
from app.db.models.simulation import Parcel, ConflictSimulation, DebateLog

# 🔴 `from app.services.pdf_service import pdf_builder` 를 여기서 뺐다 (2026-08-04).
#    이 모듈 전체가 그 한 줄 때문에 import 불가였다 — PDF 내보내기(화면6) 하나 때문에
#    공청회 토론(화면5) 라우터 512행이 통째로 못 떴다.
#    실사용은 `download_feasibility_report_pdf` **한 곳뿐**이라 그 함수 안으로 옮겼다.
#    화면6 을 붙이는 사람이 볼 것: 이 파일이 아니라 그 함수의 주석이다.
from app.db.models.audit import AuditRule
from app.services.gis_service import gis_service
from app.db.session import AsyncSessionLocal
from app.utils.redis_pubsub import RedisPubSubManager
from app.core.security_limiter import rate_limiter

# API 라우터 인스턴스 초기화
router = APIRouter()

# uvicorn 콘솔로 나가는 로거. `print` 는 백그라운드 태스크에서 묻힌다.
logger = logging.getLogger("uvicorn.error")

# 임시 DB 우회용 스위치 (Mock 데이터 모드 활성화 여부)
USE_MOCK_DB = False


# 시나리오 코드 → conflict_simulations 의 어느 칸에 넣을지.
# 근거는 `app/templates/default/reporter.txt` 다 — 수용도 0.8↑ A(원만한 타결),
# 0.4~0.8 B(조건부 타결), 0.4↓ C(협상 결렬). 여기서 새로 정한 대응이 아니다.
_SCENARIO_COLUMN = {
    "A": "optimal_scenario",
    "B": "normal_scenario",
    "C": "worst_scenario",
}


class CandidateNotFound(Exception):
    """`parcel_id` 로 booth_candidates 를 못 찾았다. 좌표를 지어내지 않고 멈추기 위한 예외."""


def _scenario_code(scenario: dict) -> str | None:
    """시나리오 객체에서 A/B/C 를 뽑는다. 못 뽑으면 None (추측하지 않는다)."""
    raw = str(scenario.get("scenario") or "").strip().upper()
    for ch in raw:
        if ch in _SCENARIO_COLUMN:
            return ch
    return None


def _scenario_text(scenario: dict) -> str:
    """시나리오 객체를 사람이 읽는 한 덩어리로. 원본은 result_json 에 그대로 남는다."""
    code = _scenario_code(scenario) or "?"
    lines = [f"[{code}] {scenario.get('scenario_description') or '설명 없음'}"]
    acc = scenario.get("final_acceptance_score")
    cri = scenario.get("conflict_risk_index")
    if acc is not None or cri is not None:
        lines.append(f"수용도 {acc} · 갈등위험지수 {cri}")
    if scenario.get("reason"):
        lines.append(f"사유: {scenario['reason']}")
    if scenario.get("risk_reason"):
        lines.append(f"위험 사유: {scenario['risk_reason']}")
    if scenario.get("summary"):
        lines.append(str(scenario["summary"]))
    return "\n".join(lines)


async def _persist_simulation(
    db: AsyncSession,
    parcel_id: int,
    facility_type: str,
    result_json: dict,
    css_score: float,
    scenarios: list[dict],
    debate_logs: list[dict],
) -> int:
    """STEP5 산출물을 `conflict_simulations` + `debate_logs` 에 적재하고 id 를 돌려준다.

    🔴 2026-08-09 신설(B안). 예전엔 `ConflictSimulation(parcel_id, facility_type,
       result_json)` 3필드를 그대로 넣었는데 **실 DB 에 그 셋이 다 없어서** 항상
       `UndefinedColumnError` 였다. 지금은 실 DB 컬럼까지 같이 채운다.

    쪼갠 값은 전부 `result_json` 에도 남는다 — 컬럼으로 안 쪼갠 값
    (candidate_lat/lng·intensity_level·timestamp)이 있어서 원본을 통째로 보관한다.
    """
    # 후보점이 놓인 필지. 기존 FK(candidate_land_id)를 dangling 으로 두지 않는다.
    # 값을 만들어 넣는 게 아니라 booth_candidates.land_id 에서 **유도**한다.
    land_id = await db.scalar(select(Parcel.land_id).where(Parcel.id == parcel_id))

    sim = ConflictSimulation(
        parcel_id=parcel_id,
        candidate_land_id=land_id,
        facility_type=facility_type,
        result_json=result_json,
        css_score=css_score,
        # css_vector 는 NOT NULL 이다. 요인별 가중치가 없으면 빈 객체로 둔다 —
        # 없는 가중치를 지어내지 않는다(원칙 5).
        css_vector=result_json.get("ahp_weights") or {},
    )

    # 엔진은 매 실행 A/B/C 중 **1개만** 낸다. 나머지 칸은 NULL 이 사실이다(원칙 4).
    for sc in scenarios or []:
        code = _scenario_code(sc)
        if code is None:
            # 조용히 버리지 않는다. result_json 에는 남아 있으므로 유실은 아니다.
            logger.warning(
                "[simulations] 시나리오 코드(A/B/C)를 못 읽었다 — 개별 컬럼은 비운다: %r",
                sc.get("scenario"),
            )
            continue
        setattr(sim, _SCENARIO_COLUMN[code], _scenario_text(sc))

    db.add(sim)
    await db.flush()  # sim.id 확보 (commit 전)

    for i, log in enumerate(debate_logs or []):
        db.add(
            DebateLog(
                simulation_id=sim.id,
                turn_index=i,
                sender=str(log.get("sender") or "참여자")[:50],
                message=str(log.get("text") or ""),
            )
        )

    await db.commit()
    return sim.id


async def _select_audit_rules(
    db: AsyncSession, facility_type: str, domain: str, run_id: str
) -> list[AuditRule]:
    """`audit_rules` 에서 **이 실행·이 도메인·이 시설의** 규칙만 가져온다.

    🔴 2026-08-09 신설. 예전엔 두 함수가 각자 `select(AuditRule)` 로 전량을 읽었다.
       도메인이 하나뿐일 땐 안 걸리지만, 성동구(재활용정거장)가 붙는 순간
       흡연부스 규칙으로 재활용정거장 토론을 한다 — 안 터지고 값만 틀린다.

    🔴 2026-08-10 `domain` 추가. `target_facility` 만으로는 부족하다 —
       적재기(`load_audit_data.py`)는 **`(domain, run_id)` 단위**로 지우고 넣는데
       읽기가 도메인을 안 걸렀다. 같은 시설을 쓰는 도메인이 둘이면(실측: 흡연 13 +
       흡연_E2E 13 = **26행**) 감리 근거가 두 배가 되고 AHP 가중치가 갈라진다.

    🔴 2026-08-10(2) `run_id` 추가 — **적재 단위와 완전히 같아졌다**(사람 승인).
       도메인까지 걸러도 **같은 도메인을 두 번 돌리면** 또 쌓인다. 적재기는
       `(domain, run_id)` 를 교체하므로 2회차에 두 run 의 규칙이 공존한다
       (실측 26행 · hard 10 + positive 16, 고유 요인명은 14). 예외가 안 나고
       AHP 가중치만 묽어진다 — **안 터지고 값만 틀리는** 유형이다.

       `run_id` 는 요청으로 받지 않는다. `domain` 과 **같이** `booth_candidates`
       행에서 온다(`/stream` 이 `parcel_id` 로 이미 읽는 그 행). 같은 행에서 뽑으면
       "논의 대상"과 "논의 근거"가 어긋날 수가 없다. 파라미터로 받으면 A run 의
       후보점에 B run 의 감리 근거를 넘길 수 있다.

       ⚠ 정본 산출물의 run_id 는 두 테이블 모두 `"정본"` 이다. 예전엔 각 적재기가
       STEP 폴더 이름(`step1_output` / `step4_output`)을 넣어 **같은 정본인데 값이
       갈렸다** — 그대로 두고 run_id 를 걸면 정본 도메인이 0건이 된다.

    맞는 행이 0개면 **조용히 넓히지 않고 raise 한다**(원칙 1). 예컨대 run_id 를 빼고
    다시 찾으면 다른 실행의 근거로 5분짜리 토론이 완주한다.
    바깥 except 가 SSE 에러 패킷으로 바꿔주므로 프런트는 "AI 엔진 오류"가 아니라
    무엇이 없는지를 받는다.
    """
    rows = (
        (
            await db.execute(
                select(AuditRule).where(
                    AuditRule.domain == domain,
                    AuditRule.run_id == run_id,
                    AuditRule.target_facility == facility_type,
                )
            )
        )
        .scalars()
        .all()
    )
    if rows:
        return list(rows)

    total = await db.scalar(select(func.count()).select_from(AuditRule))
    if not total:
        raise RuntimeError(
            "audit_rules 가 비어 있다. STEP1 감리 산출물을 먼저 적재할 것: "
            "python scripts/load_audit_data.py <도메인>"
        )
    # 어느 쪽이 어긋났는지 구분해서 알려준다 — 도메인이 없는 것, 그 도메인에 그 실행이
    # 없는 것, 그 실행에 그 시설이 없는 것은 처치가 전부 다르다.
    known = (
        (
            await db.execute(
                select(
                    AuditRule.domain, AuditRule.run_id, AuditRule.target_facility
                ).distinct()
            )
        )
        .all()
    )
    raise RuntimeError(
        f"audit_rules 에 도메인 '{domain}' · 실행 '{run_id}' · 시설 '{facility_type}' "
        f"규칙이 없다. 적재된 (도메인, 실행, 시설): {[tuple(r) for r in known]}. "
        f"적재: python scripts/load_audit_data.py {domain}"
        + (f" --run {run_id}" if run_id != "정본" else "")
    )


async def _fetch_and_parse_audit_rules_from_db(
    db: AsyncSession, facility_type: str, domain: str, run_id: str
) -> str:
    """DB에서 AuditRule을 가져와 포맷팅된 문자열로 반환"""
    if USE_MOCK_DB:
        try:
            with open("dummy_audit.json", "r", encoding="utf-8") as f:
                audit_data = json.load(f)

            class MockRule:
                def __init__(self, r_type, rat, src):
                    self.role_type = r_type
                    self.rationale = rat
                    self.source = src

            rules = []
            for result in audit_data.get("results", []):
                for role in result.get("roles", []):
                    rules.append(
                        MockRule(
                            role.get("role"), role.get("rationale"), role.get("source")
                        )
                    )
        except Exception as e:
            print(f"Mock Audit Load Error: {e}")
            rules = []
    else:
        rules = await _select_audit_rules(db, facility_type, domain, run_id)

    if not rules:
        return "프론트엔드 감리 데이터 없음"

    positive = set()
    negative = set()
    hard_exclusion = set()

    for r in rules:
        rationale = r.rationale or ""
        # 산출물의 `source` 는 조항 문자열이거나 리터럴 'human_confirmed' 다
        # (gam2_audit_judgment_test.apply_radius_answer 가 사람 확정 시 덮어쓴다).
        # 그대로 찍으면 프롬프트에 "(근거: human_confirmed)" 가 들어간다.
        source = r.source or "출처 불명"
        if source == "human_confirmed":
            source = "담당자 확정(HITL)"

        if r.role_type == "positive_factor":
            positive.add(f"- {rationale}")
        elif r.role_type == "negative_factor":
            negative.add(f"- {rationale}")
        elif r.role_type == "hard_exclusion":
            # 🔴 배제반경은 토론의 핵심 사실이다. 예전 스키마엔 컬럼이 없어
            #    "10m 이내 금지" 가 프롬프트에서 통째로 빠져 있었다.
            what = r.facility_type or "해당 시설"
            if r.exclusion_radius_m is not None:
                head = f"{what} 반경 {int(r.exclusion_radius_m)}m 이내 설치 금지"
            elif r.exclusion_type == "polygon":
                head = f"{what} 구역 내 설치 금지(면 배제)"
            else:
                head = f"{what} 배제(반경 미확정)"
            hard_exclusion.add(f"- [절대금지] {head} — {rationale} (근거: {source})")

    lines = []
    if positive:
        lines.append("## 설치 가점 요인\n" + "\n".join(sorted(positive)))
    if negative:
        lines.append("## 설치 감점/갈등 요인\n" + "\n".join(sorted(negative)))
    if hard_exclusion:
        lines.append("## 절대 배제(금지) 요인\n" + "\n".join(sorted(hard_exclusion)))

    if not lines:
        return "유효한 감리 팩터가 발견되지 않았습니다."

    return "\n\n".join(lines)


async def _extract_dynamic_meta_from_audit_rules(
    db: AsyncSession, facility_type: str, domain: str, run_id: str
) -> dict:
    """DB에서 AuditRule을 가져와 동적 메타데이터 반환"""
    if USE_MOCK_DB:
        facility = "흡연부스"
        raw_weights = {"보행혼잡도": 0.4, "소음민감도": 0.3, "상권활성화": 0.3}
        try:
            with open("dummy_audit.json", "r", encoding="utf-8") as f:
                audit_data = json.load(f)
            facility = audit_data.get("facility_inference", {}).get(
                "facility", "흡연부스"
            )
            parsed_weights = {}
            for result in audit_data.get("results", []):
                for role in result.get("roles", []):
                    if (
                        role.get("role") in ["positive_factor", "negative_factor"]
                        and role.get("weight") is not None
                    ):
                        f_type = (
                            role.get("facility_type") or result.get("summary", "")[:100]
                        )
                        parsed_weights[f_type] = abs(float(role.get("weight")))
            if parsed_weights:
                raw_weights = parsed_weights
        except Exception:
            pass

        jibun = "서울특별시 용산구 (감리 대상 부지)"
        total_w = sum(raw_weights.values())
        ahp_weights = (
            {k: round(v / total_w, 2) for k, v in raw_weights.items()}
            if total_w > 0
            else {}
        )
        return {"facility_type": facility, "jibun": jibun, "ahp_weights": ahp_weights}

    rules = await _select_audit_rules(db, facility_type, domain, run_id)

    # 🔴 2026-08-09 — 여기 두 줄이 원래 이랬다:
    #      facility = [r.facility_type for r in rules ...][0]
    #      factor_name = r.facility_type or "요인"
    #    `facility_type` 하나로 **대상 시설**과 **요인 이름**을 겸했다. 실제 산출물에서
    #    `facility_type` 은 배제 대상(금연구역·어린이집…)이라 첫 값이 "금연구역" 이었고,
    #    가점 요인 8건은 전부 `facility_type` 이 없어 이름이 죄다 `"요인"` 으로 겹쳐
    #    **dict 키 충돌로 1개만 남았다**(8개 → 1개). 안 터지고 값만 사라진다.
    #    이제 컬럼이 분리돼 있다 — target_facility(대상) / factor_name(요인).
    facility = next((r.target_facility for r in rules if r.target_facility), facility_type)

    # 지역도 산출물의 facility_inference.region 을 쓴다. "용산구" 하드코딩이었다(원칙 2).
    region = next((r.region for r in rules if r.region), None)
    jibun = f"{region} (감리 대상 부지)" if region else "감리 대상 부지"

    raw_weights = {}
    for r in rules:
        if (
            r.role_type in ["positive_factor", "negative_factor"]
            and r.weight is not None
        ):
            name = r.factor_name or r.facility_type or f"데이터셋 {r.dataset_id}"
            raw_weights[name] = abs(float(r.weight))

    total_w = sum(raw_weights.values())
    ahp_weights = {}
    if total_w > 0:
        for k, v in raw_weights.items():
            ahp_weights[k] = round(v / total_w, 2)

    return {
        "facility_type": facility,
        "jibun": jibun,
        "ahp_weights": ahp_weights,
    }


async def run_debate_and_publish(
    parcel_id: int,
    facility_type: str,
    redis: aioredis.Redis,
):
    pubsub_manager = RedisPubSubManager(redis)
    async with AsyncSessionLocal() as db:
        try:
            # 🔴 후보점 조회가 **감리 규칙 조회보다 먼저**다(2026-08-10). 감리 규칙을
            #    도메인으로 거르는데, 그 도메인의 출처가 이 행(`booth_candidates.domain`)
            #    이기 때문이다. `/stream` 요청 본문에 도메인을 새로 받지 않는다 —
            #    프런트 계약을 안 바꾸고, 후보점과 감리 근거가 **같은 도메인임을
            #    구조적으로 보장**한다(요청 파라미터로 받으면 어긋날 수 있다).
            #
            # 🔴 여기에 좌표 폴백을 두지 않는다 (2026-08-10, 사람 승인).
            #    예전엔 조회 실패 시 (37.534, 126.994) "용산구 이태원동 123-45 (테스트용)"
            #    로 갈아끼우고 토론을 계속 돌렸다. 5분짜리 LLM 토론이 **다른 위치**로
            #    돌아 DB 에 저장되고, 프런트엔 정상 결과로 보인다 — 안 터지고 값만 틀린다.
            #    게다가 용산은 MVP 도메인 값이라 성동구에서도 용산 좌표가 나온다.
            #    조회가 안 되면 멈춘다. 바깥 except 가 SSE 로 사유를 내보낸다.
            result = await db.execute(
                select(
                    Parcel,
                    func.ST_Y(Parcel.geom).label("lat"),
                    func.ST_X(Parcel.geom).label("lng"),
                ).where(Parcel.id == parcel_id)
            )
            row = result.first()
            if row is None:
                raise CandidateNotFound(
                    f"booth_candidates 에 id={parcel_id} 인 후보점이 없다. "
                    "STEP4 Top-N 을 먼저 적재할 것: "
                    f"python scripts/load_topn_candidates.py <도메인> --yes"
                )

            parcel = row[0]

            # 도메인이 비어 있으면 **어느 도메인의 감리 근거를 쓸지 알 수 없다.**
            # 추측해서 아무 규칙이나 쓰면 5분짜리 토론이 엉뚱한 근거로 완주한다(원칙 1·3).
            # 실제로 `load_topn_candidates.py` 이전에 손으로 넣은 1행이 domain NULL 이다.
            domain = parcel.domain
            if not domain:
                raise CandidateNotFound(
                    f"booth_candidates id={parcel_id} 에 domain 이 없다. "
                    "어느 도메인의 감리 규칙을 쓸지 판단할 수 없다. "
                    "STEP4 Top-N 을 적재기로 다시 넣을 것: "
                    "python scripts/load_topn_candidates.py <도메인> --yes"
                )

            # 🔴 `run_id` 도 **같은 행에서** 꺼낸다 (2026-08-10, 사람 승인).
            #    감리 규칙은 실행마다 다시 적재되므로 도메인만으로는 2회차에
            #    두 실행의 규칙이 섞인다(`_select_audit_rules` 주석).
            #    "어디를 논의할지"와 "무엇을 근거로 논의할지"가 한 행에서 나오면
            #    둘이 어긋날 수가 없다 — domain 을 여기서 꺼내는 이유와 같다.
            run_id = parcel.run_id
            if not run_id:
                raise CandidateNotFound(
                    f"booth_candidates id={parcel_id} 에 run_id 가 없다. "
                    "어느 실행의 감리 규칙을 쓸지 판단할 수 없다. "
                    "STEP4 Top-N 을 적재기로 다시 넣을 것: "
                    "python scripts/load_topn_candidates.py <도메인> --yes"
                )

            audit_context = await _fetch_and_parse_audit_rules_from_db(
                db, facility_type, domain, run_id
            )
            audit_meta = await _extract_dynamic_meta_from_audit_rules(
                db, facility_type, domain, run_id
            )

            # (기존에 DB의 facility_type으로 강제 덮어씌우던 로직 제거: 클라이언트 요청 facility_type 유지)
            # booth_candidates 테이블에는 직접적인 지번 컬럼이 없습니다.
            # 위경도는 ST_Y, ST_X로 추출한 실제 값을 사용합니다.
            gis_data = {
                "lat": row.lat,
                "lng": row.lng,
                "jibun": f"후보지 #{parcel.id} (실제 위치 기반)",
                "intensity_level": "보통",  # Fallback default
                "ahp_weights": audit_meta.get("ahp_weights", {}),
            }

            # 0. DB에서 실시간 공간 쿼리로 POI 문맥 가져오기
            if USE_MOCK_DB:
                try:
                    import os

                    mock_poi_path = os.path.join(
                        "data_ai페르소나_임시", "parcel_context.json"
                    )
                    with open(mock_poi_path, "r", encoding="utf-8") as f:
                        mock_poi_data = json.load(f)
                    poi_lines = mock_poi_data.get(str(parcel_id))
                    if not poi_lines and mock_poi_data:
                        poi_lines = next(iter(mock_poi_data.values()))
                    poi_context = (
                        "\n".join([f"- {msg}" for msg in poi_lines])
                        if poi_lines
                        else ""
                    )
                except Exception as e:
                    print(f"Mock POI Load Error: {e}")
                    poi_context = ""
            else:
                poi_context = await gis_service.get_poi_context_from_db(db, parcel_id)

            if poi_context:
                audit_context += f"\n\n## 📍 주변 인프라 요인 (DB 연산)\n{poi_context}"
                print(
                    f"[GIS] parcel_id={parcel_id}에 POI 문맥 주입 완료:\n{poi_context}"
                )

            # 1. 시스템 시작 메시지 송출
            await pubsub_manager.publish_debate_message(
                parcel_id,
                "시스템",
                f"선택된 위치(지번: {gis_data['jibun']})의 {facility_type} 모의 심의를 시작합니다...",
                is_finished=False,
            )

            # 2. LangGraph 초기화 및 상태 세팅
            graph = build_discussion_graph()

            # 토론 시작 전 공통 RAG(Common RAG) 1회 선검색
            # [A-2] 시설 종류별 맞춤형 검색 키워드 매핑 (범용성 확보)
            facility_keywords = {
                "흡연부스": "금연구역 지정 흡연시설 간접흡연 위치 거리 제한 조건",
                "전기차 충전소": "전기자동차 충전시설 주차장 면적 할당 화재 안전 규제",
                "청년주택": "청년주택 공공임대 용적률 완화 역세권 지원 혜택",
                "소각장": "폐기물 처리시설 환경오염 배출 허용 기준 주민 보상 갈등",
            }

            # 딕셔너리에 시설이 있으면 해당 키워드 사용, 없으면 기본(범용) 키워드 사용
            specific_keywords = facility_keywords.get(
                facility_type, "설치 기준 허가 규제 갈등 중재 혜택 제한 조건"
            )

            # 시설 이름과 맞춤 키워드를 결합하여 최종 쿼리 생성
            query = f"{facility_type} {specific_keywords}"
            try:
                vector_db = get_vector_db()
                retrieved_docs = await vector_db.retrieve_similar_statutes(
                    query, top_k=5, facility_type=facility_type
                )
                # [C-7] 0건(정상)과 검색 장애를 문구로 구분한다.
                # common_rag는 조례데이터 rag
                if not retrieved_docs:
                    common_rag = (
                        "현재 해당 지역에 적용할 수 있는 조례나 법령 정보가 없습니다."
                    )
                    rag_docs_list = []
                else:
                    rag_docs_list = retrieved_docs
                    # [DOC_ID: N] 형식으로 컨텍스트 조립
                    rag_texts = []
                    for d in retrieved_docs:
                        rag_texts.append(f"[DOC_ID: {d['doc_id']}] {d['text']}")
                    common_rag = "\n\n".join(rag_texts)
            except Exception as e:
                print(f"[RAG Error] 조례 검색 실패: {e}")
                common_rag = "조례 검색 중 오류가 발생했습니다."
                rag_docs_list = []

            # ===== [검증용 백엔드 터미널 로그] =====
            print("\n" + "=" * 60)
            print("[AI 토론 엔진 - 데이터 주입 검증 로그]")
            print("-" * 60)
            print("1️⃣ [XGBoost 최종 검색 조례 문서 (Top 5)]:")
            print(common_rag if common_rag else " (검색 결과 없음)")
            print("-" * 60)
            print("2️⃣ [Audit 감리 정제 팩터 (audit_context)]:")
            print(audit_context if audit_context else " (감리 데이터 없음)")
            print("=" * 60 + "\n")

            timestamp = datetime.datetime.now().isoformat()
            import random

            initial_state = {
                "messages": [],
                "css_pro": random.choice(["LOW", "MEDIUM", "HIGH"]),
                "css_con": random.choice(["LOW", "MEDIUM", "HIGH"]),
                "round_count": 0,
                "current_phase": "debate",
                "eval_score": 0.0,
                "spoken_this_round": [],
                "candidate_jibun": gis_data["jibun"],
                "candidate_lat": gis_data["lat"],
                "candidate_lng": gis_data["lng"],
                "facility_type": facility_type,
                "intensity_level": gis_data["intensity_level"],
                "ahp_weights": gis_data["ahp_weights"],
                "timestamp": timestamp,
                "common_rag": common_rag,
                "rag_docs": rag_docs_list,  # XGBoost 학습 피드백을 위한 메타데이터 저장
                "audit_context": audit_context,
                "evaluations": {},
                "final_scenarios": {},
                "is_finished": False,
                "next_speaker": "pro",
            }

            current_state = dict(initial_state)

            # 3. 그래프 비동기 스트리밍 (astream_events)
            async for event in graph.astream_events(
                initial_state, config={"recursion_limit": 50}, version="v2"
            ):
                kind = event["event"]

                # [NEW] 실시간 한 글자(Token) 스트리밍
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"].content
                    if chunk:
                        langgraph_node = event.get("metadata", {}).get(
                            "langgraph_node", "알 수 없음"
                        )
                        if langgraph_node in ["pro", "con", "gov", "gov_wrapup"]:
                            sender_map = {
                                "pro": "찬성",
                                "con": "반대",
                                "gov": "정부",
                                "gov_wrapup": "정부",
                            }
                            sender = sender_map.get(langgraph_node, "참여자")

                            await pubsub_manager.publish_debate_message(
                                parcel_id, sender, chunk, is_finished=False
                            )

                # 노드 작업이 완전히 끝났을 때 상태(State) 누적 및 DB 저장
                elif kind == "on_chain_end":
                    node_name = event["name"]

                    if node_name in [
                        "pro",
                        "con",
                        "gov",
                        "gov_wrapup",
                        "evaluator",
                        "reporter",
                        "supervisor",
                    ]:
                        node_state = event["data"].get("output")
                        if not node_state or not isinstance(node_state, dict):
                            continue

                        # 상태 업데이트 누적
                        if "messages" in node_state:
                            appended_messages = node_state["messages"]
                            current_state["messages"].extend(appended_messages)

                            # evaluator(시스템)의 턴 종료 시 전체 메시지를 한 번에 쏴줌 (evaluator는 스트리밍 안 함)
                            if node_name == "evaluator":
                                if len(appended_messages) > 0:
                                    msg = appended_messages[-1]
                                    parts = msg.split(":", 1)
                                    sender = (
                                        parts[0].strip()
                                        if len(parts) == 2
                                        else "시스템"
                                    )
                                    text = parts[1].strip() if len(parts) == 2 else msg

                                    # Extract metrics from evaluator node_state
                                    metrics = {
                                        "pro_acc": node_state.get(
                                            "evaluations", {}
                                        ).get("pro_acceptance", 0.0),
                                        "con_acc": node_state.get(
                                            "evaluations", {}
                                        ).get("con_acceptance", 0.0),
                                        "css_pro": node_state.get("css_pro", "MEDIUM"),
                                        "css_con": node_state.get("css_con", "MEDIUM"),
                                    }

                                    await pubsub_manager.publish_debate_message(
                                        parcel_id,
                                        sender,
                                        text + "\n\n",
                                        is_finished=False,
                                        metrics=metrics,
                                    )
                            # 페르소나 발언 종료 시 줄바꿈 추가 (선택사항)
                            elif node_name in ["pro", "con", "gov", "gov_wrapup"]:
                                await pubsub_manager.publish_debate_message(
                                    parcel_id, "", "\n\n", is_finished=False
                                )

                        if "final_scenarios" in node_state:
                            current_state["final_scenarios"] = node_state[
                                "final_scenarios"
                            ]

                        # reporter 노드가 끝나면 최종 DB 저장 및 마무리 전송
                        if node_name == "reporter":
                            final_scenarios_obj = current_state.get(
                                "final_scenarios", {}
                            )
                            if (
                                isinstance(final_scenarios_obj, dict)
                                and "scenario" in final_scenarios_obj
                            ):
                                final_scenarios_list = [final_scenarios_obj]
                            else:
                                final_scenarios_list = final_scenarios_obj.get(
                                    "scenarios", []
                                )

                            # CSS 점수 계산 (평가 점수(0.0~1.0)를 0~10점 척도로 환산)
                            avg_acc = current_state.get("eval_score", 0.0)
                            css_score = round(avg_acc * 10, 2)
                            if css_score == 0.0:
                                css_score = 7.5  # 기본값 처리

                            # --- DB 저장용 최종 JSON 포맷 구성 ---
                            debate_logs = []
                            sys_msg = "[시스템 면책 고지] 본 모의 심의 토론 내용은 AI 페르소나 엔진에 의해 생성된 가상의 시나리오이며, 실제 인물이나 단체, 사실관계와는 전혀 무관합니다."
                            debate_logs.append({"sender": "시스템", "text": sys_msg})

                            for msg in current_state.get("messages", []):
                                parts = msg.split(":", 1)
                                if len(parts) == 2:
                                    s, t = parts[0].strip(), parts[1].strip()
                                else:
                                    s, t = "참여자", msg

                                debate_logs.append({"sender": s, "text": t})

                            result_json = {
                                "candidate_jibun": current_state.get("candidate_jibun"),
                                "candidate_lat": current_state.get("candidate_lat"),
                                "candidate_lng": current_state.get("candidate_lng"),
                                "facility_type": current_state.get("facility_type"),
                                "intensity_level": current_state.get("intensity_level"),
                                "ahp_weights": current_state.get("ahp_weights"),
                                "timestamp": current_state.get("timestamp"),
                                "debate_logs": debate_logs,
                                "scenarios": final_scenarios_list,
                                "conflict_sensitivity_score": css_score,
                                "conflict_factors": current_state.get(
                                    "ahp_weights", {}
                                ),
                            }

                            # 최종 JSON을 DB에 저장 (ConflictSimulation)
                            if USE_MOCK_DB:
                                print("=== [MOCK 모드] DB 저장 우회 완료 ===")
                            else:
                                try:
                                    sim_id = await _persist_simulation(
                                        db=db,
                                        parcel_id=parcel_id,
                                        facility_type=facility_type,
                                        result_json=result_json,
                                        css_score=css_score,
                                        scenarios=final_scenarios_list,
                                        debate_logs=debate_logs,
                                    )
                                    logger.info(
                                        "[simulations] DB 저장 성공 "
                                        f"simulation_id={sim_id} parcel_id={parcel_id} "
                                        f"debate_logs={len(debate_logs)}행"
                                    )
                                except Exception as e:
                                    await db.rollback()
                                    # 저장에 실패해도 토론 결과 자체는 Redis 로 나간다.
                                    # 여기서 raise 하면 5분짜리 토론 결과가 통째로 날아간다.
                                    # 대신 **반드시 보이게** 남긴다 — 예전엔 `print` 라
                                    # 백그라운드 태스크 stdout 에 묻혀 아무 데도 안 남았다(원칙 1·4).
                                    logger.error(
                                        "[simulations] conflict_simulations 저장 실패 "
                                        f"(parcel_id={parcel_id}): {e}",
                                        exc_info=True,
                                    )

                            # Redis에도 최종 JSON 데이터 10분(600초) 임시 저장 (캐싱 및 GUI 검증용)
                            try:
                                cache_key = f"simulation:result:{parcel_id}"
                                await redis.setex(
                                    cache_key,
                                    600,
                                    json.dumps(result_json, ensure_ascii=False),
                                )
                                print(
                                    f"=== Redis 캐시 저장 성공 (Key: {cache_key}) ==="
                                )
                            except Exception as cache_err:
                                print(f"=== Redis 캐시 저장 실패: {cache_err} ===")

                            # --- 최종 출력용 평문(Text) 포맷 구성 ---
                            conflict_factors = current_state.get("ahp_weights", {})
                            factors_list = []
                            for k, v in conflict_factors.items():
                                if isinstance(v, (int, float)):
                                    factors_list.append(f"  • {k}: {v * 100:.1f}%")
                                else:
                                    factors_list.append(f"  • {k}: {v}")
                            factors_str = (
                                "\n".join(factors_list)
                                if factors_list
                                else "  • 주요 갈등 인자 정보 없음"
                            )

                            scenario_type = final_scenarios_obj.get(
                                "scenario", "알 수 없음"
                            )
                            title = final_scenarios_obj.get(
                                "scenario_description"
                            ) or final_scenarios_obj.get("title", "설명 없음")
                            acc_score = final_scenarios_obj.get(
                                "final_acceptance_score", 0.0
                            )
                            try:
                                acc_val = float(acc_score)
                                acc_str = (
                                    f"{acc_val * 100:.1f}%"
                                    if acc_val <= 1.0
                                    else f"{acc_val}%"
                                )
                            except Exception:
                                acc_str = str(acc_score)

                            summary = final_scenarios_obj.get("summary", "")
                            reason = final_scenarios_obj.get("reason", "")
                            risk_index = final_scenarios_obj.get(
                                "conflict_risk_index", 0.0
                            )
                            risk_reason = final_scenarios_obj.get("risk_reason", "")

                            final_text = (
                                f"🎉 모의 심의 토론이 최종 종료되었습니다.\n\n"
                                f"📌 [최종 시나리오 결과: {scenario_type} - {title}]\n"
                                f"• 갈등 민감도 지수(CSS): {css_score} / 10.0\n"
                                f"• 최종 수용도 점수: {acc_str}\n"
                                f"• 갈등 위험 지수: {risk_index}점 ({risk_reason})\n\n"
                                f"📊 [주요 갈등 인자 및 가중치 (Conflict Factors)]\n"
                                f"{factors_str}\n\n"
                                f"📝 [시나리오 요약]\n"
                                f"{summary}\n\n"
                                f"💡 [도출 사유]\n"
                                f"{reason}\n\n"
                            )

                            await pubsub_manager.publish_debate_message(
                                parcel_id,
                                "시스템",
                                final_text,
                                is_finished=True,
                            )

        except Exception as quota_err:
            err_msg = str(quota_err)
            is_quota = "insufficient_quota" in err_msg or "429" in err_msg
            if isinstance(quota_err, CandidateNotFound):
                # AI 엔진 탓으로 뭉뚱그리면 후보점 적재 문제를 프런트가 못 알아본다.
                error_code = "CANDIDATE_NOT_FOUND"
                message = err_msg
            elif is_quota:
                error_code = "OPENAI_QUOTA_EXCEEDED"
                message = "OpenAI API Quota가 초과되었습니다. API 키 잔액을 충전하고 다시 시도해 주세요."
            else:
                error_code = "AI_ENGINE_ERROR"
                message = f"AI 토론 엔진 오류가 발생했습니다: {err_msg}"
            logger.error("[Stream Error] %s: %s", error_code, err_msg, exc_info=True)

            # 에러 메시지 발행 및 스트림 강제 종료
            await redis.publish(
                f"debate:{parcel_id}",
                json.dumps(
                    {
                        "error_code": error_code,
                        "message": message,
                        "is_finished": True,
                    },
                    ensure_ascii=False,
                ),
            )


@router.get("/candidates")
async def list_booth_candidates(
    domain: str,
    run_id: str | None = None,
    limit: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """STEP4 Top-N 후보점 목록. **화면4 에서 사람이 위치를 고르는 입구**다.

    지금까지 프런트는 `parcel_id` 를 어디선가 들고 와야 했는데 그 출처가 없었다
    (`booth_candidates` 에 손으로 넣은 1행뿐이라 늘 같은 점이 나왔다).
    이제 STEP4 산출물이 적재되므로 **여기서 골라서** `/stream` 에 넘긴다.

    - 정렬은 `rank` 오름차순(1 = 최상위). `rank` 가 NULL 인 행은 뒤로 보낸다.
    - 🔴 **`rank == 1` 은 추천이지 강제가 아니다**(2026-08-10, 사람 결정).
      토론(화면5)은 **사람이 고른 후보**로 돈다 — 프런트는 목록을 보여주고
      선택된 원소의 `parcel_id` 를 `/stream` 에 넘긴다. 첫 원소를 자동으로
      쓰던 예전 문구는 여기서 폐기한다.
      `/stream` 은 예나 지금이나 임의의 `parcel_id` 를 받는다 — 바뀐 건 계약이지
      구현이 아니다(원칙 5: 코드를 확인하고 적는다).
    - 🔴 `순위`는 **점수 내림차순이 아니다**(MCLP 커버 기여 그리디). 흡연 실측에서
      4위 0.7793 > 1위 0.7703 이다. 화면에서 점수로 재정렬하면 순위가 뒤집힌다.
    - `domain` 은 **필수**다. 기본값을 두면 성동구 화면이 흡연 후보를 받는다.
    - 🔴 `run_id` 를 안 주면 **가장 최근에 적재된 run 하나**만 돌려준다. 적재기는
      같은 `(domain, run_id)` 만 교체하므로 도메인으로만 거르면 실행 두 번의 행이
      **섞여서** 나오고 `rank` 가 1,2,3…,1,2,3… 이 된다 — 사람이 고르는 목록에서
      순위가 두 번 나오면 무엇을 고른 건지 알 수 없다.
      "최근"은 `run_id` 문자열 크기가 아니라 **가장 큰 `id`(마지막 삽입)** 로 정한다.
      정본 산출물의 run_id 는 `"정본"` 이라 `r_2026…` 과 사전순 비교가 무의미하다.
    - 🔴 여기서 돌려준 `run_id` 는 화면5 의 **감리 근거를 고르는 열쇠**이기도 하다.
      `/stream` 이 `parcel_id` 로 이 행을 다시 읽어 `(domain, run_id)` 로
      `audit_rules` 를 좁힌다. 목록과 근거가 같은 행에서 나온다.
    - `limit` 은 **기본이 없다(전량)**. 예전 기본값 20 은 STEP4 의 `--topn` 기본값과
      우연히 같았을 뿐이라, `topn=30` 으로 돌리면 10개가 **말없이 잘렸다**.
      N 을 정하는 건 STEP4 의 `--topn` 이고 여기는 세는 곳이 아니다.
    """
    if limit is not None and limit < 1:
        raise HTTPException(status_code=400, detail="limit 은 1 이상이어야 한다")

    if run_id is None:
        run_id = await db.scalar(
            select(Parcel.run_id)
            .where(Parcel.domain == domain)
            .order_by(Parcel.id.desc())
            .limit(1)
        )

    stmt = (
        select(
            Parcel,
            func.ST_Y(Parcel.geom).label("lat"),
            func.ST_X(Parcel.geom).label("lng"),
        )
        .where(Parcel.domain == domain, Parcel.run_id == run_id)
        .order_by(Parcel.rank.asc().nullslast())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    rows = result.all()
    if not rows:
        # 빈 배열을 돌려주면 "후보가 없는 도메인" 과 "적재를 안 했다" 가 구분이 안 된다.
        raise HTTPException(
            status_code=404,
            detail=(
                f"domain='{domain}' run_id={run_id!r} 의 후보점이 "
                f"booth_candidates 에 없다. "
                f"적재: python scripts/load_topn_candidates.py {domain} --yes"
            ),
        )

    return {
        "domain": domain,
        "run_id": run_id,
        "count": len(rows),
        "candidates": [
            {
                "parcel_id": p.id,  # ← /stream 에 넘길 값
                "rank": p.rank,
                "score": p.score,
                "pnu": p.pnu,
                "jibun": p.jibun,
                "facility_type": p.facility_type,
                "run_id": p.run_id,
                "land_id": p.land_id,
                "lat": lat,
                "lng": lng,
            }
            for p, lat, lng in rows
        ],
    }


@router.post("/stream", dependencies=[Depends(rate_limiter)])
async def stream_ai_discussion(
    request: StreamRequest, redis: aioredis.Redis = Depends(get_redis)
):
    parcel_id = request.parcel_id
    facility_type = request.facility_type

    # 1. 백그라운드 태스크로 모의 심의 테스트 실행 (비동기로 루프를 돌며 Redis에 Publish)
    asyncio.create_task(
        run_debate_and_publish(
            parcel_id=parcel_id,
            facility_type=facility_type,
            redis=redis,
        )
    )

    # 2. SSE 클라이언트는 동일 채널을 Subscribe하여 실시간 청크 응답
    pubsub_manager = RedisPubSubManager(redis)

    async def event_generator():
        async for data in pubsub_manager.subscribe_debate_stream(parcel_id):
            yield {"event": "message", "data": json.dumps(data, ensure_ascii=False)}

    # sse_starlette 라이브러리의 EventSourceResponse를 반환하여 비동기 HTTP 청크 전송 스트림 활성화
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Credentials": "true",
    }
    return EventSourceResponse(event_generator(), headers=headers)


@router.get("/results/{parcel_id}", response_model=SimulationResultResponse)
async def get_simulation_results(parcel_id: int, db: AsyncSession = Depends(get_db)):
    """
    [동현 AI 메인 & 장천명 풀스택] 모의 심의 토론 종결 후 최종 도출된 3대 시나리오 예측치 조회 API
    - 시점: 프론트엔드가 /stream SSE 커넥션을 닫은 직후, 최종 통계 데이터를 단독 로드하기 위해 호출합니다.
    - 구현: 실제 데이터베이스(conflict_simulations 테이블) 조회 결과에 따라 최신 이력을 동적으로 로드합니다.
    """
    # DB에서 가장 최신의 시뮬레이션 결과를 쿼리합니다.
    result = await db.execute(
        select(ConflictSimulation)
        .where(ConflictSimulation.parcel_id == parcel_id)
        .order_by(ConflictSimulation.id.desc())
    )
    # 🔴 `scalar_first()` 는 SQLAlchemy 에 없는 메서드다(2026-08-09 수정).
    #    호출되는 순간 AttributeError → 500. `scalars().first()` 가 맞다.
    sim_data = result.scalars().first()

    # DB에 적재된 이력이 없을 경우 404 예외 처리
    if not sim_data:
        # DB에 테스트 시뮬레이션 데이터를 조장 단독 시나리오 검증용으로 자동 폴백 처리하거나 404 리턴
        # 프론트 E2E 정합을 위해 404 대신 디버그용 폴백 데이터를 제공할 수 있으나, 정석대로 예외를 던집니다.
        raise HTTPException(
            status_code=404,
            detail=f"필지 ID {parcel_id}에 대한 기존 모의 심의 시뮬레이션 이력이 존재하지 않습니다. 먼저 토론 스트리밍을 가동해 주세요.",
        )

    res_json = sim_data.result_json or {}

    # result_json 내에 scenarios 배열이 정상 이식되어 있으면 파싱, 없으면 합리적 시나리오 폴백 매핑
    raw_scenarios = res_json.get("scenarios", [])

    if raw_scenarios and isinstance(raw_scenarios, list) and len(raw_scenarios) > 0:
        # 단일 시나리오 스키마에 맞게 첫 번째 시나리오만 가져옵니다.
        sc_data = raw_scenarios[0]

        # Pydantic 모델(ScenarioDetail)이 요구하는 키와 타입에 맞춰 안전하게 변환
        scenario_obj = {
            "scenario": str(
                sc_data.get("scenario") or sc_data.get("scenario_type") or "알 수 없음"
            ),
            "scenario_description": str(
                sc_data.get("scenario_description")
                or sc_data.get("title")
                or "설명 없음"
            ),
            "final_acceptance_score": float(
                sc_data.get("final_acceptance_score") or 0.0
            ),
            "reason": str(sc_data.get("reason") or "이유 없음"),
            "summary": str(sc_data.get("summary") or "요약 없음"),
            "conflict_risk_index": float(sc_data.get("conflict_risk_index") or 0.0),
            "risk_reason": str(sc_data.get("risk_reason") or "갈등 위험 이유 없음"),
        }
    elif isinstance(raw_scenarios, dict) and "scenario" in raw_scenarios:
        scenario_obj = raw_scenarios
    else:
        # 시나리오 배열이 비어있는 경우: AI 토론이 완료되지 않았거나 OpenAI API Quota 초과로 인해
        # 결과가 DB에 정상 적재되지 않은 상태입니다.
        raise HTTPException(
            status_code=503,
            detail=(
                "[OPENAI_QUOTA_EXCEEDED] AI 모의 심의 토론 결과 시나리오가 존재하지 않습니다. "
                "OpenAI API Quota가 초과되었거나 토론이 정상 완료되지 않았습니다. "
                "API 키 잔액을 확인하고 토론을 다시 시작해 주세요."
            ),
        )

    # 갈등 민감도 점수 (CSS) 및 인자 도출 — DB에 저장된 실제 값만 사용
    css_score = res_json.get("conflict_sensitivity_score")
    conflict_factors = res_json.get("conflict_factors")

    if css_score is None or conflict_factors is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "[OPENAI_QUOTA_EXCEEDED] 갈등 민감도 지수(CSS) 데이터가 존재하지 않습니다. "
                "OpenAI API Quota가 초과되어 AI 분석이 완료되지 않았습니다."
            ),
        )

    css_score = float(css_score)

    # 대화 내역 추출
    debate_logs = res_json.get("debate_logs", [])

    return {
        "parcel_id": parcel_id,
        "conflict_sensitivity_score": css_score,
        "conflict_factors": conflict_factors,
        "scenario": scenario_obj,
        "debate_logs": debate_logs,
    }


@router.get("/results/{parcel_id}/pdf")
@router.get("/report/{parcel_id}")
async def download_feasibility_report_pdf(
    parcel_id: int, db: AsyncSession = Depends(get_db)
):
    """
    [장천명 풀스택] Step 5 최종 입지 선정 타당성 보고서 PDF 실시간 다운로드 API
    - DB에 저장된 최종 시뮬레이션 갈등 시나리오 정보를 WeasyPrint를 통해 PDF로 컴파일하여 내보냅니다.
    """
    # 1. DB에서 가장 최신의 시뮬레이션 결과 획득
    result = await db.execute(
        select(ConflictSimulation)
        .where(ConflictSimulation.parcel_id == parcel_id)
        .order_by(ConflictSimulation.id.desc())
    )
    # 🔴 `scalar_first()` → `scalars().first()` (2026-08-09 수정). 위 :649 와 같은 건.
    sim_data = result.scalars().first()

    if not sim_data:
        raise HTTPException(
            status_code=404,
            detail="해당 필지의 심의 시뮬레이션 이력이 존재하지 않아 보고서를 출력할 수 없습니다.",
        )

    res_json = sim_data.result_json or {}
    if not res_json:
        raise HTTPException(
            status_code=404,
            detail="[SIMULATION_NOT_FOUND] 시뮬레이션 결과 데이터가 존재하지 않습니다.",
        )

    candidate_lat = res_json.get("candidate_lat")
    candidate_lng = res_json.get("candidate_lng")
    if (
        candidate_lat is None
        or candidate_lng is None
        or (candidate_lat == 0.0 and candidate_lng == 0.0)
    ):
        raise HTTPException(
            status_code=422,
            detail="[GEOCODING_FAILED] 시뮬레이션 대상의 유효한 위경도 좌표가 존재하지 않습니다.",
        )

    css_score = res_json.get("conflict_sensitivity_score")
    if css_score is None:
        raise HTTPException(
            status_code=503,
            detail="[AI_SCORE_UNAVAILABLE] 갈등 민감도 지수(CSS) 연산에 실패했거나 아직 완료되지 않았습니다.",
        )

    # 2. PDF 조립용 컨텍스트 정보 포맷팅
    # 시나리오 추출 로직 (DB에 저장된 scenarios 배열에서 첫 번째 항목 가져오기)
    raw_scenarios = res_json.get("scenarios", [])
    scenario_obj = {}
    if raw_scenarios and isinstance(raw_scenarios, list) and len(raw_scenarios) > 0:
        scenario_obj = raw_scenarios[0]
    elif isinstance(raw_scenarios, dict) and "scenario" in raw_scenarios:
        scenario_obj = raw_scenarios

    report_data = {
        "candidate_jibun": res_json.get("candidate_jibun", "알 수 없음"),
        "candidate_lat": candidate_lat,
        "candidate_lng": candidate_lng,
        "facility_type": res_json.get("facility_type", "지정되지 않음"),
        "conflict_sensitivity_score": css_score,
        "ahp_weights": res_json.get("ahp_weights", {}),
        "scenario": scenario_obj,
        "debate_logs": res_json.get("debate_logs", []),
    }

    # 3. PDF 빌더 기동 및 스트리밍 파일 전송
    #
    # 🔴 지역 import 다 — 최상단이 아니라 여기서 부른다 (2026-08-04).
    #    이 한 줄 때문에 모듈 전체가 import 불가였고, 그래서 화면5(토론)까지 못 떴다.
    #    화면6 만 이걸 쓴다. 모듈 전체가 한 기능의 의존성에 인질로 잡히면 안 된다.
    #
    # 🔴 아래 옛 경고("동작하지 않는다 — pdf_service·템플릿 삭제됨 + weasyprint 필요")는
    #    **틀렸다. 지금은 동작한다**(2026-08-09 실측 정정).
    #      (1) app/services/pdf_service.py        — **있다**
    #      (2) app/templates/report_template.html — **있다**
    #      (3) weasyprint                          — **안 쓴다.** pdf_service 는
    #          playwright(chromium headless)로 렌더한다. weasyprint 는 코드 참조 0회다.
    #    실제 생성 확인: 33,335 bytes, 헤더 `%PDF-`.
    #    이 주석은 2026-08-04 에 두 파일이 잠깐 지워졌던 시점을 기준으로 적었고,
    #    복구된 뒤에도 갱신하지 않았다. 그 사이 프런트가 이 주석을 근거로
    #    "weasyprint GTK 미설치로 화면6 막힘"을 요구사항에 올렸다 — **안 고친 주석은
    #    남의 계획이 된다**(원칙 4). 남은 전제는 playwright 브라우저 설치뿐이고,
    #    실패하면 아래 except 가 사유를 그대로 실어 보낸다(원칙 1).
    try:
        from app.services.pdf_service import pdf_builder

        pdf_file = await pdf_builder.generate_feasibility_pdf(report_data)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"PDF 생성 중 오류가 발생했습니다. 서버 환경(Playwright 설치)을 확인해 주세요. 오류: {str(e)}",
        )

    filename = f"OmniSite_Feasibility_Report_{parcel_id}.pdf"
    return StreamingResponse(
        pdf_file,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
