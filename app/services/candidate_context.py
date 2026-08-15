# -*- coding: utf-8 -*-
"""화면4 에서 고른 후보점(`parcel_id`) 하나로 **토론 입력 일습**을 조달한다.

왜 서비스로 뺐나 — 화면5 토론 엔진이 **둘**이기 때문이다.
  · A 대립 토론  `app/core/sim_ai/`        · `/simulation(s)/stream`
  · B 다인 토론  `app/core/stakeholder_mode/` · `/stakeholders/*`

둘의 입력은 **같아야 한다.** 같은 후보지를 두고 A 는 감리 규칙 13행과 조례 5조문으로,
B 는 프런트가 조립해 보낸 다른 값으로 토론하면 두 결과를 나란히 놓고 비교할 수 없다.
그래서 조달을 한 곳에 두고 양쪽이 같은 함수를 부른다.

🔴 여기 있는 함수 대부분은 **`app/api/v1/simulations.py` 에서 옮겨온 것**이다.
   사본이 아니라 이동이다 — 같은 파일명·같은 함수명을 두 곳에 두면 import 는 멀쩡하고
   값만 다르게 나온다(저장소가 실제로 겪은 `app/services/dummy/` 사고).
   이름은 일부러 그대로 뒀다(`_select_audit_rules` 등). CLAUDE.md·계약서가 이 이름으로
   함정을 설명하고 있어서, 옮기면서 이름까지 바꾸면 그 문서들이 가리키는 곳이 사라진다.
"""

import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sim_ai.vector_db import get_vector_db
from app.db.models.audit import AuditRule
from app.db.models.simulation import Parcel
from app.services.poi_context import build_poi_context

logger = logging.getLogger("uvicorn.error")

class CandidateNotFound(Exception):
    """`parcel_id` 로 booth_candidates 를 못 찾았다. 좌표를 지어내지 않고 멈추기 위한 예외."""


# 조례 검색 질의에 붙이는 **도메인 무관** 어휘. 시설명이 아니라 "규제 문서의 말투"다.
#
# 🔴 2026-08-11. 여기 예전엔 `{"흡연부스": "금연구역 지정 흡연시설 …",
#    "전기차 충전소": …, "청년주택": …, "소각장": …}` 사전이 있었다 — 도메인 값
#    하드코딩(원칙 2)이고, 사전에 없는 시설은 전부 범용어로 떨어져 **검색 품질이
#    시설별로 조용히 갈렸다.** 지금은 시설 고유 어휘를 사전이 아니라
#    **그 실행의 감리 산출물**(`audit_rules` 의 배제 대상 시설명)에서 가져온다.
#    흡연 도메인 실측으로 그 값은 `금연구역·어린이집·지하철역·버스정류소·
#    어린이보호구역` 이다 — 지우는 사전이 손으로 적고 있던 바로 그 낱말들이다.
_ORDINANCE_QUERY_TERMS = "설치 기준 허가 규제 갈등 중재 혜택 제한 조건"


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
       행에서 온다(`resolve_candidate` 가 `parcel_id` 로 읽는 그 행). 같은 행에서
       뽑으면 "논의 대상"과 "논의 근거"가 어긋날 수가 없다. 파라미터로 받으면
       A run 의 후보점에 B run 의 감리 근거를 넘길 수 있다.

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
        await db.execute(
            select(
                AuditRule.domain, AuditRule.run_id, AuditRule.target_facility
            ).distinct()
        )
    ).all()
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
    rules = await _select_audit_rules(db, facility_type, domain, run_id)

    # 🔴 2026-08-09 — 여기 두 줄이 원래 이랬다:
    #      facility = [r.facility_type for r in rules ...][0]
    #      factor_name = r.facility_type or "요인"
    #    `facility_type` 하나로 **대상 시설**과 **요인 이름**을 겸했다. 실제 산출물에서
    #    `facility_type` 은 배제 대상(금연구역·어린이집…)이라 첫 값이 "금연구역" 이었고,
    #    가점 요인 8건은 전부 `facility_type` 이 없어 이름이 죄다 `"요인"` 으로 겹쳐
    #    **dict 키 충돌로 1개만 남았다**(8개 → 1개). 안 터지고 값만 사라진다.
    #    이제 컬럼이 분리돼 있다 — target_facility(대상) / factor_name(요인).
    facility = next(
        (r.target_facility for r in rules if r.target_facility), facility_type
    )

    # 지역도 산출물의 facility_inference.region 을 쓴다. "용산구" 하드코딩이었다(원칙 2).
    # ⚠ `region` 을 **따로 돌려준다**. 예전엔 `jibun` 문자열에서 접미사를 잘라내
    #    되찾아 썼는데(`stakeholders.py`), 그러면 접미사 문구를 바꾸는 순간
    #    말없이 지역명이 통째로 틀어진다.
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

    # 조례 검색에 쓸 **이 도메인의 시설 어휘**. `hard_exclusion` 의 대상 시설명이다
    # (흡연 실측: 금연구역·어린이집·지하철역·버스정류소·어린이보호구역).
    # 예전엔 이 낱말들을 `_FACILITY_KEYWORDS` 에 손으로 적어뒀다 — 산출물에 이미
    # 있는 값을 코드가 다시 적고 있었던 셈이고, 도메인이 늘면 거기부터 틀린다.
    exclusion_targets = list(
        dict.fromkeys(
            r.facility_type
            for r in rules
            if r.role_type == "hard_exclusion" and r.facility_type
        )
    )

    return {
        "facility_type": facility,
        "region": region,
        "jibun": jibun,
        "ahp_weights": ahp_weights,
        "exclusion_targets": exclusion_targets,
    }


