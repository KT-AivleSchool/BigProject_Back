import pytest
from app.core.stakeholder_mode.schemas.stakeholder import StakeholderCandidate
from app.core.stakeholder_mode.schemas.interest_profile import StakeholderInterestProfile, DecisionAuthority, RiskTolerance
from app.core.stakeholder_mode.services.persona_diversity_validator import PersonaDiversityValidator
from app.core.stakeholder_mode.services.persona_context_selector import PersonaContextSelector
from app.core.stakeholder_mode.schemas.persona import PersonaConfig
from app.core.stakeholder_mode.schemas.context import CandidateContext, SpatialFact

def test_interest_profiles_have_benefit_and_cost():
    profiles = [
        StakeholderInterestProfile(
            stakeholder_id="test-1",
            primary_goal="Goal 1",
            success_definition=[],
            expected_benefits=["이익"],
            expected_costs=["비용"],
            major_risks=[],
            protected_interests=[],
            red_lines=[],
            negotiable_conditions=[],
            spatial_focus_tags=[],
            required_evidence_types=[],
            preferred_metrics=[],
            unique_questions=["질문"],
            decision_authority=DecisionAuthority.AFFECTED,
            risk_tolerance=RiskTolerance.LOW,
            time_horizon="단기",
            likely_initial_position="반대",
            position_reason="이유"
        )
    ]
    validator = PersonaDiversityValidator()
    issues = validator.validate(profiles)
    
    # 이익과 비용이 있으므로 에러가 나지 않아야 함
    assert len(issues) == 0


def test_primary_goals_are_not_all_identical():
    profiles = [
        StakeholderInterestProfile(
            stakeholder_id="test-1", primary_goal="주거 환경 보호", success_definition=[], expected_benefits=["1"], expected_costs=["1"], major_risks=[], protected_interests=[], red_lines=[], negotiable_conditions=[], spatial_focus_tags=[], required_evidence_types=[], preferred_metrics=[], unique_questions=["1"], decision_authority=DecisionAuthority.AFFECTED, risk_tolerance=RiskTolerance.LOW, time_horizon="단기", likely_initial_position="반대", position_reason="이유"
        ),
        StakeholderInterestProfile(
            stakeholder_id="test-2", primary_goal="주거 환경 보호", success_definition=[], expected_benefits=["1"], expected_costs=["1"], major_risks=[], protected_interests=[], red_lines=[], negotiable_conditions=[], spatial_focus_tags=[], required_evidence_types=[], preferred_metrics=[], unique_questions=["1"], decision_authority=DecisionAuthority.AFFECTED, risk_tolerance=RiskTolerance.LOW, time_horizon="단기", likely_initial_position="반대", position_reason="이유"
        )
    ]
    validator = PersonaDiversityValidator()
    issues = validator.validate(profiles)
    
    # 동일 목표 경고가 있어야 함
    assert any(i["issue_type"] == "duplicate_goal" for i in issues)


def test_resident_and_operator_receive_different_contexts():
    candidate = CandidateContext(
        candidate_id="SITE-1",
        name="테스트 부지",
        spatial_facts=[
            SpatialFact(fact_id="1", category="주거지", name="아파트", candidate_id="SITE-1", tags=["주거", "민감"], source_id="1"),
            SpatialFact(fact_id="2", category="관광지", name="안내소", candidate_id="SITE-1", tags=["관광"], source_id="2")
        ],
        restrictions=[],
        transportation=[],
        operational_facts=[]
    )

    resident_persona = PersonaConfig(
        persona_id="p-1", stakeholder_id="s-1", display_name="주민", constituency="인근 주민", relationship_to_topic="영향",
        primary_goal="주거환경", decision_authority="affected", risk_tolerance="low", time_horizon="단기", likely_initial_position="반대",
        spatial_focus_tags=["주거", "민감"]
    )
    
    tourist_persona = PersonaConfig(
        persona_id="p-2", stakeholder_id="s-2", display_name="관광객", constituency="방문객", relationship_to_topic="이용",
        primary_goal="관광동선", decision_authority="user", risk_tolerance="medium", time_horizon="단기", likely_initial_position="찬성",
        spatial_focus_tags=["관광"]
    )

    selector = PersonaContextSelector()
    resident_context = selector.select(resident_persona, candidate)
    tourist_context = selector.select(tourist_persona, candidate)

    assert len(resident_context["spatial_facts"]) == 1
    assert resident_context["spatial_facts"][0]["category"] == "주거지"

    assert len(tourist_context["spatial_facts"]) == 1
    assert tourist_context["spatial_facts"][0]["category"] == "관광지"
