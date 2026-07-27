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
    """단일 페르소나의 독립 평가를 수행합니다."""
    llm = ChatOpenAI(
        api_key=settings.OPENAI_API_KEY,
        model=model_name,
        temperature=0.7
    )
    
    structured_llm = llm.with_structured_output(PersonaOpinion)

    system_prompt_str = render_persona_system_prompt(persona)
    user_prompt_str = render_initial_opinion_prompt(
        persona=persona,
        topic=topic,
        candidate_sites=candidate_sites,
        ordinance_contexts=ordinance_contexts
    )

    opinion = await structured_llm.ainvoke([
        SystemMessage(content=system_prompt_str),
        HumanMessage(content=user_prompt_str)
    ])

    # ID 및 이름 메타데이터 보장
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
    """모든 페르소나의 독립 평가를 비동기 병렬로 실행합니다."""
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
