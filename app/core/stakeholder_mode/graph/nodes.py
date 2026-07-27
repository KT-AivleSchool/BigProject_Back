from typing import List, Dict, Any

from app.core.stakeholder_mode.graph.state import StakeholderGraphState
from app.core.stakeholder_mode.schemas.stakeholder import (
    CandidateSite,
    OrdinanceContext,
    StakeholderCandidate
)
from app.core.stakeholder_mode.schemas.persona import PersonaConfig
from app.core.stakeholder_mode.schemas.output import PersonaOpinion, StakeholderModeResult
from app.core.stakeholder_mode.services.stakeholder_recommender import recommend_stakeholders
from app.core.stakeholder_mode.services.persona_factory import create_persona_configs
from app.core.stakeholder_mode.services.persona_executor import execute_all_personas
from app.core.stakeholder_mode.prompts.renderer import (
    render_persona_system_prompt,
    render_initial_opinion_prompt
)


async def receive_input_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """1. 감리 AI 데이터 및 조례 결과 수신/파싱 노드"""
    project_id = state.get("project_id", "PROJECT-001")
    topic = state.get("topic", "공공시설 후보지 선정")
    candidate_sites = state.get("candidate_sites", [])
    ordinance_contexts = state.get("ordinance_contexts", [])

    return {
        "project_id": project_id,
        "topic": topic,
        "candidate_sites": candidate_sites,
        "ordinance_contexts": ordinance_contexts
    }


async def recommend_stakeholders_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """2. 이해관계자 3~5개 추천 노드"""
    topic = state["topic"]
    sites = [CandidateSite(**s) for s in state.get("candidate_sites", [])]
    ords = [OrdinanceContext(**o) for o in state.get("ordinance_contexts", [])]

    candidates: List[StakeholderCandidate] = await recommend_stakeholders(
        topic=topic,
        candidate_sites=sites,
        ordinance_contexts=ords
    )

    return {
        "recommended_stakeholders": [c.model_dump() for c in candidates]
    }


async def review_stakeholders_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """3. 사용자 선택 및 수정 (HITL 통과/기본 통과 노드)"""
    selected = state.get("selected_stakeholders")
    if not selected:
        selected = state.get("recommended_stakeholders", [])

    return {
        "selected_stakeholders": selected
    }


async def create_personas_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """4. PersonaConfig 생성 노드"""
    topic = state["topic"]
    selected_candidates = [StakeholderCandidate(**s) for s in state.get("selected_stakeholders", [])]
    sites = [CandidateSite(**s) for s in state.get("candidate_sites", [])]
    ords = [OrdinanceContext(**o) for o in state.get("ordinance_contexts", [])]

    persona_configs: List[PersonaConfig] = await create_persona_configs(
        topic=topic,
        selected_stakeholders=selected_candidates,
        candidate_sites=sites,
        ordinance_contexts=ords
    )

    return {
        "persona_configs": [p.model_dump() for p in persona_configs]
    }


async def render_prompts_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """5. Jinja2 프롬프트 렌더링 노드"""
    topic = state["topic"]
    persona_configs = [PersonaConfig(**p) for p in state.get("persona_configs", [])]
    sites = [CandidateSite(**s) for s in state.get("candidate_sites", [])]
    ords = [OrdinanceContext(**o) for o in state.get("ordinance_contexts", [])]

    rendered_prompts: Dict[str, Dict[str, str]] = {}
    for persona in persona_configs:
        sys_prompt = render_persona_system_prompt(persona)
        usr_prompt = render_initial_opinion_prompt(
            persona=persona,
            topic=topic,
            candidate_sites=sites,
            ordinance_contexts=ords
        )
        rendered_prompts[persona.persona_id] = {
            "system": sys_prompt,
            "user": usr_prompt
        }

    return {
        "rendered_prompts": rendered_prompts
    }


async def run_personas_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """6. 페르소나별 독립 평가 노드"""
    topic = state["topic"]
    persona_configs = [PersonaConfig(**p) for p in state.get("persona_configs", [])]
    sites = [CandidateSite(**s) for s in state.get("candidate_sites", [])]
    ords = [OrdinanceContext(**o) for o in state.get("ordinance_contexts", [])]

    opinions: List[PersonaOpinion] = await execute_all_personas(
        persona_configs=persona_configs,
        topic=topic,
        candidate_sites=sites,
        ordinance_contexts=ords
    )

    return {
        "opinions": [op.model_dump() for op in opinions]
    }


async def aggregate_results_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """7. 결과 집계 노드"""
    project_id = state["project_id"]
    topic = state["topic"]
    opinions = [PersonaOpinion(**op) for op in state.get("opinions", [])]

    result = StakeholderModeResult(
        project_id=project_id,
        topic=topic,
        personas=opinions
    )

    return {
        "final_result": result.model_dump()
    }
