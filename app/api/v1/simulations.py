import json
import datetime
from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.schemas.simulations import SimulationResultResponse
from app.core.sim_ai.graph import build_discussion_graph
from app.db.session import get_db
from app.db.models.simulation import Parcel, ConflictSimulation

# API 라우터 인스턴스 초기화
router = APIRouter()


@router.get("/stream")
def stream_ai_discussion(
    parcel_id: int, 
    facility_type: str,
    db: AsyncSession = Depends(get_db)
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
                "data": json.dumps({"error": "해당 필지(Parcel)를 찾을 수 없습니다."}, ensure_ascii=False)
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

        timestamp = datetime.datetime.now().isoformat()

        initial_state = {
            "messages": [],
            "css_pro": "HIGH",
            "css_con": "HIGH",
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
            "rag_pro": "",
            "rag_con": "",
            "rag_gov": "",
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
                        "scenarios": scenarios.get("scenarios", []),
                    }

                    # 최종 JSON을 DB에 저장 (ConflictSimulation)
                    try:
                        new_sim = ConflictSimulation(
                            parcel_id=parcel_id,
                            facility_type=facility_type,
                            result_json=result_json
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
def get_simulation_results(parcel_id: int):
    """
    [동현 AI 메인] 모의 심의 토론 종결 후 최종 도출된 3대 시나리오 예측치 조회 API
    - 시점: 프론트엔드가 /stream SSE 커넥션을 닫은 직후, 최종 통계 데이터를 단독 로드하기 위해 호출합니다.
    """
    # [협업 지침] 실제 데이터베이스(conflict_simulations 테이블) 조회 결과에 따라
    # 동적으로 점수 및 시나리오 통계치 데이터를 로드하도록 구현해 주세요.
    return {
        "parcel_id": parcel_id,
        "conflict_sensitivity_score": 7.8,  # 종합 갈등 민감도 점수 (CSS)
        "conflict_factors": {"소음피해": 8.5, "보행혼잡": 6.2, "임대료상승": 9.0},
        "scenarios": [
            {
                "scenario_type": "A",
                "title": "주민 합의 차폐막 설치 조건부 타결 시나리오",
                "probability": 0.65,  # 타결 예상 확률 (65%)
                "summary": "방음 펜스 및 차폐 조경 설치 예산을 구청이 부담하여 주민 소음 우려를 완화하고 설치를 완료함.",
                "conflict_risk_index": 35.0,  # 갈등 잔존 위험 지표
            },
            {
                "scenario_type": "B",
                "title": "상인 연계 야외 데크 추가 확장 시나리오",
                "probability": 0.20,  # 타결 예상 확률 (20%)
                "summary": "스마트 쉼터 외부 휴게 공간을 주변 상가 입구와 수평 연계하여 골목 소상공인 매출 극대화를 꾀함.",
                "conflict_risk_index": 65.0,
            },
            {
                "scenario_type": "C",
                "title": "공공 중재 전면 취소 및 대안 부지 이전 시나리오",
                "probability": 0.15,  # 타결 예상 확률 (15%)
                "summary": "민원 반발의 장기화 및 행정 비용의 과다로 인해 인근 공공 공지로 설치 대상을 전격 이전함.",
                "conflict_risk_index": 85.0,
            },
        ],
    }
