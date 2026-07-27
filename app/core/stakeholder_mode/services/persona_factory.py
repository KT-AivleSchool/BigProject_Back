# [이해관계자 페르소나 모드] PersonaConfig 생성 팩토리 서비스
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
    """
    [PersonaConfig 팩토리 서비스]
    사용자가 최종 선택/수정한 StakeholderCandidate 목록을 순회하며
    LLM을 통해 관심사, 우려사항, 초기 입장, 수용 조건 및 근거 ID가 매핑된 PersonaConfig 객체들을 생성합니다.
    """
    # 1. LLM 클라이언트 인스턴스 초기화
    llm = ChatOpenAI(
        api_key=settings.OPENAI_API_KEY,
        model=model_name,
        temperature=0.7
    )
    
    # 2. PersonaConfig Pydantic 모델에 맞춘 구조화 출력 바인딩
    structured_llm = llm.with_structured_output(PersonaConfig)
    
    # 3. 근거 ID 및 조례 ID 목록 생성
    evidence_ids = [site.candidate_id for site in candidate_sites]
    ordinance_ids = [ord_item.chunk_id for ord_item in ordinance_contexts]

    persona_configs: List[PersonaConfig] = []

    # 4. 각 이해관계자별로 PersonaConfig 객체 비동기 생성
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
        
        # 5. 필수 ID 및 디스플레이 이름 정규화 보장
        config.persona_id = persona_id
        config.display_name = candidate.name
        config.stakeholder_type = candidate.stakeholder_type
        
        persona_configs.append(config)

    return persona_configs
