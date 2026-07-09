#  AI 페르소나별 시스템 프롬프트 정의서
# 프롬프트의 뼈대 구조만 남기고 상세 텍스트는 app/templates/ 경로 아래의 개별 텍스트 파일로 위임합니다.
# 주제(topic)별 폴더를 활용하여 페르소나와 프롬프트를 동적으로 변경할 수 있으며, 없을 시 default 폴더로 Fallback 처리합니다.

from app.core.jinja2_env import render_template, render_template_string
from jinja2.exceptions import TemplateNotFound

# 기본 Fallback 제공용 글로벌 프롬프트 (기존 코드와의 호환성을 유지하기 위한 로드)
COMMON_SYSTEM_PROMPT = render_template("common_system_prompt.txt", locale="ko", theme="default")
RESIDENT_ROLE_PROMPT = render_template("resident_role.txt", locale="ko", theme="default")
MERCHANT_ROLE_PROMPT = render_template("merchant_role.txt", locale="ko", theme="default")
OFFICER_ROLE_PROMPT = render_template("officer_role.txt", locale="ko", theme="default")
EVALUATOR_PROMPT = render_template("evaluator.txt", locale="ko", theme="default")
REPORTER_PROMPT = render_template("reporter.txt", locale="ko", theme="default")

CSS_PROMPT_TEMPLATE = {
    "HIGH": render_template("css_high.txt", locale="ko", theme="default"),
    "MEDIUM": render_template("css_medium.txt", locale="ko", theme="default"),
    "LOW": render_template("css_low.txt", locale="ko", theme="default"),
}


def get_template_with_fallback(topic: str, filename: str, locale: str = "ko") -> str:
    """
    지정된 주제(topic) 하위의 템플릿 파일을 로드합니다.
    새로운 render_template의 theme 파라미터에 topic을 전달하여 Locale 및 Theme 동적 스왑을 지원합니다.
    """
    return render_template(filename, locale=locale, theme=topic)


def build_prompt(
    role_prompt: str,  # "resident", "merchant", "officer" 키워드 또는 실제 프롬프트 문자열
    site_information: str,  # 입지 정보
    rag_context: str,  # 법률, 조례 RAG 데이터
    discussion_history: str,  # 이전 라운드 대화 내용
    css_level: str,  # 갈등 강도 변수 (HIGH / MEDIUM / LOW)
    topic: str = "default",  # 토론 주제 (예: smoking_booth, ev_charging 등)
    locale: str = "ko",  # 다국어 지원 파라미터
) -> str:
    """
    공통 시스템 프롬프트 + CSS + 역할 프롬프트를 하나의 최종 Prompt로 생성합니다.
    Jinja2의 [[ ]] 문법 렌더러를 사용하여 프롬프트를 동적으로 조립합니다.
    """
    # 1. 역할(Role) 프롬프트 결정
    role_key = str(role_prompt).lower().strip()
    
    # 키워드 매칭 또는 전역 변수 문자열 매칭 시 동적 로드
    if "resident" in role_key:
        role_prompt_content = get_template_with_fallback(topic, "resident_role.txt", locale=locale)
    elif "merchant" in role_key:
        role_prompt_content = get_template_with_fallback(topic, "merchant_role.txt", locale=locale)
    elif "officer" in role_key:
        role_prompt_content = get_template_with_fallback(topic, "officer_role.txt", locale=locale)
    else:
        # 매칭되지 않는 완전한 프롬프트 문자열일 경우 그대로 전달받아 사용
        role_prompt_content = role_prompt

    # 2. 공통 시스템 프롬프트 템플릿 로드 (주제별 오버라이드 지원)
    common_template = get_template_with_fallback(topic, "common_system_prompt.txt", locale=locale)

    rendered_common = render_template_string(
        common_template,
        site_information=site_information,
        rag_context=rag_context,
        discussion_history=discussion_history,
    )

    # 3. 갈등 민감도별 행동 지침 템플릿 로드 (주제별 오버라이드 지원)
    css_filename = f"css_{css_level.lower()}.txt"
    css_instruction = get_template_with_fallback(topic, css_filename, locale=locale)

    # 최종 프롬프트 빌드
    return f"""
{rendered_common}

{css_instruction}

{role_prompt_content}
"""