def site_jibun(parcel, region: str | None) -> str:
    """토론 프롬프트·PDF·HWPX 에 나가는 **후보지 지번 표기**. A·B 공용.

    🔴 2026-08-11 까지는 양쪽 다 `f"후보지 #{id} (실제 위치 기반)"` 였다. 그런데
       `booth_candidates.jibun` 은 2026-08-10 적재기부터 **실측 20/20 채워져 있다** —
       실제 지번을 쓰려고 넣어둔 컬럼을 아무도 안 읽고 있었다(사람 지시로 배선).

    `jibun` 은 지적도 원문 그대로다: `"422 대"` 처럼 **지번 뒤에 지목이 붙어 있고
    동 이름이 없다.** 쪼개지 않는다 — 쪼개면 없는 정보를 지어낸다. 앞에는 감리
    산출물의 `region`(`"서울특별시 용산구"`)만 붙인다. **법정동 이름은 조달할 수
    없다**: `props_json.법정동코드`(10자리)는 `dong_boundaries` 의 행정동
    코드(8자리)와 **다른 축**이라 크로스워크에 없는 게 정상이다(CLAUDE.md 행정코드).

    지번이 비면 지어내지 않고 **없다고 적는다**(원칙 4). `jibun` 은 nullable 이고
    (`JIBUN` 없는 geojson 을 적재하면 NULL 이다) 그렇다고 토론을 막을 값은 아니다 —
    대신 PNU 로 어느 필지인지는 남긴다.
    """
    raw = (parcel.jibun or "").strip()
    if not raw:
        pnu = (parcel.pnu or "").strip()
        return f"지번 미상 (PNU {pnu})" if pnu else f"지번 미상 (후보지 #{parcel.id})"
    region = (region or "").strip()
    return f"{region} {raw}" if region else raw


async def resolve_candidate(db: AsyncSession, parcel_id: int) -> dict:
    """`parcel_id` → 후보점 행 + 좌표. **여기서 좌표를 지어내지 않는다.**

    🔴 예전엔 조회 실패 시 (37.534, 126.994) "용산구 이태원동 123-45 (테스트용)" 로
       갈아끼우고 토론을 계속 돌렸다. 5분짜리 LLM 토론이 **다른 위치**로 돌아 DB 에
       저장되고, 프런트엔 정상 결과로 보인다 — 안 터지고 값만 틀린다.
       게다가 용산은 MVP 도메인 값이라 성동구에서도 용산 좌표가 나온다.

    `domain`·`run_id` 가 없으면 **어느 도메인·어느 실행의 감리 근거를 쓸지 알 수 없다.**
    추측해서 아무 규칙이나 쓰면 5분짜리 토론이 엉뚱한 근거로 완주한다(원칙 1·3).
    """
    row = (
        await db.execute(
            select(
                Parcel,
                func.ST_Y(Parcel.geom).label("lat"),
                func.ST_X(Parcel.geom).label("lng"),
            ).where(Parcel.id == parcel_id)
        )
    ).first()
    if row is None:
        raise CandidateNotFound(
            f"booth_candidates 에 id={parcel_id} 인 후보점이 없다. "
            "STEP4 Top-N 을 먼저 적재할 것: "
            "python scripts/load_topn_candidates.py <도메인> --yes"
        )

    parcel = row[0]
    # 실제로 `load_topn_candidates.py` 이전에 손으로 넣은 1행이 domain NULL 이었다.
    if not parcel.domain:
        raise CandidateNotFound(
            f"booth_candidates id={parcel_id} 에 domain 이 없다. "
            "어느 도메인의 감리 규칙을 쓸지 판단할 수 없다. "
            "STEP4 Top-N 을 적재기로 다시 넣을 것: "
            "python scripts/load_topn_candidates.py <도메인> --yes"
        )
    if not parcel.run_id:
        raise CandidateNotFound(
            f"booth_candidates id={parcel_id} 에 run_id 가 없다. "
            "어느 실행의 감리 규칙을 쓸지 판단할 수 없다. "
            "STEP4 Top-N 을 적재기로 다시 넣을 것: "
            "python scripts/load_topn_candidates.py <도메인> --yes"
        )

    return {
        "parcel": parcel,
        "lat": row.lat,
        "lng": row.lng,
        "domain": parcel.domain,
        "run_id": parcel.run_id,
    }


