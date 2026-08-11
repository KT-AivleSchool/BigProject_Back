from typing import Sequence, Dict, Any
from app.core.stakeholder_mode.schemas.interest_profile import StakeholderInterestProfile
from app.core.stakeholder_mode.schemas.stakeholder import StakeholderCandidate
from app.core.stakeholder_mode.prompts.renderer import jinja_env


class InterestProfileGenerator:
    """
    이해관계자별 StakeholderInterestProfile을 생성하는 서비스
    """
    def __init__(self, llm_client):
        # with_structured_output을 사용하여 Pydantic 모델 반환 보장
        self.structured_llm = llm_client.with_structured_output(StakeholderInterestProfile)

    async def generate(
        self,
        topic: str,
        stakeholder: StakeholderCandidate,
        candidate_contexts: Sequence[Dict[str, Any]],
        ordinance_contexts: Sequence[Dict[str, Any]],
    ) -> StakeholderInterestProfile:
        template = jinja_env.get_template("stakeholder/generate_interest_profile.j2")
        prompt_text = template.render(
            topic=topic,
            stakeholder=stakeholder.model_dump(),
            candidate_contexts=candidate_contexts,
            ordinance_contexts=ordinance_contexts,
        )

        profile = await self.structured_llm.ainvoke(prompt_text)

        if profile.stakeholder_id != stakeholder.stakeholder_id:
            profile.stakeholder_id = stakeholder.stakeholder_id

        return profile
