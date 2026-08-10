# [이해관계자 페르소나 모드] Jinja2 프롬프트 렌더링 헬퍼 모듈
from pathlib import Path
from typing import Any, Dict, List
from jinja2 import Environment, FileSystemLoader

from app.core.stakeholder_mode.schemas.persona import PersonaConfig
from app.core.stakeholder_mode.schemas.stakeholder import CandidateSite, OrdinanceContext

# 현재 파일 기준으로 templates 디렉터리의 절대 경로 설정
TEMPLATES_DIR = Path(__file__).parent / "templates"

# Jinja2 템플릿 환경 객체 초기화 (템플릿 디렉터리 바인딩 및 불필요한 공백 제거 옵션 활성화)
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True
)


def render_persona_system_prompt(persona: PersonaConfig) -> str:
    """
    [페르소나 시스템 프롬프트 렌더링 함수]
    PersonaConfig 객체 데이터를 Jinja2 persona_system.j2 템플릿에 주입하여
    해당 페르소나의 역할 및 입장 시스템 프롬프트 문자열을 생성합니다.
    """
    template = jinja_env.get_template("persona_system.j2")
    return template.render(persona=persona)


def render_initial_opinion_prompt(
    persona: PersonaConfig,
    topic: str,
    candidate_sites: List[CandidateSite],
    ordinance_contexts: List[OrdinanceContext]
) -> str:
    """
    [페르소나 유저 평가 프롬프트 렌더링 함수]
    안건 주제, 후보지 속성 데이터, 조례 Context 목록을 Jinja2 initial_opinion.j2 템플릿에 안전하게 바인딩하여
    페르소나가 독립적인 후보지 평가를 작성하도록 지시하는 유저 프롬프트를 렌더링합니다.
    """
    template = jinja_env.get_template("initial_opinion.j2")
    
    # CandidateSite 및 OrdinanceContext 객체를 템플릿 내 json 바인딩을 위해 dict 형식으로 변환
    sites_data = [site.model_dump() if hasattr(site, "model_dump") else site for site in candidate_sites]
    ord_data = [ord_item.model_dump() if hasattr(ord_item, "model_dump") else ord_item for ord_item in ordinance_contexts]

    return template.render(
        persona=persona,
        topic=topic,
        candidate_sites=sites_data,
        ordinance_contexts=ord_data
    )