async def retrieve_ordinance_texts(
    facility_type: str, top_k: int = 5, terms: list[str] | None = None
) -> tuple[str, list[dict]]:
    """조례 벡터검색. `(프롬프트용 통짜 문자열, 문서 리스트)` 를 돌려준다.

    🔴 `facility_type` 필터는 **반드시** 넘긴다. 이 인자를 받고도 안 쓰던 시절이
       있었고, 그때 흡연부스 토론 상위 15건 중 4건이 전기차충전소 조례였다.

    `terms` 는 질의를 넓히는 **도메인 어휘**다. 호출자가 감리 산출물에서 뽑아
    넘긴다(`audit_meta["exclusion_targets"]`). 안 넘기면 시설명 + 범용어만 쓴다 —
    검색이 좁아질 뿐 틀린 도메인이 섞이지는 않는다(필터가 따로 걸려 있다).
    """
    query = " ".join([facility_type, *(terms or []), _ORDINANCE_QUERY_TERMS])
    try:
        vector_db = get_vector_db()
        retrieved_docs = await vector_db.retrieve_similar_statutes(
            query, top_k=top_k, facility_type=facility_type
        )
        # [C-7] 0건(정상)과 검색 장애를 문구로 구분한다.
        if not retrieved_docs:
            return "현재 해당 지역에 적용할 수 있는 조례나 법령 정보가 없습니다.", []
        # [DOC_ID: N] 형식으로 컨텍스트 조립 — 토론자가 인용할 때 쓰는 식별자다.
        texts = [f"[DOC_ID: {d['doc_id']}] {d['text']}" for d in retrieved_docs]
        return "\n\n".join(texts), list(retrieved_docs)
    except Exception as e:
        # ⚠ 여기서 삼키는 건 **검색 장애**뿐이고 문구로 드러난다(0건과 구분됨).
        #   옮겨오면서 동작을 바꾸지 않았다.
        print(f"[RAG Error] 조례 검색 실패: {e}")
        return "조례 검색 중 오류가 발생했습니다.", []


