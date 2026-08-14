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
from app.api.deps import get_db, get_redis
from app.db.models.simulation import (
    Parcel,
    HearingResultA,
    DebateLog,
    HearingResultB,
)

# 🔴 `from app.services.pdf_service import pdf_builder` 를 여기서 뺐다 (2026-08-04).
#    이 모듈 전체가 그 한 줄 때문에 import 불가였다 — PDF 내보내기(화면6) 하나 때문에
#    공청회 토론(화면5) 라우터 512행이 통째로 못 떴다.
#    실사용은 `download_feasibility_report_pdf` **한 곳뿐**이라 그 함수 안으로 옮겼다.
#    화면6 을 붙이는 사람이 볼 것: 이 파일이 아니라 그 함수의 주석이다.
from app.services.poi_context import build_poi_context

# 🔴 아래 다섯은 **이 파일에 있던 것을 옮긴 것**이다(2026-08-11). 사본이 아니라 이동이다.
#    화면5 토론 엔진이 둘(A `/simulation(s)/stream` · B `/stakeholders/*`)이라
#    "어디를·무엇을 근거로" 조달하는 코드가 한 곳에 있어야 한다. 여기 두면 B 가
#    이 API 모듈을 import 하게 되고, 그러면 곧 각자 조달하는 사본이 생긴다.
#    이름을 그대로 둔 이유는 그쪽 모듈 docstring 참조.
from app.services.candidate_context import (  # noqa: F401
    CandidateNotFound,
    _extract_dynamic_meta_from_audit_rules,
    _fetch_and_parse_audit_rules_from_db,
    basis_snapshot,
    resolve_candidate,
    retrieve_ordinance_texts,
    site_jibun,
)
from app.db.session import AsyncSessionLocal

# `/hearings` 404 의 사유를 가르는 데만 쓴다 — 「지금 DB 에 있나」(위 모델)와
# 「그때 넣었다는 기록이 있나」(러너의 status.json)는 다른 질문이다.
from app.services import pipeline_runner
from app.utils.redis_pubsub import RedisPubSubManager
from app.core.security_limiter import rate_limiter
from app.core.sim_ai.scenario import scenario_code

# API 라우터 인스턴스 초기화
router = APIRouter()

# uvicorn 콘솔로 나가는 로거. `print` 는 백그라운드 태스크에서 묻힌다.
logger = logging.getLogger("uvicorn.error")

# 시나리오 코드 → hearing_result_a 의 어느 칸에 넣을지.
# 근거는 `app/templates/default/reporter.txt` 다 — 수용도 0.8↑ A(원만한 타결),
# 0.4~0.8 B(조건부 타결), 0.4↓ C(협상 결렬). 여기서 새로 정한 대응이 아니다.
_SCENARIO_COLUMN = {
    "A": "optimal_scenario",
    "B": "normal_scenario",
    "C": "worst_scenario",
}


# 토론 1라운드의 CSS(갈등 민감도) 초기값.
#
# 🔴 예전엔 `random.choice(["LOW","MEDIUM","HIGH"])` 였다. 이건 표시값이 아니라
#    **1라운드 프롬프트를 갈아끼우는 값**이다 — `graph.pro_node:109`·`con_node:149` 가
#    이 값으로 `css_{high,medium,low}.txt` 중 하나를 고른다("근거 없이는 양보하지
#    마세요" ↔ "가능한 빠르게 합의점을 찾으세요"). 같은 후보지·같은 감리 근거로도
#    토론 내용이 매번 달라졌고, 라운드 1은 이후 라운드 전부의 입력이라 결과까지 갈렸다.
#    안 터지고 값만 틀린다.
#
# HIGH 로 고정한 이유 — **새로 정한 값이 아니라 이미 선언돼 있던 기본값**이다.
#    ① `graph.py:109/149` 의 `state.get("css_pro", "HIGH")` ② `prompts.py:62` 의
#    `CSS_PROMPT_TEMPLATE` 폴백 ③ 수용도 0.0 에서 출발하므로
#    `graph._map_css_by_score(0.0) == "HIGH"` — 라운드 1만 따로 놀지 않는다.
#    라운드 2부터는 예나 지금이나 evaluator 의 수용도 점수로 결정론적으로 다시 매핑된다.
#
# 도메인 값이 아니라 **토론 진행 방식**이라 원칙 2(하드코딩 금지)에 걸리지 않는다.
# 대신 값과 출처를 `result_json["determinism"]` 에 남긴다 — 안 남기면 AI 가 판정한
# 초기값처럼 읽힌다(원칙 4).
INITIAL_CSS_LEVEL = "HIGH"
INITIAL_CSS_SOURCE = "deterministic_default"


