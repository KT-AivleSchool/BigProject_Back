import operator
from typing import TypedDict, Annotated, Sequence, List, Dict, Any
from app.core.stakeholder_mode.schemas.persona import PersonaConfig

class DynamicDiscussionState(TypedDict):
    """
    동적 다자간 토론 상태(State) 객체
    기존 MultiPartyAgentState를 확장하여 하드코딩된 역할 대신 동적 PersonaConfig를 사용합니다.
    """
    project_id: str
    topic: str
    site_information: str                             # 입지 및 시설 공간 데이터 요약
    
    # 동적 페르소나 설정 및 상태
    personas: List[Dict[str, Any]]                    # PersonaConfig의 dict 리스트
    active_participants: List[str]                    # 참여 중인 persona_id 리스트
    css_levels: Dict[str, str]                        # persona_id별 갈등 민감도 ("HIGH", "MEDIUM", "LOW")
    ordinance_contexts: List[Dict[str, Any]]          # 이전 단계에서 검색된 조례 정보 목록
    
    # 대화 및 진행 상태
    
    # 핑퐁(티키타카) 제어를 위한 필드
    # 핑퐁(티키타카) 제어를 위한 필드
    rebuttal_target: str
    rebuttal_count: int
    
    messages: Annotated[Sequence[str], operator.add]  # 대화 이력 누적
    next_speaker: str                                 # 라우터가 지정한 다음 발화자의 persona_id (또는 "evaluator")
    round_count: int                                  # 토론 진행 라운드 수
    
    # 평가 및 결과
    evaluations: dict                                 # persona_id별 수용도 정량 평가 결과
    final_scenarios: dict                             # 최종 3대 시나리오 결과
    is_finished: bool                                 # 세션 종료 여부
