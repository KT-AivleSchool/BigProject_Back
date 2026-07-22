import json
import datetime
import asyncio
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.schemas.simulations import SimulationResultResponse, StreamRequest
from app.core.sim_ai.graph import build_discussion_graph, vector_db
from app.api.deps import get_db, get_redis
from app.db.models.simulation import Parcel, ConflictSimulation
from app.services.pdf_service import pdf_builder
from app.db.session import AsyncSessionLocal
from app.utils.redis_pubsub import RedisPubSubManager
from app.core.security_limiter import rate_limiter

# API 라우터 인스턴스 초기화
router = APIRouter()


def _parse_audit_data(audit_data: dict) -> str:
    if not audit_data:
        return "프론트엔드 감리 데이터 없음"

    positive = []
    negative = []
    hard_exclusion = []

    results = audit_data.get("results", [])
    for res in results:
        roles = res.get("roles", [])
        for r in roles:
            role_type = r.get("role", "")
            rationale = r.get("rationale", "")
            if role_type == "positive_factor":
                positive.append(f"- {rationale}")
            elif role_type == "negative_factor":
                negative.append(f"- {rationale}")
            elif role_type == "hard_exclusion":
                source = r.get("source", "출처 불명")
                hard_exclusion.append(f"- [절대금지] {rationale} (근거: {source})")

    lines = []
    if positive:
        lines.append("## 설치 가점 요인\n" + "\n".join(positive))
    if negative:
        lines.append("## 설치 감점/갈등 요인\n" + "\n".join(negative))
    if hard_exclusion:
        lines.append("## 절대 배제(금지) 요인\n" + "\n".join(hard_exclusion))

    if not lines:
        return "유효한 감리 팩터가 발견되지 않았습니다."

    return "\n\n".join(lines)


async def run_debate_and_publish(
    parcel_id: int,
    facility_type: str,
    audit_context: str,
    redis: aioredis.Redis,
):
    pubsub_manager = RedisPubSubManager(redis)
    async with AsyncSessionLocal() as db:
        try:
            try:
                # DB에서 parcel_id로 GIS 데이터를 조회합니다.
                result = await db.execute(select(Parcel).where(Parcel.id == parcel_id))
                parcel = result.scalar()

                if not parcel:
                    await pubsub_manager.publish_debate_message(
                        parcel_id,
                        "시스템",
                        "해당 필지(Parcel)를 찾을 수 없습니다.",
                        is_finished=True,
                    )
                    return

                gis_data = {
                    "lat": parcel.lat,
                    "lng": parcel.lng,
                    "jibun": parcel.jibun,
                    "intensity_level": parcel.intensity_level,
                    "ahp_weights": parcel.ahp_weights or {},
                }
            except Exception as e:
                print(
                    f"[Fallback] DB 연결 실패({e}). 로컬 모의 GIS 데이터로 대체합니다."
                )
                gis_data = {
                    "lat": 37.534,
                    "lng": 126.994,
                    "jibun": "서울특별시 용산구 이태원동 123-45 (테스트용)",
                    "intensity_level": "높음",
                    "ahp_weights": {
                        "보행혼잡도": 0.4,
                        "소음민감도": 0.3,
                        "상권활성화": 0.3,
                    },
                }

            # 1. 시스템 시작 메시지 송출
            await pubsub_manager.publish_debate_message(
                parcel_id,
                "시스템",
                f"선택된 위치(지번: {gis_data['jibun']})의 {facility_type} 모의 심의를 시작합니다...",
                is_finished=False,
            )

            # 2. LangGraph 초기화 및 상태 세팅
            graph = build_discussion_graph()

            # [NEW] 토론 시작 전 공통 RAG(Common RAG) 1회 선검색
            query = f"{facility_type} 설치 기준 허가 규제 갈등 중재 혜택"
            try:
                retrieved_docs = await vector_db.retrieve_similar_statutes(
                    query, top_k=5, facility_type=facility_type
                )
                if not retrieved_docs:
                    common_rag = (
                        "현재 해당 지역에 적용할 수 있는 조례나 법령 정보가 없습니다."
                    )
                else:
                    common_rag = "\n".join(retrieved_docs)
            except Exception as e:
                print(f"[RAG Error] 조례 검색 실패: {e}")
                common_rag = (
                    "현재 해당 지역에 적용할 수 있는 조례나 법령 정보가 없습니다."
                )

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
                "audit_context": audit_context,
                "evaluations": {},
                "final_scenarios": {},
                "is_finished": False,
                "next_speaker": "pro",
            }

            # 내부 상태 누적용 변수
            current_state = dict(initial_state)

            # 3. 그래프 비동기 스트리밍 (astream) — OpenAI Quota 초과 시 에러 이벤트 즉시 송출
            try:
                async for output in graph.astream(initial_state):
                    for node_name, node_state in output.items():
                        # 상태 업데이트 누적
                        if "messages" in node_state:
                            current_state["messages"].extend(node_state["messages"])
                        if "final_scenarios" in node_state:
                            current_state["final_scenarios"] = node_state[
                                "final_scenarios"
                            ]

                        if node_name in [
                            "pro",
                            "con",
                            "gov",
                            "gov_wrapup",
                            "evaluator",
                            "reporter",
                        ]:
                            if (
                                "messages" in node_state
                                and len(node_state["messages"]) > 0
                            ):
                                msg = node_state["messages"][-1]

                                parts = msg.split(":", 1)
                                if len(parts) == 2:
                                    sender = parts[0].strip()
                                    text = parts[1].strip()
                                else:
                                    sender = "참여자"
                                    text = msg

                                await pubsub_manager.publish_debate_message(
                                    parcel_id, sender, text, is_finished=False
                                )

                        if node_name == "reporter":
                            # 단일 시나리오 객체일 경우 리스트로 래핑
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

                            # CSS 점수 계산 (평균 점수(0.0~1.0)를 0~10점 척도로 환산)
                            avg_acc = current_state.get("eval_score", 0.0)
                            css_score = round(avg_acc * 10, 2)
                            if css_score == 0.0:
                                css_score = 7.5  # 기본값 처리

                            # --- DB 저장용 최종 JSON 포맷 구성 ---
                            debate_logs = []
                            sys_msg = "[시스템 면책 고지] 본 모의 심의 토론 내용은 AI 페르소나 엔진에 의해 생성된 가상의 시나리오이며, 실제 인물이나 단체, 사실관계와는 전혀 무관합니다."
                            debate_logs.append({"sender": "시스템", "text": sys_msg})
                            raw_text_lines = [sys_msg]

                            for msg in current_state.get("messages", []):
                                parts = msg.split(":", 1)
                                if len(parts) == 2:
                                    s, t = parts[0].strip(), parts[1].strip()
                                else:
                                    s, t = "참여자", msg
                                debate_logs.append({"sender": s, "text": t})
                                raw_text_lines.append(msg)

                            result_json = {
                                "candidate_jibun": current_state.get("candidate_jibun"),
                                "candidate_lat": current_state.get("candidate_lat"),
                                "candidate_lng": current_state.get("candidate_lng"),
                                "facility_type": current_state.get("facility_type"),
                                "intensity_level": current_state.get("intensity_level"),
                                "ahp_weights": current_state.get("ahp_weights"),
                                "timestamp": current_state.get("timestamp"),
                                "debate_logs": debate_logs,
                                "raw_text": "\n\n".join(raw_text_lines),
                                "scenarios": final_scenarios_list,
                                "conflict_sensitivity_score": css_score,
                                "conflict_factors": current_state.get(
                                    "ahp_weights", {}
                                ),
                            }

                            # 최종 JSON을 DB에 저장 (ConflictSimulation)
                            try:
                                new_sim = ConflictSimulation(
                                    parcel_id=parcel_id,
                                    facility_type=facility_type,
                                    result_json=result_json,
                                )
                                db.add(new_sim)
                                await db.commit()
                                print("=== 최종 도출된 JSON 결과 (DB 저장 성공) ===")
                            except Exception as e:
                                await db.rollback()
                                print(f"=== DB 저장 실패: {e} ===")

                            await pubsub_manager.publish_debate_message(
                                parcel_id,
                                "시스템",
                                f"모의 심의 토론이 최종 종료되었습니다. 도출된 최종 단일 시나리오:\n\n{json.dumps(final_scenarios_list, ensure_ascii=False, indent=2)}",
                                is_finished=True,
                            )
            except Exception as graph_err:
                err_msg = str(graph_err)
                is_quota = "quota" in err_msg.lower() or "429" in err_msg
                error_code = "OPENAI_QUOTA_EXCEEDED" if is_quota else "AI_ENGINE_ERROR"

                print(f"[AI Simulation Error] {error_code}: {err_msg}")
                await pubsub_manager.publish_debate_message(
                    parcel_id,
                    "시스템 오류",
                    json.dumps(
                        {
                            "error_code": error_code,
                            "message": (
                                "OpenAI API Quota가 초과되었습니다. API 키 잔액을 충전하고 다시 시도해 주세요."
                                if is_quota
                                else f"AI 토론 엔진 오류가 발생했습니다: {err_msg}"
                            ),
                            "is_finished": True,
                        },
                        ensure_ascii=False,
                    ),
                )
        except Exception as e:
            print(f"[Fatal Simulation Error] {e}")


