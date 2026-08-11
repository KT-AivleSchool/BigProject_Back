# [이해관계자 페르소나 모드] Phase 2 확장 LangGraph 7대 노드 함수 정의서
from typing import List, Dict, Any

from app.core.stakeholder_mode.graph.state import StakeholderGraphState
from app.core.stakeholder_mode.schemas.stakeholder import (
    CandidateSite,
    OrdinanceContext,
    StakeholderCandidate
)
from app.core.stakeholder_mode.schemas.persona import PersonaConfig
from app.core.stakeholder_mode.schemas.output import (
    PersonaOpinion,
    StakeholderModeResult,
    ValidationIssue
)
from app.core.stakeholder_mode.services.stakeholder_recommender import recommend_stakeholders
from app.core.stakeholder_mode.services.persona_factory import create_persona_configs
from app.core.stakeholder_mode.services.persona_executor import execute_all_personas
from app.core.stakeholder_mode.services.validator import validate_evidence_and_ordinances
from app.core.stakeholder_mode.prompts.renderer import (
    render_persona_system_prompt,
    render_initial_opinion_prompt
)


async def receive_input_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """1. 감리 AI 데이터 및 조례 결과 수신/파싱 노드

    🔴 2026-08-11. `project_id`·`topic` 에 `"PROJECT-001"`·`"공공시설 후보지 선정"`
       기본값이 있었다. 안건이 안 실려 오면 **그 문구로 토론이 완주한다** — 안 터지고
       주제만 틀린다. 없으면 멈춘다(원칙 1).
    """
    project_id = state.get("project_id")
    topic = state.get("topic")
    if not project_id or not topic:
        raise ValueError(
            "receive_input_node: project_id·topic 이 필요하다 "
            f"(project_id={project_id!r}, topic={topic!r}). 기본 안건으로 대신하지 않는다."
        )
    candidate_sites = state.get("candidate_sites", [])
    ordinance_contexts = state.get("ordinance_contexts", [])

    return {
        "project_id": project_id,
        "topic": topic,
        "candidate_sites": candidate_sites,
        "ordinance_contexts": ordinance_contexts
    }


async def recommend_stakeholders_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """2. 연관성 순 이해관계자 3~5개 추천 노드"""
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


async def generate_interest_profiles_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """2-1. 이해관계자별 프로필 생성 노드"""
    from app.core.stakeholder_mode.services.interest_profile_generator import InterestProfileGenerator
    from app.core.stakeholder_mode.graph.dynamic_nodes import get_llm_fast

    topic = state["topic"]
    candidates = [StakeholderCandidate(**c) for c in state.get("recommended_stakeholders", [])]
    candidate_sites = state.get("candidate_sites", [])
    ordinance_contexts = state.get("ordinance_contexts", [])

    generator = InterestProfileGenerator(llm_client=get_llm_fast())
    profiles = []
    
    for stakeholder in candidates:
        profile = await generator.generate(
            topic=topic,
            stakeholder=stakeholder,
            candidate_contexts=candidate_sites,
            ordinance_contexts=ordinance_contexts
        )
        profiles.append(profile.model_dump())

    return {
        "interest_profiles": profiles
    }


async def validate_persona_diversity_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """2-2. 페르소나 차별성 검사 및 보정 노드"""
    from app.core.stakeholder_mode.services.persona_diversity_validator import PersonaDiversityValidator
    from app.core.stakeholder_mode.schemas.interest_profile import StakeholderInterestProfile
    
    profiles = [StakeholderInterestProfile(**p) for p in state.get("interest_profiles", [])]
    validator = PersonaDiversityValidator()
    issues = validator.validate(profiles)

    # MVP에서는 경고/에러 내역만 상태에 저장하고 넘어감. (필요 시 LLM 재귀 호출 로직 추가 가능)
    return {
        "diversity_issues": issues
    }

async def review_stakeholders_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """3. 사용자 선택 및 수정 (HITL 노드)"""
    selected = state.get("selected_stakeholders")
    if not selected:
        selected = state.get("recommended_stakeholders", [])

    return {
        "selected_stakeholders": selected
    }


async def create_personas_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """4. Phase 2 고도화된 PersonaConfig 생성 노드"""
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


async def select_persona_contexts_node(state: StakeholderGraphState) -> Dict[str, Any]:
    """4-1. 페르소나별 관련 데이터(Context) 선택 노드"""
    from app.core.stakeholder_mode.services.persona_context_selector import PersonaContextSelector
    from app.core.stakeholder_mode.schemas.persona import PersonaConfig
    from app.core.stakeholder_mode.schemas.context import CandidateContext
    
    # 임시 CandidateContext 파싱 로직 (기존 Dict를 CandidateContext로 변환 필요)
    candidate_sites = state.get("candidate_sites", [])
    
    contexts = []
    for site in candidate_sites:
        # 안전한 변환 (더미 데이터 생성 혹은 파싱)
        contexts.append(CandidateContext(
            candidate_id=site.get("candidate_id", ""),
            name=site.get("name", ""),
            spatial_facts=[],
            restrictions=[],
            transportation=[],
            operational_facts=[]
        ))
        
    selector = PersonaContextSelector()
    persona_configs = [PersonaConfig(**p) for p in state.get("persona_configs", [])]
    
    persona_contexts = {}
    for persona in persona_configs:
        # 첫 번째 후보지만 있다고 가정하거나 루프
        if contexts:
            persona_contexts[persona.persona_id] = selector.select(persona, contexts[0])
            
    return {
        "persona_contexts": persona_contexts
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
    """6. 페르소나별 독립 평가 실행 노드 (후보지별 점수 포함)"""
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
    """7. 결과 집계 및 근거 검증(Validation) 노드"""
    project_id = state["project_id"]
    topic = state["topic"]
    sites = [CandidateSite(**s) for s in state.get("candidate_sites", [])]
    ords = [OrdinanceContext(**o) for o in state.get("ordinance_contexts", [])]
    opinions = [PersonaOpinion(**op) for op in state.get("opinions", [])]

    # 근거 및 조례 ID 검증 실행
    validation_issues: List[ValidationIssue] = validate_evidence_and_ordinances(
        opinions=opinions,
        candidate_sites=sites,
        ordinance_contexts=ords
    )

    result = StakeholderModeResult(
        project_id=project_id,
        topic=topic,
        personas=opinions,
        validation_issues=validation_issues
    )

    return {
        "validation_issues": [issue.model_dump() for issue in validation_issues],
        "final_result": result.model_dump()
    }
