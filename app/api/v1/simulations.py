import asyncio
from fastapi import APIRouter, status
from sse_starlette.sse import EventSourceResponse

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
            {"sender": "시스템", "text": f"필지 PNU-{parcel_id} 스마트 쉼터 모의 심의를 시작합니다."},
            {"sender": "주민대표 (반대)", "text": "도로 조례 제5조에 따르면, 해당 부지는 인근 주거 지역과의 소음 방지 거리가 확보되지 않았습니다!"},
            {"sender": "상인대표 (찬성)", "text": "하지만 스마트 쉼터 설치로 인해 유동인구가 머무는 시간이 늘면 주변 골목 상권 활성화에 큰 도움이 됩니다."},
            {"sender": "조정 공무원", "text": "양측 의견을 수렴하여, 소음 방지 차폐막 설치 비용을 구청 예산에 반영하는 조건부 타결 시나리오 A를 제시합니다."},
            {"sender": "시스템", "text": "합의 타결 성공. 심의 보고서 인쇄가 가능합니다."}
        ]
        
        for dialogue in test_dialogues:
            # 1.5초 간격으로 스트림 패킷 발송
            await asyncio.sleep(1.5)
            yield {
                "event": "message",
                "data": f'{{"sender": "{dialogue["sender"]}", "text": "{dialogue["text"]}"}}'
            }
            
    return EventSourceResponse(event_generator())