# 🔴 2026-08-11. 코드 추출을 `app/core/sim_ai/scenario.py` 로 **옮겼다**(사본 아님).
#    여기 있던 구현은 `"Scenario A"` 를 받으면 `SCENARIO` 의 **`C`** 를 먼저 집어
#    `worst_scenario` 칸에 넣었다. A 엔진 템플릿이 `"A (또는 B, C)"` 형식이라
#    여태 안 드러났을 뿐이다. `audit_ai/classifier.py` 도 같은 함수를 쓴다 —
#    소비자마다 각자 뽑으면 어휘가 갈린다(실제로 갈려 있었다).
_scenario_code = scenario_code


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
    """STEP5 산출물을 `hearing_result_a` + `debate_logs` 에 적재하고 id 를 돌려준다.

    🔴 2026-08-09 신설(B안). 예전엔 `HearingResultA(parcel_id, facility_type,
       result_json)` 3필드를 그대로 넣었는데 **실 DB 에 그 셋이 다 없어서** 항상
       `UndefinedColumnError` 였다. 지금은 실 DB 컬럼까지 같이 채운다.

    쪼갠 값은 전부 `result_json` 에도 남는다 — 컬럼으로 안 쪼갠 값
    (candidate_lat/lng·intensity_level·timestamp)이 있어서 원본을 통째로 보관한다.
    """
    # 후보점이 놓인 필지. 기존 FK(candidate_land_id)를 dangling 으로 두지 않는다.
    # 값을 만들어 넣는 게 아니라 booth_candidates.land_id 에서 **유도**한다.
    land_id = await db.scalar(select(Parcel.land_id).where(Parcel.id == parcel_id))

    sim = HearingResultA(
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
            #    조회가 안 되면 멈춘다. 바깥 except 가 SSE 로 사유를 내보낸다.
            #
            # 🔴 `run_id` 도 **같은 행에서** 꺼낸다 (2026-08-10, 사람 승인).
            #    감리 규칙은 실행마다 다시 적재되므로 도메인만으로는 2회차에
            #    두 실행의 규칙이 섞인다(`_select_audit_rules` 주석).
            #    "어디를 논의할지"와 "무엇을 근거로 논의할지"가 한 행에서 나오면
            #    둘이 어긋날 수가 없다 — domain 을 여기서 꺼내는 이유와 같다.
            #
            # 위 세 검사는 `resolve_candidate` 안에 있다. B 다인 토론도 같은 함수를
            # 쓴다 — 폴백 금지 규칙이 엔진마다 따로 있으면 한쪽만 되살아난다.
            resolved = await resolve_candidate(db, parcel_id)
            parcel = resolved["parcel"]
            domain = resolved["domain"]
            run_id = resolved["run_id"]

            audit_context = await _fetch_and_parse_audit_rules_from_db(
                db, facility_type, domain, run_id
            )
            audit_meta = await _extract_dynamic_meta_from_audit_rules(
                db, facility_type, domain, run_id
            )

            # (기존에 DB의 facility_type으로 강제 덮어씌우던 로직 제거: 클라이언트 요청 facility_type 유지)
            # 🔴 "booth_candidates 에는 지번 컬럼이 없습니다" 라고 적혀 있던 자리다.
            #    2026-08-10 적재기가 `jibun` 을 넣으면서 거짓이 됐고, 2026-08-11
            #    사람 지시로 **실제 지번을 쓴다**. 조립은 `site_jibun` 한 곳에 있다 —
            #    B 다인 토론(`build_site_context`)도 같은 함수를 쓴다.
            # 위경도는 ST_Y, ST_X로 추출한 실제 값을 사용합니다.
            gis_data = {
                "lat": resolved["lat"],
                "lng": resolved["lng"],
                "jibun": site_jibun(parcel, audit_meta.get("region")),
                # ⚠ 측정값이 아니다. 산출 근거가 없어 고정값을 쓰고 있고,
                #   그 사실을 `result_json["determinism"]` 에 남긴다(원칙 4).
                "intensity_level": "보통",
                "ahp_weights": audit_meta.get("ahp_weights", {}),
            }

            # 0. 그 후보점의 **run 이 낸 STEP2 산출물**에서 주변 문맥을 센다.
            #    예전엔 별도 적재 테이블 6개(`gis_service.get_poi_context_from_db`)를 봤는데
            #    그 테이블엔 `domain`·`run_id` 가 없어 **어떤 run 을 물어도 같은 답**이
            #    나왔다(사유는 `app/services/poi_context.py` 모듈 주석).
            #    B 다인 토론도 같은 함수를 쓴다 — 두 엔진이 다른 문맥으로 토론하면
            #    두 결과를 나란히 놓고 비교할 수 없다.
            poi_context = (await build_poi_context(resolved))["text"]

            # 🔴 POI 를 붙이기 **전** 상태를 따로 잡아둔다. 아래에서 이어붙이고 나면
            #    「감리가 말한 것」과 「공간 연산이 말한 것」이 한 덩어리가 되어
            #    결과 스냅샷에서 구분이 사라진다.
            audit_context_no_poi = audit_context

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

            # 토론 시작 전 공통 RAG(Common RAG) 1회 선검색.
            # 시설별 검색 키워드와 `facility_type` 필터는 공용 함수 안에 있다 —
            # B 다인 토론도 **같은 조문**으로 토론해야 두 결과를 나란히 비교할 수 있다.
            common_rag, rag_docs_list = await retrieve_ordinance_texts(
                facility_type, terms=audit_meta.get("exclusion_targets")
            )

            # 이 토론이 **무엇을 근거로 했는지**를 결과에 박아둔다(원칙 4).
            # 「나중에 다시 조회하면 나온다」는 전제는 실제로 깨진다 —
            # `load_audit_data.py` 는 같은 `(domain, run_id)` 의 audit_rules 를 교체하는데
            # `hearing_result_a` 와는 FK 가 없어서 **토론은 남고 근거만 바뀐다.**
            # 조립은 A·B 공용 함수 한 곳에 있다(두 엔진의 근거를 비교하려면 모양이 같아야 한다).
            basis = basis_snapshot(
                domain=domain,
                run_id=run_id,
                facility_type=facility_type,
                audit_context=audit_context_no_poi,
                poi_context=poi_context,
                rag_docs=rag_docs_list,
                audit_meta=audit_meta,
            )

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

            initial_state = {
                "messages": [],
                # 무작위 아님. 근거는 INITIAL_CSS_LEVEL 주석 참조.
                "css_pro": INITIAL_CSS_LEVEL,
                "css_con": INITIAL_CSS_LEVEL,
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
                                    # 🔴 못 읽은 값을 0.0/"MEDIUM" 으로 채우지 않는다.
                                    #    0.0 은 「완전 평행선」이라는 **정상 판정**과
                                    #    화면에서 구분이 안 된다(원칙 4). null 을 보내면
                                    #    프런트가 직전 값을 유지한다(hearing/page.tsx).
                                    _evals = node_state.get("evaluations") or {}
                                    _pro = _evals.get("pro_acceptance")
                                    _con = _evals.get("con_acceptance")
                                    metrics = {
                                        "pro_acc": float(_pro)
                                        if isinstance(_pro, (int, float))
                                        else None,
                                        "con_acc": float(_con)
                                        if isinstance(_con, (int, float))
                                        else None,
                                        "css_pro": node_state.get("css_pro"),
                                        "css_con": node_state.get("css_con"),
                                        "eval_error": _evals.get("eval_error"),
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
                                # 이 토론에 실제로 들어간 근거 원문. 결론만 남기면
                                # 나중에 "무엇을 보고 이렇게 판단했나"를 되짚을 수 없다.
                                "basis": basis,
                                # 이 토론에 들어간 값 중 **측정된 게 아닌 것**을 밝힌다.
                                # 안 적으면 AI 가 판정한 값처럼 읽힌다(원칙 4).
                                "determinism": {
                                    "initial_css_pro": INITIAL_CSS_LEVEL,
                                    "initial_css_con": INITIAL_CSS_LEVEL,
                                    "initial_css_source": INITIAL_CSS_SOURCE,
                                    # ⚠ intensity_level 은 아직 측정값이 아니다
                                    #   (`gis_data` 조립부의 고정값). 산출할 근거가
                                    #   생기기 전까지는 그 사실을 여기 남긴다.
                                    "intensity_level_source": "hardcoded_default",
                                },
                            }

                            # 최종 JSON을 DB에 저장 (HearingResultA)
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
                                    "[simulations] hearing_result_a 저장 실패 "
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


async def _missing_run_detail(run_id: str, domain: str | None) -> dict:
    """후보점이 0행일 때 **왜 없는지**를 객체로 만든다 (프런트 회신 2026-08-11).

    🔴 「적재된 적 없다」와 「적재됐는데 지금 없다」는 다른 사실인데 예전엔 둘 다
       같은 404 문자열이었다 → 화면에는 「서버에 기록이 없다」로 보이는데 실제로는
       「있었는데 덮어써졌다」였다(원칙 4). 실제로 밟았다: `r_20260810_002`·`006` 은
       `status.json` 에 `loaded.booth_candidates = 20` 이 있는데 DB 엔 0행이다 —
       E2E 테스트 도메인을 손으로 지우면서 `ON DELETE CASCADE` 로 딸려 나갔다.

    답을 **기록에 되쓰지 않고 요청 시점에 두 기록을 대조해서** 만든다:
      ① 지금 DB 에 있는 것   `booth_candidates`
      ② 그때 넣었다는 기록   `runs/<run_id>/status.json` 의 `loaded`
    둘 다 참이고, 사유는 그 **차이**다. 어느 쪽도 고쳐 쓰지 않는다.

    `detail` 은 문자열이 아니라 객체다(프런트 `client.ts:readDetail()` 이 객체를
    처리한다). 🔴 **`message` 는 어느 갈래에서도 반드시 채운다** — 프런트는 모르는
    `code` 를 만나면 분기하지 않고 `message` 를 그대로 띄운다. 비면 화면에 코드값만
    뜬다. 그래서 코드 집합이 늘어도 프런트 배포를 기다릴 필요가 없다.
    """
    rec = await asyncio.to_thread(pipeline_runner.loaded_record, run_id)
    loaded = rec["loaded"] if isinstance(rec.get("loaded"), dict) else None
    where = f"run_id={run_id!r}" + (f" domain={domain!r}" if domain else "")

    if rec["state"] == "unknown_run":
        code = "UNKNOWN_RUN"
        msg = (
            f"{where} 의 후보점이 없다. runs/{run_id} 폴더 자체가 없다 — "
            "run_id 가 틀렸거나 이 서버에서 실행한 run 이 아니다."
        )
    elif rec["state"] == "status_unreadable":
        # 「없다」가 아니라 「물을 수 없다」다. 같은 코드로 접으면 화면이 없는 말을 한다.
        code = "STATUS_UNREADABLE"
        msg = (
            f"{where} 의 후보점이 없다. runs/{run_id} 폴더는 있는데 status.json 을 "
            f"읽지 못해 적재 이력을 확인할 수 없다 ({rec['reason']}). "
            "「적재된 적 없다」는 뜻이 **아니다**."
        )
    elif loaded and (loaded.get("booth_candidates") or 0) > 0:
        code = "LOADED_BUT_MISSING"
        msg = (
            f"{where} 는 booth_candidates {loaded['booth_candidates']}행을 "
            "적재했다고 기록돼 있으나 지금 DB 에 없다. 적재 후 삭제되거나 "
            "덮어써졌다(같은 (domain, run_id) 재적재 · 수동 삭제 · DB 재생성). "
            "그 run 의 공청회·발화도 ON DELETE CASCADE 로 함께 지워졌다 — "
            "후보점은 topN.geojson 에서 다시 만들 수 있지만 토론은 재구성되지 않는다."
        )
    else:
        code = "NEVER_LOADED"
        msg = (
            f"{where} 의 후보점이 없고, 적재 기록도 없다. "
            "적재 칸이 없는 모드(fixture·hitl)이거나, full 이어도 적재 단계 전에 "
            "멈춘 run 이다. status.json 의 steps 에서 '적재-후보' 칸을 확인할 것."
        )

    detail = {
        "code": code,
        "message": msg,
        "run_id": run_id,
        "domain": domain,
        "loaded": rec["loaded"],
        "current": {"booth_candidates": 0},
    }

    # domain 을 걸어서 0행이면 **run 이 아니라 domain 이 틀렸을 수** 있다.
    # 그때 위 갈래는 전부 거짓말이 된다 — 행은 있는데 "없다"고 말하게 된다.
    if domain is not None:
        n_all = await _count_parcels(run_id)
        if n_all > 0:
            detail["current"]["booth_candidates_any_domain"] = n_all
            detail["code"] = "DOMAIN_MISMATCH"
            detail["message"] = (
                f"run_id={run_id!r} 에는 후보점 {n_all}행이 있으나 "
                f"domain={domain!r} 인 것은 없다. domain 을 빼고 다시 묻거나 "
                "status.json 의 domain 을 확인할 것."
            )
    return detail


async def _count_parcels(run_id: str) -> int:
    """`_missing_run_detail` 전용. 같은 세션을 다시 쓰지 않고 새로 연다 —
    호출 지점이 `raise HTTPException` 인자 안이라 요청 세션의 수명이 애매하다."""
    async with AsyncSessionLocal() as s:
        return await s.scalar(
            select(func.count(Parcel.id)).where(Parcel.run_id == run_id)
        ) or 0


@router.get("/hearings")
async def list_hearings(
    run_id: str,
    domain: str | None = None,
    engine: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """이 **실행(run)** 에서 열린 공청회 목록.

    🔴 `hearing_result_a` 에는 **`run_id` 컬럼이 없다.** 연결 경로는
       `parcel_id → booth_candidates.id → booth_candidates.run_id` **조인 하나뿐**이라
       "이 run 의 토론" 은 여기서만 물을 수 있다. 프런트는 `status.json` 의
       `loaded.run_id` 를 그대로 넘기면 된다.

    - `run_id` 는 **필수**다. 생략 시 최근 run 을 고르는 편의를 두지 않았다 —
      결과 문서 목록에서 어느 실행인지 모르면 목록 자체가 근거가 안 된다.
    - 정렬은 후보 `rank` 오름차순, 같은 후보 안에서는 `simulation_id` 오름차순.
    - 후보점은 있는데 토론이 없으면 **빈 배열 + 200** 이다(참인 진술). 후보점 자체가
      없으면 404 — 둘은 다른 상태다.
      🔴 404 의 `detail` 은 **문자열이 아니라 객체**다(2026-08-11, 프런트 합의).
      `{code, message, run_id, domain, loaded, current}` 이고 `code` 는 다섯이다:
      `UNKNOWN_RUN` · `STATUS_UNREADABLE` · `LOADED_BUT_MISSING` · `NEVER_LOADED` ·
      `DOMAIN_MISMATCH`. 사유를 어떻게 가르는지는 `_missing_run_detail` 참조.
      **`message` 는 항상 채운다** — 프런트는 모르는 `code` 를 만나면 분기하지 않고
      `message` 를 그대로 띄운다. 코드를 늘려도 프런트 배포를 안 기다리는 대신,
      비면 화면에 코드값만 뜬다.

    🔴 **`result_url` 이 항상 채워지지는 않는다.** `/results/{parcel_id}` 는 그 필지의
       **가장 최근 1건**만 돌려주므로(`ORDER BY id DESC`), 같은 필지를 여러 번 토론했다면
       옛 건에는 가리킬 URL 이 없다. 없는 걸 채우면 프런트가 **다른 토론 결과**를 그
       토론의 결과로 표시한다 — 안 터지고 값만 틀린다. 그래서 `is_latest_for_parcel`
       를 같이 준다. (같은 필지 재토론 정책은 프런트 회신 대기 중)
       ⚠ **B 는 이 문제가 없다** — 조회가 `hearing_result_b.id` 단건이라 옛 건도
         자기 URL 을 갖는다. A 만 「필지 최신 1건」 조회라서 생기는 제약이다.

    🔴 **행의 키 집합은 `engine` 에 따라 다르다.** A 는 `simulation_id`·`css_score`·
       `scenario_code`·`debate_log_count`, B 는 `hearing_id`·`topic`·`purpose`·
       `persona_count`·`message_count` 를 갖는다. 두 엔진은 합치지 않기로 한 것이라
       (2026-08-10) 산출물 지표가 겹치지 않는다 — 공통 칸에 억지로 접으면 없는
       대응관계를 지어내게 된다(원칙 5). 모든 행에 `engine` 이 있으니 그걸로 가른다.
    """
    if engine is not None and engine not in ("A", "B"):
        raise HTTPException(status_code=400, detail="engine 은 'A' 또는 'B' 다")

    cand_stmt = select(func.count(Parcel.id)).where(Parcel.run_id == run_id)
    if domain is not None:
        cand_stmt = cand_stmt.where(Parcel.domain == domain)
    if await db.scalar(cand_stmt) == 0:
        raise HTTPException(status_code=404, detail=await _missing_run_detail(run_id, domain))

    hearings: list[dict] = []

    # ── A 대립 토론 (hearing_result_a) ──────────────────────────────
    a_stmt = (
        select(
            HearingResultA.id.label("simulation_id"),
            HearingResultA.parcel_id,
            HearingResultA.facility_type,
            HearingResultA.css_score,
            HearingResultA.candidate_land_id,
            HearingResultA.created_at,
            # 시나리오는 **매 실행 1칸만** 채워진다. 본문 대신 어느 칸인지만 가져온다
            # (목록 응답에 Text 3칸을 실을 이유가 없다).
            HearingResultA.optimal_scenario.isnot(None).label("has_a"),
            HearingResultA.normal_scenario.isnot(None).label("has_b"),
            HearingResultA.worst_scenario.isnot(None).label("has_c"),
            Parcel.rank,
            Parcel.domain,
            Parcel.run_id,
            Parcel.jibun,
            func.count(DebateLog.id).label("debate_log_count"),
        )
        .join(Parcel, Parcel.id == HearingResultA.parcel_id)
        .outerjoin(DebateLog, DebateLog.simulation_id == HearingResultA.id)
        .where(Parcel.run_id == run_id)
        .group_by(HearingResultA.id, Parcel.id)
        .order_by(Parcel.rank.asc().nullslast(), HearingResultA.id.asc())
    )
    if domain is not None:
        a_stmt = a_stmt.where(Parcel.domain == domain)

    if engine != "B":
        rows = (await db.execute(a_stmt)).all()

        # 같은 필지의 여러 토론 중 마지막 것 = `/results/{parcel_id}` 가 돌려주는 그 행.
        # parcel_id 는 run 마다 새로 발번되는 serial PK 라 이 결과집합 안에서 최대값을
        # 구하면 전역 최대값과 같다(다른 run 이 같은 parcel_id 를 갖지 않는다).
        latest_of: dict[int, int] = {}
        for r in rows:
            if r.simulation_id > latest_of.get(r.parcel_id, -1):
                latest_of[r.parcel_id] = r.simulation_id

        for r in rows:
            is_latest = latest_of[r.parcel_id] == r.simulation_id
            scenario_code = (
                "A" if r.has_a else "B" if r.has_b else "C" if r.has_c else None
            )
            hearings.append(
                {
                    "simulation_id": r.simulation_id,
                    # 🔴 상수 "A" 다 — 추정이 아니라 **이 쿼리가 읽는 테이블이
                    #    `hearing_result_a` 하나**라서다. B 는 `hearing_result_b`
                    #    를 읽는 아래 블록이 따로 만든다(엔진을 안 합쳤으므로 조회도
                    #    안 합친다).
                    "engine": "A",
                    "parcel_id": r.parcel_id,
                    "rank": r.rank,
                    "jibun": r.jibun,
                    "domain": r.domain,
                    "run_id": r.run_id,
                    "facility_type": r.facility_type,
                    "candidate_land_id": r.candidate_land_id,
                    "css_score": float(r.css_score)
                    if r.css_score is not None
                    else None,
                    "scenario_code": scenario_code,
                    "debate_log_count": r.debate_log_count,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "is_latest_for_parcel": is_latest,
                    # 위 docstring 참조 — 최신 건에만 URL 을 준다.
                    "result_url": f"/api/v1/simulations/results/{r.parcel_id}"
                    if is_latest
                    else None,
                    "pdf_url": f"/api/v1/simulations/results/{r.parcel_id}/pdf"
                    if is_latest
                    else None,
                }
            )

    # ── B 다인 토론 (hearing_result_b) ─────────────────────────────────
    # 🔴 2026-08-11 이전엔 이 자리가 **501** 이었다("저장 경로가 없다"). 이제 있다.
    #    A 와 달리 `result_url` 이 **항상** 채워진다 — 조회 키가 필지가 아니라
    #    `hearing_result_b.id` 라 옛 건도 자기 자신을 가리킬 수 있다.
    if engine != "A":
        b_stmt = (
            select(
                HearingResultB.id.label("hearing_id"),
                HearingResultB.parcel_id,
                HearingResultB.facility_type,
                HearingResultB.topic,
                HearingResultB.purpose,
                # personas·result_json 은 목록에 안 싣는다(통짜 JSONB 다).
                # 몇 명이 토론했는지만 세어 준다.
                func.jsonb_array_length(HearingResultB.personas).label("persona_count"),
                HearingResultB.message_count,
                HearingResultB.created_at,
                Parcel.rank,
                Parcel.domain,
                Parcel.run_id,
                Parcel.jibun,
            )
            .join(Parcel, Parcel.id == HearingResultB.parcel_id)
            .where(Parcel.run_id == run_id)
            .order_by(Parcel.rank.asc().nullslast(), HearingResultB.id.asc())
        )
        if domain is not None:
            b_stmt = b_stmt.where(Parcel.domain == domain)

        for r in (await db.execute(b_stmt)).all():
            hearings.append(
                {
                    "hearing_id": r.hearing_id,
                    "engine": "B",
                    "parcel_id": r.parcel_id,
                    "rank": r.rank,
                    "jibun": r.jibun,
                    "domain": r.domain,
                    "run_id": r.run_id,
                    "facility_type": r.facility_type,
                    "topic": r.topic,
                    "purpose": r.purpose,
                    "persona_count": r.persona_count,
                    # 0 이면 "한 마디도 안 나온 토론" 이다 — 행이 없는 것과 다르다.
                    "message_count": r.message_count,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "result_url": f"/api/v1/simulations/hearings/b/{r.hearing_id}",
                }
            )

    # 엔진이 섞이면 각 블록의 정렬이 이어지지 않는다 — 합친 뒤 한 번 더 세운다.
    # 키는 (rank, created_at) 이고 rank 가 없는 행은 뒤로 보낸다.
    hearings.sort(
        key=lambda h: (
            h["rank"] is None,
            h["rank"] if h["rank"] is not None else 0,
            h["created_at"] or "",
        )
    )

    return {
        "run_id": run_id,
        "domain": domain,
        "engine": engine,
        "count": len(hearings),
        "hearings": hearings,
    }


@router.get("/hearings/b/{hearing_id}")
async def get_hearing_b(hearing_id: int, db: AsyncSession = Depends(get_db)):
    """B 다인 토론 결과 **1건**.

    A 의 `/results/{parcel_id}`(필지의 **최신 1건**)와 키가 다르다 — 여기는
    `hearing_result_b.id` 단건이다. 그래서 같은 필지를 여러 번 토론해도 옛 건이
    자기 URL 을 갖는다(`/hearings` 의 `result_url` 이 B 는 항상 채워지는 이유).

    🔴 `result_json` 은 **통짜로 그대로** 내보낸다. B 산출물 모양이 아직 움직이고
       있어서(B 담당자 소유) 여기서 쪼개면 키가 하나 바뀔 때마다 조용히 NULL 이 된다.
       `run_id`·`rank`·`jibun` 은 저장돼 있지 않고 `booth_candidates` 조인으로 얻는다.
    """
    stmt = (
        select(
            HearingResultB,
            Parcel.rank,
            Parcel.domain,
            Parcel.run_id,
            Parcel.jibun,
        )
        .join(Parcel, Parcel.id == HearingResultB.parcel_id)
        .where(HearingResultB.id == hearing_id)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"hearing_id={hearing_id} 의 B 다인 토론 결과가 없다.",
        )
    h, rank, domain, run_id, jibun = row

    return {
        "hearing_id": h.id,
        "engine": "B",
        "parcel_id": h.parcel_id,
        "rank": rank,
        "jibun": jibun,
        "domain": domain,
        "run_id": run_id,
        "facility_type": h.facility_type,
        "topic": h.topic,
        "purpose": h.purpose,
        "personas": h.personas,
        "message_count": h.message_count,
        "result_json": h.result_json,
        "created_at": h.created_at.isoformat() if h.created_at else None,
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
    #
    # 🔴 **여기에 `X-Accel-Buffering: no` 를 손으로 적지 않는다 — 이미 붙어 있다.**
    #    `sse_starlette/sse.py` 가 사용자가 넘긴 headers 를 반영한 **뒤에**
    #    `_headers["X-Accel-Buffering"] = "no"` 를 무조건 덮어쓴다(+ `Connection:
    #    keep-alive`, `Cache-Control: no-store`, 15초 ping). 그래서 이 줄을 더해도
    #    효과가 없고, 있으면 "이게 있어야 도는구나" 로 읽혀 지웠을 때 원인을 못 찾는다.
    #
    #    실측 2026-08-13 `api.omnisite.o-r.kr` — 같은 nginx·같은 분에
    #    A(여기)는 첫 데이터 0.727초 · recv 235조각 · 총 14.2초로 정상 스트리밍인데,
    #    B(`stakeholders.py`, 맨 StreamingResponse)는 42.4초 침묵 뒤 31개가
    #    3밀리초 안에 왔다. **갈린 것은 이 헤더 하나뿐이다**(이슈 #264).
    #    ⚠ 그러므로 `Back_deploy`(대문자) 의 `f8062f1`
    #      「X-Accel-Buffering 을 빼면 스트림이 돌아온다」는 **거꾸로다.** 그건
    #      로컬에서 본 증상인데 로컬엔 nginx 가 없어 이 헤더는 아무 일도 안 한다.
    #    ⚠ 이 엔드포인트를 `StreamingResponse` 로 바꾸면 그 순간 B 와 같은 병에 걸린다.
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Credentials": "true",
        # 🔴 `Cache-Control` 만 손으로 적는다. 위 주석대로 `X-Accel-Buffering` 은
        #    라이브러리가 무조건 덮어쓰지만(직접 대입), `Cache-Control` 은
        #    **`setdefault`** 라(`sse.py:53`) 여기서 준 값이 그대로 살아남는다 —
        #    안 주면 `no-store` 가 된다. 두 줄의 운명이 다르므로 같이 묶지 말 것.
        #
        #    왜 `no-transform` 인가 — `X-Accel-Buffering` 은 **nginx 만 아는 낱말**이라
        #    중간에 있는 게 nginx 가 아니면 한 글자도 안 읽는다. 우리 개발 환경이
        #    그렇다: Next dev 서버(`compress` 기본 true)가 프록시 응답을 gzip 으로
        #    다시 감싸는데, 그 압축기는 청크마다 flush 하지 않아 **스트림 전체를
        #    끝까지 모았다가 한 번에** 내보낸다. 실측(2026-08-13): 파이썬으로
        #    같은 경로를 재면 청크가 시간에 퍼져 오는데(끝 1초 1.5%), 브라우저로
        #    재면 **100% 가 마지막 순간에** 온다(encoded 7,262B ↔ decoded
        #    142,520B). 차이는 하나 — 파이썬은 `Accept-Encoding` 을 안 보낸다.
        #    `no-transform` 은 RFC 9111 의 「이 응답을 변형하지 마라」이고
        #    nginx·CDN·`compression` 미들웨어가 공통으로 읽는다.
        "Cache-Control": "no-cache, no-transform",
    }
    return EventSourceResponse(event_generator(), headers=headers)


@router.get("/results/{parcel_id}", response_model=SimulationResultResponse)
async def get_simulation_results(parcel_id: int, db: AsyncSession = Depends(get_db)):
    """
    [동현 AI 메인 & 장천명 풀스택] 모의 심의 토론 종결 후 최종 도출된 3대 시나리오 예측치 조회 API
    - 시점: 프론트엔드가 /stream SSE 커넥션을 닫은 직후, 최종 통계 데이터를 단독 로드하기 위해 호출합니다.
    - 구현: 실제 데이터베이스(hearing_result_a 테이블) 조회 결과에 따라 최신 이력을 동적으로 로드합니다.
    """
    # DB에서 가장 최신의 시뮬레이션 결과를 쿼리합니다.
    result = await db.execute(
        select(HearingResultA)
        .where(HearingResultA.parcel_id == parcel_id)
        .order_by(HearingResultA.id.desc())
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
        select(HearingResultA)
        .where(HearingResultA.parcel_id == parcel_id)
        .order_by(HearingResultA.id.desc())
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
