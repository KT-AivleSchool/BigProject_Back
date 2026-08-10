from app.core.stakeholder_mode.schemas.persona import PersonaConfig
from app.core.stakeholder_mode.schemas.stakeholder import CandidateSite, OrdinanceContext
from app.core.stakeholder_mode.prompts.renderer import (
    render_persona_system_prompt,
    render_initial_opinion_prompt
)


def test_render_persona_system_prompt():
    persona = PersonaConfig(
        persona_id="PERSONA-001",
        display_name="후보지 인근 주민",
        stakeholder_type="resident",
        relationship_to_topic="후보지 주변 거주하여 소음 및 교통 영향받음",
        interests=["주거환경", "소음 예방"],
        concerns=["차량 증가", "소음"],
        initial_position="conditional_opposition",
        acceptable_conditions=["차량 진입로 분리", "운영시간 제한"],
        evidence_ids=["SITE-A"],
        ordinance_chunk_ids=["ORD-001"]
    )

    rendered = render_persona_system_prompt(persona)

    assert "후보지 인근 주민" in rendered
    assert "주거환경" in rendered
    assert "차량 진입로 분리" in rendered
    assert "공식 의견인 것처럼 표현하지 마세요" in rendered


def test_render_initial_opinion_prompt():
    persona = PersonaConfig(
        persona_id="PERSONA-001",
        display_name="후보지 인근 주민",
        stakeholder_type="resident",
        relationship_to_topic="주거환경 영향 받음",
        interests=["소음"],
        concerns=["교통"],
        initial_position="conditional_opposition",
        acceptable_conditions=["방음벽 설치"],
        evidence_ids=["SITE-A"],
        ordinance_chunk_ids=["ORD-001"]
    )

    sites = [
        CandidateSite(candidate_id="SITE-A", name="후보지 A", attributes={"accessibility": 80}),
        CandidateSite(candidate_id="SITE-B", name="후보지 B", attributes={"accessibility": 60})
    ]

    ordinances = [
        OrdinanceContext(chunk_id="ORD-001", ordinance_name="도시계획 조례", content="소음 방지 거리 확보")
    ]

    rendered = render_initial_opinion_prompt(
        persona=persona,
        topic="공공시설 후보지 선정",
        candidate_sites=sites,
        ordinance_contexts=ordinances
    )

    assert "공공시설 후보지 선정" in rendered
    assert "후보지 A" in rendered
    assert "도시계획 조례" in rendered
    assert "소음 방지 거리 확보" in rendered
