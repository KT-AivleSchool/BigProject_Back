import json
import datetime
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.schemas.simulations import SimulationResultResponse
from app.core.sim_ai.graph import build_discussion_graph, vector_db
from app.core.sim_ai.document_loader import statute_document_loader
from app.db.session import get_db
from app.db.models.simulation import Parcel, ConflictSimulation

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
        parcel = result.scalar_first()

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

        # 3. 그래프 비동기 스트리밍 (astream)
        async for output in graph.astream(initial_state):
            for node_name, node_state in output.items():
                # 상태 업데이트 누적
                if "messages" in node_state:
                    current_state["messages"].extend(node_state["messages"])
                if "final_scenarios" in node_state:
                    current_state["final_scenarios"] = node_state["final_scenarios"]

                if node_name in ["pro", "con", "gov", "gov_wrapup", "evaluator"]:
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
                                {"sender": sender, "text": text, "is_finished": False},
                                ensure_ascii=False,
                            ),
                        }

                elif node_name == "reporter":
                    scenarios = current_state.get("final_scenarios", {})

                    # --- [NEW] DB 저장용 최종 JSON 포맷 구성 ---
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
                        "eval_score": current_state.get("eval_score", 0.0),
                        "scenario": scenarios,
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
                    # -------------------------------------------

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

    # sse_starlette 라이브러리의 EventSourceResponse를 반환하여 비동기 HTTP 청크 전송 스트림 활성화
    return EventSourceResponse(event_generator())


@router.get("/results/{parcel_id}", response_model=SimulationResultResponse)
async def get_simulation_results(parcel_id: int, db: AsyncSession = Depends(get_db)):
    """
    [동현 AI 메인] 모의 심의 토론 종결 후 최종 도출된 단일 시나리오 예측치 조회 API
    - 시점: 프론트엔드가 /stream SSE 커넥션을 닫은 직후, 최종 통계 데이터를 단독 로드하기 위해 호출합니다.
    """
    result = await db.execute(
        select(ConflictSimulation)
        .where(ConflictSimulation.parcel_id == parcel_id)
        .order_by(ConflictSimulation.created_at.desc())
    )
    sim = result.scalar_first()

    if not sim or not sim.result_json:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="해당 지번의 시뮬레이션 결과를 찾을 수 없습니다."
        )

    result_data = sim.result_json
    
    scenario = result_data.get("scenario", {})
    if not scenario:
        scenario = {
            "scenario": "A",
            "scenario_description": "결과 분석 오류",
            "final_acceptance_score": 0.0,
            "reason": "데이터 없음",
            "summary": "AI가 시나리오 생성에 실패했습니다.",
            "conflict_risk_index": 50.0,
            "risk_reason": "알 수 없는 오류"
        }

    ahp_weights = result_data.get("ahp_weights", {})
    if not ahp_weights:
        ahp_weights = {"소음피해": 0.0, "보행혼잡": 0.0, "임대료상승": 0.0}
        
    eval_score = result_data.get("eval_score", 0.0)
    # 갈등 민감도(CSS)는 10점 만점: 수용도가 높을수록 갈등은 낮음
    css_score = round(10.0 - (eval_score * 10.0), 1)

    return {
        "parcel_id": parcel_id,
        "conflict_sensitivity_score": css_score,
        "conflict_factors": ahp_weights,
        "scenario": scenario,
    }


@router.post("/statutes/upload")
async def upload_statute_document(file: UploadFile = File(...)):
    """
    [장천명 풀스택] 조례 및 범례 PDF/DOCX/HWP RAG 적재 라우터
    - 업로드된 다중 포맷 문서에서 텍스트를 추출하고 Chunking하여 Vector DB에 적재합니다.
    - AI 감리단이 파악한 시설 종류에 맞춰 추후 AI 페르소나가 자유롭게 의미 검색(Semantic Search)을 수행합니다.
    """
    import os

    ext = os.path.splitext(file.filename)[1].lower()
    
    # [수정] 파일 확장자와 MIME Type 매핑 (위장 파일 방어)
    allowed_types = {
        ".pdf": ["application/pdf"],
        ".docx": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
        ".doc": ["application/msword"],
        ".hwp": ["application/x-hwp", "application/haansofthwp", "application/vnd.hancom.hwp"],
    }

    if ext not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"지원하지 않는 파일 형식입니다. {list(allowed_types.keys())} 포맷만 업로드 가능합니다.",
        )

    # MIME Type 검증
    if not file.content_type or file.content_type not in allowed_types[ext]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="파일 확장자와 데이터 타입이 일치하지 않습니다. (위조 파일 의심)",
        )

    try:
        file_bytes = await file.read()
        chunks = statute_document_loader.process_document(file_bytes, ext)

        if not chunks:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{file.filename} 파일에서 텍스트를 추출할 수 없거나 비어 있습니다.",
            )

        # Vector DB 적재 (facility_type 불필요)
        await vector_db.add_statute_chunks(chunks)

        return {
            "status": "success",
            "message": f"{file.filename} 조례/범례 문서가 성공적으로 적재되었습니다.",
            "chunk_count": len(chunks),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"조례 RAG 문서 추출 및 적재 중 서버 오류가 발생했습니다: {str(e)}",
        )
