import pytest
from app.core.stakeholder_mode.schemas.stakeholder import (
    CandidateSite,
    OrdinanceContext,
    StakeholderCandidate
)
from app.core.stakeholder_mode.schemas.persona import PersonaConfig
from app.core.stakeholder_mode.schemas.output import PersonaOpinion, StakeholderModeResult


def test_schema_instantiation():
    """Pydantic 데이터 모델 파싱 및 유효성 검증 테스트"""
    candidate = StakeholderCandidate(
        name="후보지 인근 주민",
        stakeholder_type="resident",
        relationship_to_topic="생활환경 영향 받음",
        recommendation_reason="소음 및 교통량 변화 가능성"
    )
    assert candidate.name == "후보지 인근 주민"
    assert candidate.stakeholder_type == "resident"

    persona = PersonaConfig(
        persona_id="PERSONA-001",
        display_name="후보지 인근 주민",
        stakeholder_type="resident",
        relationship_to_topic="후보지 주위 거주",
        interests=["주거환경"],
        concerns=["소음"],
        initial_position="conditional_opposition",
        acceptable_conditions=["방음벽 설치"],
        evidence_ids=["SITE-A"],
        ordinance_chunk_ids=["ORD-001"]
    )
    assert persona.persona_id == "PERSONA-001"
    assert len(persona.interests) == 1

    opinion = PersonaOpinion(
        persona_id="PERSONA-001",
        stakeholder_name="후보지 인근 주민",
        preferred_candidate_id="SITE-B",
        position="conditional_support",
        benefits=["주변 환경 정비"],
        concerns=["소음 증가"],
        required_conditions=["운영시간 제한"],
        evidence_ids=["ORD-001"]
    )
    assert opinion.preferred_candidate_id == "SITE-B"

    result = StakeholderModeResult(
        project_id="PROJECT-001",
        topic="공공시설 후보지 선정",
        personas=[opinion]
    )
    assert result.project_id == "PROJECT-001"
    assert len(result.personas) == 1
