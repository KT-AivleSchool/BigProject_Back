# [이해관계자 페르소나 모드] 페르소나 독립 평가 실행 서비스
import asyncio
from typing import List
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import settings
from app.core.stakeholder_mode.schemas.persona import PersonaConfig
from app.core.stakeholder_mode.schemas.stakeholder import CandidateSite, OrdinanceContext
from app.core.stakeholder_mode.schemas.output import PersonaOpinion
from app.core.stakeholder_mode.prompts.renderer import (
    render_persona_system_prompt,
    render_initial_opinion_prompt
)


async def execute_persona_evaluation(
    persona: PersonaConfig,
    topic: str,
    candidate_sites: List[CandidateSite],
    ordinance_contexts: List[OrdinanceContext],
    model_name: str = "gpt-4o-mini"
) -> PersonaOpinion:
    """
    [단일 페르소나 독립 평가 함수]
    특정 PersonaConfig에 대해 Jinja2 프롬프트를 동적 렌더링하고
    LLM을 호출하여 정형화된 PersonaOpinion 평가 결과를 생성합니다.
    """
    # 1. LLM 클라이언트 인스턴스 생성
    llm = ChatOpenAI(
        api_key=settings.OPENAI_API_KEY,
        model=model_name,
        temperature=0.7
    )
    
    # 2. PersonaOpinion 구조화 응답 바인딩
    structured_llm = llm.with_structured_output(PersonaOpinion)

    # 3. Jinja2 템플릿 엔진을 활용한 시스템/유저 프롬프트 렌더링
    system_prompt_str = render_persona_system_prompt(persona)
    user_prompt_str = render_initial_opinion_prompt(
        persona=persona,
        topic=topic,
        candidate_sites=candidate_sites,
        ordinance_contexts=ordinance_contexts
    )

    # 4. LLM 비동기 호출
    opinion = await structured_llm.ainvoke([
        SystemMessage(content=system_prompt_str),
        HumanMessage(content=user_prompt_str)
    ])

    # 5. ID 및 이해관계자 명칭 정규화 보장
    opinion.persona_id = persona.persona_id
    opinion.stakeholder_name = persona.display_name

    return opinion


async def execute_all_personas(
    persona_configs: List[PersonaConfig],
    topic: str,
    candidate_sites: List[CandidateSite],
    ordinance_contexts: List[OrdinanceContext],
    model_name: str = "gpt-4o-mini"
) -> List[PersonaOpinion]:
    """
    [다중 페르소나 비동기 병렬 평가 실행 함수]
    asyncio.gather를 활용하여 등록된 모든 페르소나의 독립 평가를 동시에 실행하고
    의견 리스트로 모아서 반환합니다.
    """
    tasks = [
        execute_persona_evaluation(
            persona=persona,
            topic=topic,
            candidate_sites=candidate_sites,
            ordinance_contexts=ordinance_contexts,
            model_name=model_name
        )
        for persona in persona_configs
    ]
    opinions = await asyncio.gather(*tasks)
    return list(opinions)
