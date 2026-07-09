import os
from pathlib import Path
from typing import Any, Dict
from jinja2 import Environment, FileSystemLoader, ChoiceLoader, DictLoader, TemplateNotFound

# =====================================================================
# 1. OS 독립적 절대 경로(Pathlib) 설정 (Directory Setup)
# =====================================================================
# pathlib.Path를 사용하여 OS별 경로 구분자(/ vs \) 이슈 및 실행 디렉토리(Uvicorn Cwd) 종속성 차단
BASE_DIR = Path(__file__).resolve().parent.parent  # app/
TEMPLATES_DIR = BASE_DIR / "templates"
WORKSPACE_TEMPLATES_DIR = BASE_DIR.parent / "templates"

# 폴더 자동 생성 (예외 방지)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# =====================================================================
# 2. 파일 로더 우선순위 체계 구축 및 UTF-8 인코딩 강제 (Template Loaders)
# =====================================================================
# [★중요] Windows 환경(CP949)과 Linux 환경(UTF-8)에서 한글 깨짐 및 인코딩 오류가 발생하지 않도록
# encoding="utf-8"을 명시적으로 주입합니다.
loaders = [
    FileSystemLoader(str(TEMPLATES_DIR), encoding="utf-8"),            # 1순위: app/templates/
    FileSystemLoader(str(WORKSPACE_TEMPLATES_DIR), encoding="utf-8"),  # 2순위: 프로젝트 루트/templates/
    DictLoader({})                                                      # 3순위: Fallback
]

# =====================================================================
# 3. 글로벌 Jinja2 환경 선언 및 커스텀 문법 결합 (Environment Config)
# =====================================================================
# JSON 중괄호({})와의 충돌 방지를 위해 [[ ]] 및 [% %] 문법 적용
jinja2_env = Environment(
    loader=ChoiceLoader(loaders),
    variable_start_string='[[',
    variable_end_string=']]',
    block_start_string='[%',
    block_end_string='%]',
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)

# =====================================================================
# 4. 프롬프트 전용 커스텀 유틸리티 필터 (Jinja2 Filters)
# =====================================================================
def json_filter(value: Any) -> str:
    """객체를 한글 깨짐 없이 예쁜 JSON 포맷으로 직렬화"""
    import json
    return json.dumps(value, ensure_ascii=False, indent=2)

jinja2_env.filters["json"] = json_filter

# =====================================================================
# 5. 프롬프트 렌더링 인터페이스 함수들 (Rendering Utilities)
# =====================================================================
def render_template(
    template_name: str, 
    context: Dict[str, Any] = None,
    locale: str = "ko",
    theme: str = "default"
) -> str:
    """
    [다국어 및 테마 지원 파일 기반 렌더러]
    주어진 locale과 theme에 해당하는 최적의 경로의 템플릿 파일을 탐색하고 렌더링합니다.
    매칭되는 경로가 없을 경우 최하위 default/ 폴더로 순차적 폴백(Fallback)을 수행합니다.
    """
    if context is None:
        context = {}

    # 다국어(locale) 및 테마(theme) 탐색 우선순위 후보 리스트 생성
    candidate_paths = [
        f"{locale}/{theme}/{template_name}",
        f"default/{theme}/{template_name}",
        f"{locale}/default/{template_name}",
        f"default/default/{template_name}",
        f"default/{template_name}",
        template_name
    ]

    last_exception = None
    for path in candidate_paths:
        try:
            template = jinja2_env.get_template(path)
            return template.render(**context)
        except TemplateNotFound as e:
            last_exception = e
            continue

    raise last_exception or TemplateNotFound(template_name)


def render_template_string(template_str: str, **context: Any) -> str:
    """
    [코드 내 문자열 기반 렌더러]
    텍스트 프롬프트 스니펫에 데이터를 매핑하여 렌더링합니다.
    """
    template = jinja2_env.from_string(template_str)
    return template.render(**context)


# =====================================================================
# 6. 멀티에이전트 토큰 비용 절감용 하이브리드 빌더 (Prompt Caching Builder)
# =====================================================================
def build_agent_prompt_cached(
    common_system_prompt: str,
    role_prompt: str,
    site_information: str,
    rag_context: str,
    css_instruction: str,
    discussion_history: str
) -> str:
    """
    OpenAI/Claude 등의 프롬프트 캐싱(Prompt Caching) 최적화 순서에 맞추어 
    시스템 프롬프트를 조립하고 렌더링합니다.
    """
    cache_friendly_prefix = f"""{common_system_prompt}

[시설 및 입지 정보]
{site_information}

[관련 법률 및 조례 (RAG)]
{rag_context}

[당신의 페르소나 및 역할 가이드]
{role_prompt}"""

    dynamic_suffix = f"""

[현재 갈등 단계에 따른 스탠스 지침]
{css_instruction}

[이전 토론 내용 이력]
{discussion_history}"""

    return render_template_string(
        cache_friendly_prefix + dynamic_suffix,
        site_information=site_information,
        rag_context=rag_context,
        discussion_history=discussion_history
    )
