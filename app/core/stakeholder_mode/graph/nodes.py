# [이해관계자 페르소나 모드] LangGraph 7대 워크플로우 노드 함수 정의서
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
    """
    [노드 1: receive_input]
    감리 AI 정제 데이터 및 조례 RAG 결과를 수신하고 상태(State)에 파싱 및 저장합니다.
    """
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
    """
    [노드 2: recommend_stakeholders]
    LLM 서비스를 호출하여 안건 및 조례 기반 대표 이해관계자 3~5개를 자동 추천합니다.
    """
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
    """
    [노드 3: review_stakeholders]
    사용자 선택 및 수정 단계 (HITL).
    사용자가 수정한 selected_stakeholders가 있으면 이를 채택하고, 없으면 추천 목록을 기본 선택으로 사용합니다.
    """
    selected = state.get("selected_stakeholders")
    if not selected:
        selected = state.get("recommended_stakeholders", [])

    return {
        "selected_stakeholders": selected
    }


async def create_personas_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """
    [노드 4: create_personas]
    선택된 이해관계자를 바탕으로 관심사/우려/입장이 설정된 PersonaConfig 객체들을 생성합니다.
    """
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
    """
    [노드 5: render_prompts]
    생성된 PersonaConfig와 안건/조례 데이터를 Jinja2 템플릿 엔진에 바인딩하여 동적 프롬프트를 렌더링합니다.
    """
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
    """
    [노드 6: run_personas]
    각 페르소나별로 비동기 LLM 호출을 수행하여 독립적인 후보지 평가 결과(PersonaOpinion)를 수집합니다.
    """
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
    """
    [노드 7: aggregate_results]
    모든 페르소나의 독립 평가 결과를 하나의 구조화된 StakeholderModeResult JSON으로 종합 집계합니다.
    """
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