@router.post("/stream", dependencies=[Depends(rate_limiter)])
async def stream_ai_discussion(
    request: StreamRequest, redis: aioredis.Redis = Depends(get_redis)
):
    parcel_id = request.parcel_id
    facility_type = request.facility_type

    # 전달받은 JSON 데이터를 파싱하여 텍스트로 정제
    audit_context = (
        _parse_audit_data(request.audit_data)
        if request.audit_data
        else "감리 데이터가 제공되지 않았습니다."
    )

    # 1. 백그라운드 태스크로 모의 심의 테스트 실행 (비동기로 루프를 돌며 Redis에 Publish)
    asyncio.create_task(
        run_debate_and_publish(
            parcel_id=parcel_id,
            facility_type=facility_type,
            audit_context=audit_context,
            redis=redis,
        )
    )

    # 2. SSE 클라이언트는 동일 채널을 Subscribe하여 실시간 청크 응답
    pubsub_manager = RedisPubSubManager(redis)

    async def event_generator():
        async for data in pubsub_manager.subscribe_debate_stream(parcel_id):
            yield {"event": "message", "data": json.dumps(data, ensure_ascii=False)}

    # sse_starlette 라이브러리의 EventSourceResponse를 반환하여 비동기 HTTP 청크 전송 스트림 활성화
    return EventSourceResponse(event_generator())


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
    sim_data = result.scalar_first()

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
    sim_data = result.scalar_first()

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
    try:
        pdf_file = pdf_builder.generate_feasibility_pdf(report_data)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"PDF 생성 중 오류가 발생했습니다. 서버 환경(WeasyPrint/폰트 설치)을 확인해 주세요. 오류: {str(e)}",
        )

    filename = f"OmniSite_Feasibility_Report_{parcel_id}.pdf"
    return StreamingResponse(
        pdf_file,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
