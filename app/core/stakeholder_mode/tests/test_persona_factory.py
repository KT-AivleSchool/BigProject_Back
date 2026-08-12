from app.core.stakeholder_mode.schemas.stakeholder import (
    StakeholderCandidate
)
from app.core.stakeholder_mode.schemas.persona import PersonaConfig, Priority
from app.core.stakeholder_mode.schemas.output import (
    PersonaOpinion,
    CandidateScore,
    StakeholderModeResult
)


def test_phase2_schema_instantiation():
    """Phase 2 확장 데이터 모델 파싱 및 유효성 검증 테스트"""
    candidate = StakeholderCandidate(
        stakeholder_id="SH-001",
        display_name="후보지 인근 주민",
        constituency="주민",
        name="후보지 인근 주민",
        stakeholder_type="resident",
        relationship_to_topic="생활환경 영향 받음",
        recommendation_reason="소음 및 교통량 변화 가능성",
        relevance_score=0.95
    )
    assert candidate.name == "후보지 인근 주민"
    assert candidate.display_name == "후보지 인근 주민"



    persona = PersonaConfig(
        persona_id="PERSONA-001",
        display_name="후보지 인근 주민",
        stakeholder_type="resident",
        relationship_to_topic="후보지 주위 거주",
        importance_grade="A",
        participation_type="essential",
        priorities=[Priority(criterion="주거 소음 방지", weight=0.6)],
        interests=["주거환경"],
        concerns=["소음"],
        initial_position="conditional_opposition",
        acceptable_conditions=["방음벽 설치"],
        non_negotiable_conditions=["야간 공사 절대 불가"],
        evidence_ids=["SITE-A"],
        ordinance_chunk_ids=["ORD-001"]
    )
    assert persona.persona_id == "PERSONA-001"
    assert persona.importance_grade == "A"
    assert persona.priorities[0].weight == 0.6
    assert persona.non_negotiable_conditions[0] == "야간 공사 절대 불가"

    score = CandidateScore(candidate_id="SITE-B", score=85.0, reasoning="주거지와 멀어 소음 차단 우수")
    opinion = PersonaOpinion(
        persona_id="PERSONA-001",
        stakeholder_name="후보지 인근 주민",
        preferred_candidate_id="SITE-B",
        position="conditional_support",
        candidate_scores=[score],
        benefits=["주변 환경 정비"],
        concerns=["소음 증가"],
        required_conditions=["운영시간 제한"],
        non_negotiable_violations=[],
        evidence_ids=["SITE-B", "ORD-001"]
    )
    assert opinion.preferred_candidate_id == "SITE-B"
    assert opinion.candidate_scores[0].score == 85.0

    result = StakeholderModeResult(
        project_id="PROJECT-001",
        topic="공공시설 후보지 선정",
        personas=[opinion],
        validation_issues=[]
    )
    assert result.project_id == "PROJECT-001"
    assert len(result.personas) == 1