async def build_site_context(
    db: AsyncSession, parcel_id: int, facility_type: str | None = None
) -> dict:
    """후보점 하나로 **토론 입력 일습**을 조달한다. A·B 공용.

    프런트가 `gis_data`·`ordinance_contexts` 를 조립해 보내면 안 되는 이유:
      · 감리 근거는 `(domain, run_id, target_facility)` 로 좁혀야 하는데 그 셋의 출처가
        `booth_candidates` 행이다. 요청으로 받으면 후보지와 근거가 어긋날 수 있다.
      · 조례 검색에는 `facility_type` 필터가 걸려야 한다(안 걸면 흡연부스 토론에
        전기차충전소 조례가 섞인다 — 실제로 겪었다).
      · A 와 B 가 다른 근거로 토론하면 두 결과를 나란히 놓고 비교할 수 없다.

    `facility_type` 을 안 주면 `booth_candidates.facility_type` 을 쓴다.
    그것도 없으면 **추측하지 않고 멈춘다**(원칙 1).
    """
    resolved = await resolve_candidate(db, parcel_id)
    parcel = resolved["parcel"]

    facility_type = facility_type or parcel.facility_type
    if not facility_type:
        raise CandidateNotFound(
            f"booth_candidates id={parcel_id} 에 facility_type 이 없고 요청에도 없다. "
            "어떤 시설을 두고 토론할지 판단할 수 없다."
        )

    audit_meta = await _extract_dynamic_meta_from_audit_rules(
        db, facility_type, resolved["domain"], resolved["run_id"]
    )
    audit_context = await _fetch_and_parse_audit_rules_from_db(
        db, facility_type, resolved["domain"], resolved["run_id"]
    )

    # `jibun` 조립은 `site_jibun` **한 곳**에 있다 — A(`simulations.py`)도 같은 함수를
    # 쓴다. 두 엔진이 다른 표기를 쓰면 두 결과를 나란히 놓고 비교할 수 없다.
    gis_data = {
        "lat": resolved["lat"],
        "lng": resolved["lng"],
        "jibun": site_jibun(parcel, audit_meta.get("region")),
        # ⚠ 측정값이 아니다. 산출 근거가 없어 고정값을 쓰고 있고,
        #   그 사실을 `result_json["determinism"]` 에 남긴다(원칙 4).
        "intensity_level": "보통",
        "ahp_weights": audit_meta.get("ahp_weights", {}),
    }

    # 🔴 그 후보점의 **run 이 낸 STEP2 산출물**에서 센다. 예전엔 별도 적재 테이블 6개를
    #    봤는데, 그 테이블엔 `domain`·`run_id` 가 없어 **어떤 run 을 물어도 같은 답**이
    #    나왔다(사유는 `poi_context.py` 모듈 주석). `resolved` 를 그대로 넘긴다 —
    #    같은 행에서 뽑아야 문맥과 근거가 어긋나지 않는다.
    poi = await build_poi_context(resolved)
    poi_context = poi["text"]
    common_rag, rag_docs = await retrieve_ordinance_texts(
        facility_type, terms=audit_meta.get("exclusion_targets")
    )

    return {
        "parcel_id": parcel.id,
        "parcel": parcel,
        "domain": resolved["domain"],
        "run_id": resolved["run_id"],
        "facility_type": facility_type,
        "rank": parcel.rank,
        "gis_data": gis_data,
        "audit_context": audit_context,
        "audit_meta": audit_meta,
        "poi_context": poi_context,
        "common_rag": common_rag,
        "rag_docs": rag_docs,
        # B(`/stakeholders/*`)가 그대로 쓰는 형태. A 의 `common_rag` 와 같은 원본이다.
        "ordinance_contexts": [f"[DOC_ID: {d['doc_id']}] {d['text']}" for d in rag_docs],
    }


def basis_snapshot(
    *,
    domain: str | None,
    run_id: str | None,
    facility_type: str | None,
    audit_context: str | None,
    poi_context: str | None,
    rag_docs: list[dict] | None,
    audit_meta: dict | None,
) -> dict:
    """이 토론이 **실제로 무엇을 근거로 했는지**를 결과에 박아두는 스냅샷. A·B 공용.

    왜 필요한가 — 토론 결과에는 지금까지 **결론만** 남고 근거는 안 남았다. 근거는
    전부 「지금 DB·벡터스토어를 다시 조회하면 나온다」에 기대고 있는데, 그 전제가
    실제로 깨진다:

      · `load_audit_data.py` 는 같은 `(domain, run_id)` 의 `audit_rules` 를 **교체**한다.
        `hearing_result_a` 와는 FK 가 없으므로 **토론은 남고 근거만 바뀐다.**
        (`booth_candidates` 쪽은 CASCADE 라 토론도 같이 지워져 이 문제가 안 난다 —
         한쪽만 짝이 맞아 있었다.)
      · 조례 청크는 재업로드 시 `delete_statute_chunks()` 로 지워진다. `doc_id` 만
        적어두면 그때 가리킬 곳이 없어진다.
      · run 폴더는 앞으로 정리 대상이다.

    그래서 **doc_id 가 아니라 본문**을 담는다. 실측 3.5KB 남짓이라(감리 918 · POI 113 ·
    조례 2,473, 흡연 parcel_id=2) 통짜로 넣어도 부담이 없다. 요약하지 않는다 —
    요약하면 토론에 들어간 것과 남는 것이 갈리고, 갈려도 안 터진다.

    ⚠ `poi_context` 는 **POI 를 붙이기 전의** `audit_context` 와 따로 받는다.
      A(`simulations.py`)는 프롬프트용으로 둘을 이어붙이는데, 이어붙인 뒤 넘기면
      「감리가 말한 것」과 「공간 연산이 말한 것」이 한 덩어리가 되어 구분이 사라진다.
    """
    meta = audit_meta or {}
    return {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "domain": domain,
        "run_id": run_id,
        "facility_type": facility_type,
        "audit_context": audit_context or None,
        "poi_context": poi_context or None,
        "exclusion_targets": meta.get("exclusion_targets") or [],
        "ahp_weights": meta.get("ahp_weights") or {},
        "ordinances": [
            {"doc_id": d.get("doc_id"), "text": d.get("text")} for d in (rag_docs or [])
        ],
    }
