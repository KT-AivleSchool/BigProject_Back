# [이해관계자 페르소나 모드] Phase 2 근거 및 조례 검증 서비스 모듈
from typing import List
from app.core.stakeholder_mode.schemas.stakeholder import CandidateSite, OrdinanceContext
from app.core.stakeholder_mode.schemas.output import PersonaOpinion, ValidationIssue


def validate_evidence_and_ordinances(
    opinions: List[PersonaOpinion],
    candidate_sites: List[CandidateSite],
    ordinance_contexts: List[OrdinanceContext]
) -> List[ValidationIssue]:
    """
    [근거 및 조례 ID 검증 함수]
    페르소나가 작성한 독립 평가(PersonaOpinion)의 근거 ID 및 조례 청크 ID가
    실제 입력된 후보지 목록 및 조례 Context에 존재하는지 무결성을 검증합니다.
    """
    valid_site_ids = {site.candidate_id for site in candidate_sites}
    valid_ord_ids = {ord_item.chunk_id for ord_item in ordinance_contexts}
    valid_all_ids = valid_site_ids.union(valid_ord_ids)

    issues: List[ValidationIssue] = []

    for opinion in opinions:
        for ev_id in opinion.evidence_ids:
            if ev_id not in valid_all_ids:
                issues.append(
                    ValidationIssue(
                        persona_id=opinion.persona_id,
                        issue_type="invalid_evidence_id",
                        message=f"'{opinion.stakeholder_name}' 페르소나가 존재하지 않는 근거/조례 ID '{ev_id}'를 참조했습니다."
                    )
                )

    return issues
