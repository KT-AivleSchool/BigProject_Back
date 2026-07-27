from pathlib import Path
from typing import Any, Dict, List
from jinja2 import Environment, FileSystemLoader

from app.core.stakeholder_mode.schemas.persona import PersonaConfig
from app.core.stakeholder_mode.schemas.stakeholder import CandidateSite, OrdinanceContext

TEMPLATES_DIR = Path(__file__).parent / "templates"

jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True
)


def render_persona_system_prompt(persona: PersonaConfig) -> str:
    """PersonaConfig 객체를 받아 Jinja2 persona_system.j2 템플릿을 렌더링합니다."""
    template = jinja_env.get_template("persona_system.j2")
    return template.render(persona=persona)


def render_initial_opinion_prompt(
    persona: PersonaConfig,
    topic: str,
    candidate_sites: List[CandidateSite],
    ordinance_contexts: List[OrdinanceContext]
) -> str:
    """주제, 후보지 데이터, 조례 및 PersonaConfig를 바탕으로 Jinja2 initial_opinion.j2 템플릿을 렌더링합니다."""
    template = jinja_env.get_template("initial_opinion.j2")
    
    # CandidateSite, OrdinanceContext 객체를 dict 표현으로 변환하여 안전하게 바인딩
    sites_data = [site.model_dump() if hasattr(site, "model_dump") else site for site in candidate_sites]
    ord_data = [ord_item.model_dump() if hasattr(ord_item, "model_dump") else ord_item for ord_item in ordinance_contexts]

    return template.render(
        persona=persona,
        topic=topic,
        candidate_sites=sites_data,
        ordinance_contexts=ord_data
    )
