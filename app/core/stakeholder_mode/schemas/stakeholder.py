from typing import Any, Dict, List
from pydantic import BaseModel, Field


class CandidateSite(BaseModel):
    candidate_id: str = Field(..., description="후보지 고유 ID (예: SITE-A)")
    name: str = Field(..., description="후보지 명칭 (예: 후보지 A)")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="후보지 속성 정보 (접근성, 예산 등)")


class OrdinanceContext(BaseModel):
    chunk_id: str = Field(..., description="조례 청크 ID (예: ORD-001)")
    ordinance_name: str = Field(..., description="조례 명칭 (예: 도시계획 조례)")
    content: str = Field(..., description="조례 텍스트 내용")


class StakeholderCandidate(BaseModel):
    name: str = Field(..., description="이해관계자 명칭 (예: 후보지 인근 주민)")
    stakeholder_type: str = Field(..., description="이해관계자 유형 (예: resident, merchant, officer)")
    relationship_to_topic: str = Field(..., description="주제와의 관계 및 영향")
    recommendation_reason: str = Field(..., description="LLM 추천 사유")


class StakeholderModeInput(BaseModel):
    project_id: str = Field(..., description="프로젝트 고유 ID")
    topic: str = Field(..., description="토론 및 평가 주제")
    candidate_sites: List[CandidateSite] = Field(..., description="후보지 목록")
    ordinance_contexts: List[OrdinanceContext] = Field(default_factory=list, description="조례 Context 목록")
