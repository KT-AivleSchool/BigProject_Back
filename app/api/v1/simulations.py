import asyncio
from fastapi import APIRouter, status
from sse_starlette.sse import EventSourceResponse
from app.schemas.simulations import SimulationRunRequest, SimulationResultResponse, ScenarioDetail

router = APIRouter()

@router.get("/stream")
def stream_ai_discussion(parcel_id: int):
    """
    [동현 AI 메인 & 장천명 풀스택] LangGraph 3자 페르소나 모의 심의 토론 실시간 SSE 스트리밍 API
    (Next.js EventSource 연동 테스트용 PoC 에코 모듈 탑재)
    """
    async def event_generator():
        # 가상의 3자 토론 시뮬레이션 스트리밍 데이터
        test_dialogues = [
            {"sender": "시스템", "text": f"필지 PNU-{parcel_id} 스마트 쉼터 모의 심의를 시작합니다.", "is_finished": False},
            {"sender": "주민대표 (반대)", "text": "도로 조례 제5조에 따르면, 해당 부지는 인근 주거 지역과의 소음 방지 거리가 확보되지 않았습니다!", "is_finished": False},
            {"sender": "상인대표 (찬성)", "text": "하지만 스마트 쉼터 설치로 인해 유동인구가 머무는 시간이 늘면 주변 골목 상권 활성화에 큰 도움이 됩니다.", "is_finished": False},
            {"sender": "조정 공무원", "text": "양측 의견을 수렴하여, 소음 방지 차폐막 설치 비용을 구청 예산에 반영하는 조건부 타결 시나리오 A를 제시합니다.", "is_finished": False},
            {"sender": "시스템", "text": "합의 타결 성공. 심의 보고서 인쇄가 가능합니다.", "is_finished": True}
        ]
        
        for dialogue in test_dialogues:
            # 1.5초 간격으로 스트림 패킷 발송
            await asyncio.sleep(1.5)
            yield {
                "event": "message",
                "data": f'{{"sender": "{dialogue["sender"]}", "text": "{dialogue["text"]}", "is_finished": {str(dialogue["is_finished"]).lower()}}}'
            }

            
    return EventSourceResponse(event_generator())

@router.get("/results/{parcel_id}", response_model=SimulationResultResponse)
def get_simulation_results(parcel_id: int):
    """
    [동현 AI 메인] 모의 심의 토론 종결 후 최종 도출된 3대 시나리오 예측치 조회 API
    """
    return {
        "parcel_id": parcel_id,
        "conflict_sensitivity_score": 7.8,
        "conflict_factors": {
            "소음피해": 8.5,
            "보행혼잡": 6.2,
            "임대료상승": 9.0
        },
        "scenarios": [
            {
                "scenario_type": "A",
                "title": "주민 합의 차폐막 설치 조건부 타결 시나리오",
                "probability": 0.65,
                "summary": "방음 펜스 및 차폐 조경 설치 예산을 구청이 부담하여 주민 소음 우려를 완화하고 설치를 완료함.",
                "conflict_risk_index": 35.0
            },
            {
                "scenario_type": "B",
                "title": "상인 연계 야외 데크 추가 확장 시나리오",
                "probability": 0.20,
                "summary": "스마트 쉼터 외부 휴게 공간을 주변 상가 입구와 수평 연계하여 골목 소상공인 매출 극대화를 꾀함.",
                "conflict_risk_index": 65.0
            },
            {
                "scenario_type": "C",
                "title": "공공 중재 전면 취소 및 대안 부지 이전 시나리오",
                "probability": 0.15,
                "summary": "민원 반발의 장기화 및 행정 비용의 과다로 인해 인근 공공 공지로 설치 대상을 전격 이전함.",
                "conflict_risk_index": 85.0
            }
        ]
    }

