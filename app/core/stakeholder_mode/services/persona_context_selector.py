from app.core.stakeholder_mode.schemas.persona import PersonaConfig
from app.core.stakeholder_mode.schemas.context import CandidateContext


class PersonaContextSelector:
    """
    전체 CandidateContext 중 특정 페르소나에게 필요한 데이터만 필터링하는 셀렉터
    """
    def select(self, persona: PersonaConfig, candidate_context: CandidateContext) -> dict:
        wanted_tags = set(persona.spatial_focus_tags)
        required_types = set(persona.required_evidence_types)

        selected_spatial_facts = [
            fact.model_dump()
            for fact in candidate_context.spatial_facts
            if wanted_tags.intersection(fact.tags) or fact.category in wanted_tags
        ]

        selected_restrictions = [
            item
            for item in candidate_context.restrictions
            if item.get("evidence_type") in required_types
        ]

        return {
            "candidate_id": candidate_context.candidate_id,
            "name": candidate_context.name,
            "spatial_facts": selected_spatial_facts[:15],
            "restrictions": selected_restrictions[:10],
            "transportation": candidate_context.transportation[:10],
            "operational_facts": candidate_context.operational_facts[:10],
        }
