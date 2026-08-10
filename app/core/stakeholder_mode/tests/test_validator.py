from app.core.stakeholder_mode.schemas.stakeholder import CandidateSite, OrdinanceContext
from app.core.stakeholder_mode.schemas.output import PersonaOpinion, CandidateScore
from app.core.stakeholder_mode.services.validator import validate_evidence_and_ordinances


def test_validator_detects_invalid_evidence():
    sites = [CandidateSite(candidate_id="SITE-A", name="후보지 A")]
    ordinances = [OrdinanceContext(chunk_id="ORD-001", ordinance_name="도시계획 조례", content="내용")]

    valid_opinion = PersonaOpinion(
        persona_id="PERSONA-001",
        stakeholder_name="주민",
        position="support",
        candidate_scores=[CandidateScore(candidate_id="SITE-A", score=90, reasoning="좋음")],
        evidence_ids=["SITE-A", "ORD-001"]
    )

    invalid_opinion = PersonaOpinion(
        persona_id="PERSONA-002",
        stakeholder_name="상인",
        position="opposition",
        candidate_scores=[CandidateScore(candidate_id="SITE-A", score=40, reasoning="나쁨")],
        evidence_ids=["INVALID-ID-999"]
    )

    issues = validate_evidence_and_ordinances(
        opinions=[valid_opinion, invalid_opinion],
        candidate_sites=sites,
        ordinance_contexts=ordinances
    )

    assert len(issues) == 1
    assert issues[0].persona_id == "PERSONA-002"
    assert issues[0].issue_type == "invalid_evidence_id"
    assert "INVALID-ID-999" in issues[0].message
