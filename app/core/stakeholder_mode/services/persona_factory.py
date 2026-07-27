import uuid
from typing import List
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import settings
from app.core.stakeholder_mode.schemas.stakeholder import (
    CandidateSite,
    OrdinanceContext,
    StakeholderCandidate
)
from app.core.stakeholder_mode.schemas.persona import PersonaConfig


async def create_persona_configs(
    topic: str,
    selected_stakeholders: List[StakeholderCandidate],
    candidate_sites: List[CandidateSite],
    ordinance_contexts: List[OrdinanceContext],
    model_name: str = "gpt-4o-mini"
) -> List[PersonaConfig]:
    """선택 및 수정된 StakeholderCandidate 목록을 받아 각각 상세 PersonaConfig로 변환합니다."""
    llm = ChatOpenAI(
        api_key=settings.OPENAI_API_KEY,
        model=model_name,
        temperature=0.7
    )
    
    structured_llm = llm.with_structured_output(PersonaConfig)
    
    evidence_ids = [site.candidate_id for site in candidate_sites]
    ordinance_ids = [ord_item.chunk_id for ord_item in ordinance_contexts]

    persona_configs: List[PersonaConfig] = []

    for index, candidate in enumerate(selected_stakeholders, start=1):
        persona_id = f"PERSONA-{index:03d}"
        
        system_prompt = f"""당신은 AI 페르소나 설계 전문가입니다.
주어진 이해관계자 정보({candidate.name}, 유형: {candidate.stakeholder_type})와 토론 주제를 바탕으로, 해당 이해관계자의 가상 페르소나 설정 객체(PersonaConfig)를 구체적으로 작성하세요.

- persona_id: '{persona_id}'로 지정하세요.
- display_name: '{candidate.name}'로 지정하세요.
- stakeholder_type: '{candidate.stakeholder_type}'로 지정하세요.
- relationship_to_topic: '{candidate.relationship_to_topic}'를 포함하여 자세히 명시하세요.
- interests: 관심사 2~4개
- concerns: 우려사항 2~4개
- initial_position: 초기 입장 ('support', 'conditional_support', 'opposition', 'conditional_opposition' 중 하나)
- acceptable_conditions: 수용 가능한 조건 1~3개
- evidence_ids: 제공된 근거 ID 목록 중 관련 ID 선택 {evidence_ids}
- ordinance_chunk_ids: 제공된 조례 청크 ID 목록 중 관련 ID 선택 {ordinance_ids}"""

        user_prompt = f"주제: {topic}\n이해관계자 추천 사유: {candidate.recommendation_reason}\n\nPersonaConfig를 생성해 주세요."

        config = await structured_llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])
        
        # ID 및 기본값 보장
        config.persona_id = persona_id
        config.display_name = candidate.name
        config.stakeholder_type = candidate.stakeholder_type
        
        persona_configs.append(config)

    return persona_configs
