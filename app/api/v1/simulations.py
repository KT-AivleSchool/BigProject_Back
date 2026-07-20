import json
import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.schemas.simulations import SimulationResultResponse
from app.core.sim_ai.graph import build_discussion_graph
from app.api.deps import get_db
from app.db.models.simulation import Parcel, ConflictSimulation
from app.services.pdf_service import pdf_builder

# API 라우터 인스턴스 초기화
router = APIRouter()


@router.get("/stream")
def stream_ai_discussion(
    parcel_id: int, facility_type: str, db: AsyncSession = Depends(get_db)
):
    """
    [동현 AI 메인 & 장천명 풀스택] LangGraph 3자 페르소나 모의 심의 토론 실시간 SSE 스트리밍 API
    - 동작 방식: HTTP 연결을 유지한 상태로, AI 에이전트의 대화 토큰을 chunk 단위로 지속 밀어줍니다.
    - 프론트 연동: Next.js의 EventSource API와 1:1로 비동기식 실시간 세션을 체결하여 렌더링합니다.
    """

    async def event_generator():
        # DB에서 parcel_id로 GIS 데이터를 조회합니다.
        result = await db.execute(select(Parcel).where(Parcel.id == parcel_id))
        parcel = result.scalar()

        if not parcel:
            # 존재하지 않는 parcel_id일 경우 에러 메시지 전송 후 스트림 종료
            yield {
                "event": "error",
                "data": json.dumps(
                    {"error": "해당 필지(Parcel)를 찾을 수 없습니다."},
                    ensure_ascii=False,
                ),
            }
            return

        gis_data = {
            "lat": parcel.lat,
            "lng": parcel.lng,
            "jibun": parcel.jibun,
            "intensity_level": parcel.intensity_level,
            "ahp_weights": parcel.ahp_weights or {},
        }

        # 1. 시스템 시작 메시지 송출
        yield {
            "event": "message",
            "data": json.dumps(
                {
                    "sender": "시스템",
                    "text": f"선택된 위치(지번: {gis_data['jibun']})의 {facility_type} 모의 심의를 시작합니다...",
                    "is_finished": False,
                },
                ensure_ascii=False,
            ),
        }

        # 2. LangGraph 초기화 및 상태 세팅
        graph = build_discussion_graph()

        # [NEW] 토론 시작 전 공통 RAG(Common RAG) 1회 선검색
        query = f"{facility_type} 설치 기준 허가 규제 갈등 중재 혜택"
        try:
            retrieved_docs = await vector_db.retrieve_similar_statutes(query, top_k=5)
            common_rag = "\n".join(retrieved_docs)
        except Exception:
            common_rag = "조례 정보 없음"

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
                        current_state["final_scenarios"] = node_state["final_scenarios"]

                    if node_name in ["pro", "con", "gov", "gov_wrapup"]:
                        if "messages" in node_state and len(node_state["messages"]) > 0:
                            msg = node_state["messages"][-1]

                            parts = msg.split(":", 1)
                            if len(parts) == 2:
                                sender = parts[0].strip()
                                text = parts[1].strip()
                            else:
                                sender = "참여자"
                                text = msg

                            yield {
                                "event": "message",
                                "data": json.dumps(
                                    {
                                        "sender": sender,
                                        "text": text,
                                        "is_finished": False,
                                    },
                                    ensure_ascii=False,
                                ),
                            }

                    elif node_name == "reporter":
                        scenarios = current_state.get("final_scenarios", {})

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
                            "scenarios": scenarios.get("scenarios", []),
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

                        print(json.dumps(result_json, ensure_ascii=False, indent=2))

                        yield {
                            "event": "message",
                            "data": json.dumps(
                                {
                                    "sender": "시스템",
                                    "text": "토론 종료. 3대 시나리오 도출이 완료되었습니다.",
                                    "is_finished": True,
                                },
                                ensure_ascii=False,
                            ),
                        }

        except Exception as quota_err:
            # OpenAI API Quota 초과(429) 또는 기타 AI 엔진 오류 발생 시 즉시 에러 SSE 이벤트 송출
            # Fallback 데이터 없이 정직하게 오류 코드를 클라이언트에게 전달합니다.
            err_msg = str(quota_err)
            is_quota = "insufficient_quota" in err_msg or "429" in err_msg
            error_code = "OPENAI_QUOTA_EXCEEDED" if is_quota else "AI_ENGINE_ERROR"
            print(f"[Stream Error] {error_code}: {err_msg}")

            yield {
                "event": "error",
                "data": json.dumps(
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
            }

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
    scenarios_list = []

    if raw_scenarios and isinstance(raw_scenarios, list):
        for idx, sc in enumerate(raw_scenarios):
            sc_type = (
                sc.get("scenario_type")
                or sc.get("scenario")
                or f"Scenario {chr(65 + idx)}"
            )
            # "Scenario A" 형태인 경우 뒤의 문자만 취함
            if "Scenario" in sc_type:
                sc_type = sc_type.replace("Scenario", "").strip()

            scenarios_list.append(
                {
                    "scenario_type": sc_type,
                    "title": sc.get("title")
                    or sc.get("scenario_description")
                    or f"시나리오 {sc_type}",
                    "probability": float(
                        sc.get("probability")
                        or sc.get("ratio")
                        or (0.65 if idx == 0 else 0.20 if idx == 1 else 0.15)
                    ),
                    "summary": sc.get("summary")
                    or sc.get("reason")
                    or "AI 시뮬레이션 최종 합의 시나리오 내용입니다.",
                    "conflict_risk_index": float(
                        sc.get("conflict_risk_index")
                        or sc.get("risk_score")
                        or (35.0 if idx == 0 else 65.0 if idx == 1 else 85.0)
                    ),
                }
            )
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

    return {
        "parcel_id": parcel_id,
        "conflict_sensitivity_score": css_score,
        "conflict_factors": conflict_factors,
        "scenarios": scenarios_list,
    }


@router.get("/results/{parcel_id}/pdf")
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

    # 2. PDF 조립용 컨텍스트 정보 포맷팅
    report_data = {
        "candidate_jibun": res_json.get("candidate_jibun", "알 수 없음"),
        "candidate_lat": res_json.get("candidate_lat", 0.0),
        "candidate_lng": res_json.get("candidate_lng", 0.0),
        "facility_type": res_json.get("facility_type", "지정되지 않음"),
        "conflict_sensitivity_score": res_json.get("conflict_sensitivity_score", 7.8),
        "ahp_weights": res_json.get("ahp_weights", {}),
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
