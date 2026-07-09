# [동현님 담당] AI 페르소나별 시스템 프롬프트 정의서 (템플릿 기반 및 완전 일반화 버전)
# 다목적 플랫폼(OmniSite) 철학에 따라, topic 기반의 하드코딩된 분기를 제거하고 
# RAG 컨텍스트와 AI 감리 판단에 의해 동적으로 프롬프트가 적용되도록 일반화(Pro/Con/Gov) 하였습니다.

from app.core.jinja2_env import render_template

# 글로벌 프롬프트 로드 (변수가 없는 정적 텍스트들은 미리 로드)
PRO_ROLE_PROMPT = render_template("pro_role.txt")
CON_ROLE_PROMPT = render_template("con_role.txt")
GOV_ROLE_PROMPT = render_template("gov_role.txt")
EVALUATOR_PROMPT = render_template("evaluator.txt")
REPORTER_PROMPT = render_template("reporter.txt")

CSS_PROMPT_TEMPLATE = {
    "HIGH": render_template("css_high.txt"),
    "MEDIUM": render_template("css_medium.txt"),
    "LOW": render_template("css_low.txt"),
}


def build_prompt(
    role_prompt: str,
    candidate_jibun: str,
    candidate_lat: float,
    candidate_lng: float,
    facility_type: str,
    intensity_level: str,
    ahp_weights: dict,
    rag_context: str,
    discussion_history: str,
    css_level: str,
) -> str:
    """
    공통 시스템 프롬프트 + CSS + 역할 프롬프트를 하나의 최종 Prompt로 생성합니다.
    """
    # AHP 가중치를 문자열로 예쁘게 포맷팅
    ahp_str = (
        "\n".join([f"  * {k}: {v}" for k, v in ahp_weights.items()])
        if ahp_weights
        else "  * 데이터 없음"
    )

    # 1. 공통 시스템 프롬프트 템플릿 로드 (호출될 때마다 동적으로 변수 주입)
    rendered_common = render_template(
        "common_system_prompt.txt",
        context={
            "candidate_jibun": candidate_jibun,
            "candidate_lat": candidate_lat,
            "candidate_lng": candidate_lng,
            "facility_type": facility_type,
            "intensity_level": intensity_level,
            "ahp_weights": ahp_str,
            "rag_context": rag_context,
            "discussion_history": discussion_history,
        }
    )

    # 2. 갈등 민감도별 행동 지침 템플릿 로드
    css_instruction = CSS_PROMPT_TEMPLATE.get(css_level.upper(), CSS_PROMPT_TEMPLATE["HIGH"])

    # 최종 프롬프트 빌드
    return f"""
{rendered_common}

{css_instruction}

{role_prompt}
"""
