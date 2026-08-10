# [이해관계자 페르소나 모드] Phase 2 확장 PersonaConfig 생성 팩토리 서비스
from typing import List
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import settings
from app.core.stakeholder_mode.schemas.stakeholder import (
    CandidateSite,
    OrdinanceContext,
    StakeholderCandidate
)
from app.core.stakeholder_mode.schemas.persona import PersonaConfig, Priority


async def create_persona_configs(
    topic: str,
    selected_stakeholders: List[StakeholderCandidate],
    candidate_sites: List[CandidateSite],
    ordinance_contexts: List[OrdinanceContext],
    model_name: str = "gpt-4o-mini"
) -> List[PersonaConfig]:
    """
    [Phase 2 확장 PersonaConfig 팩토리 서비스]
    사용자가 선택/수정한 이해관계자 목록을 바탕으로 중요도 등급(A~D), 참여 유형, 관심사별 가중치(priorities)
    및 절대 수용 불가 조건(non_negotiable_conditions)이 설정된 PersonaConfig 객체들을 생성합니다.
    """
    # 1. LLM 클라이언트 인스턴스 초기화
    llm = ChatOpenAI(
        api_key=settings.OPENAI_API_KEY,
        model=model_name,
        temperature=0.7
    )
    
    # 2. PersonaConfig Pydantic 모델에 맞춘 구조화 출력 바인딩
    structured_llm = llm.with_structured_output(PersonaConfig, method="function_calling")
    
    # 3. 근거 ID 및 조례 ID 목록 생성
    evidence_ids = [site.candidate_id for site in candidate_sites]
    ordinance_ids = [ord_item.chunk_id for ord_item in ordinance_contexts]

    persona_configs: List[PersonaConfig] = []

    # 4. 각 이해관계자별로 PersonaConfig 객체 비동기 생성
    for index, candidate in enumerate(selected_stakeholders, start=1):
        persona_id = f"PERSONA-{index:03d}"
        
        system_prompt = f"""당신은 AI 페르소나 설계 전문가입니다.
주어진 이해관계자 정보({candidate.name}, 유형: {candidate.stakeholder_type})와 토론 주제를 바탕으로, 해당 이해관계자의 상세 페르소나 설정 객체(PersonaConfig)를 작성하세요.

[작성 가이드라인]
- persona_id: '{persona_id}'
- display_name: '{candidate.name}'
- stakeholder_type: '{candidate.stakeholder_type}'
- relationship_to_topic: '{candidate.relationship_to_topic}'를 포함하여 명시하세요.
- importance_grade: 안건에서의 중요도 등급 ('A': 핵심, 'B': 주요, 'C': 참고, 'D': 단순관찰 중 하나)
- participation_type: 참여 유형 ('essential': 필수, 'optional': 선택, 'reference': 참고 중 하나)
- priorities: 핵심 우선순위 평가 기준 및 가중치 목록 (Priority: criterion, weight [합계 약 1.0])
- interests: 주요 관심사 2~4개
- concerns: 주요 우려사항 2~4개
- initial_position: 초기 입장 ('support', 'conditional_support', 'opposition', 'conditional_opposition' 중 하나)
- acceptable_conditions: 수용 가능한 조건 1~3개
- non_negotiable_conditions: 절대 타협/수용 불가능한 배제 조건 1~2개
- evidence_ids: 제공된 근거 ID 목록 중 선택 {evidence_ids}
- ordinance_chunk_ids: 제공된 조례 청크 ID 목록 중 선택 {ordinance_ids}"""

        user_prompt = f"주제: {topic}\n이해관계자 추천 사유: {candidate.recommendation_reason}\n\nPersonaConfig를 생성해 주세요."

        config = await structured_llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])
        
        # 5. 필수 ID 및 디스플레이 이름 정규화 보장
        config.persona_id = persona_id
        config.display_name = candidate.name
        config.stakeholder_type = candidate.stakeholder_type
        
        persona_configs.append(config)

    return persona_configs
