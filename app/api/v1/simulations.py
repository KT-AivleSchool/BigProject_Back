import asyncio
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse
from app.schemas.simulations import SimulationResultResponse

# API 라우터 인스턴스 초기화
router = APIRouter()


@router.get("/stream")
def stream_ai_discussion(parcel_id: int):
    """
    [동현 AI 메인 & 장천명 풀스택] LangGraph 3자 페르소나 모의 심의 토론 실시간 SSE 스트리밍 API
    - 동작 방식: HTTP 연결을 유지한 상태로, AI 에이전트의 대화 토큰을 chunk 단위로 지속 밀어줍니다.
    - 프론트 연동: Next.js의 EventSource API와 1:1로 비동기식 실시간 세션을 체결하여 렌더링합니다.
    """

    async def event_generator():
        # [협업 지침] 동현님이 LangGraph를 구현하면, 아래 하드코딩된 대화 배열(test_dialogues)을
        # 실제 LangGraph의 노드 실행 발화 결과로 동적 바인딩해 주셔야 합니다.
        test_dialogues = [
            {
                "sender": "시스템",
                "text": f"필지 PNU-{parcel_id} 스마트 쉼터 모의 심의를 시작합니다.",
                "is_finished": False,
            },
            {
                "sender": "주민대표 (반대)",
                "text": "도로 조례 제5조에 따르면, 해당 부지는 인근 주거 지역과의 소음 방지 거리가 확보되지 않았습니다!",
                "is_finished": False,
            },
            {
                "sender": "상인대표 (찬성)",
                "text": "하지만 스마트 쉼터 설치로 인해 유동인구가 머무는 시간이 늘면 주변 골목 상권 활성화에 큰 도움이 됩니다.",
                "is_finished": False,
            },
            {
                "sender": "조정 공무원",
                "text": "양측 의견을 수렴하여, 소음 방지 차폐막 설치 비용을 구청 예산에 반영하는 조건부 타결 시나리오 A를 제시합니다.",
                "is_finished": False,
            },
            {
                "sender": "시스템",
                "text": "합의 타결 성공. 심의 보고서 인쇄가 가능합니다.",
                "is_finished": True,
            },
        ]

        for dialogue in test_dialogues:
            # 실시간 타이핑 효과 및 패킷 간 시간 간격을 시뮬레이션하기 위해 1.5초 지연(Sleep)
            await asyncio.sleep(1.5)

            # SSE 프로토콜 표준 규격에 맞게 event와 data의 JSON 문자열 스트림 구조로 yield 전송
            # is_finished 플래그를 실어주어 프론트엔드가 소켓 연결을 닫을 타이밍을 감지하게 합니다.
            yield {
                "event": "message",
                "data": f'{{"sender": "{dialogue["sender"]}", "text": "{dialogue["text"]}", "is_finished": {str(dialogue["is_finished"]).lower()}}}',
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
