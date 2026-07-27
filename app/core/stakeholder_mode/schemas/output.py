from typing import List, Optional
from pydantic import BaseModel, Field


class PersonaOpinion(BaseModel):
    persona_id: str = Field(..., description="페르소나 ID")
    stakeholder_name: str = Field(..., description="이해관계자 이름")

    preferred_candidate_id: Optional[str] = Field(None, description="선호 후보지 ID (선택한 경우)")
    position: str = Field(..., description="입장 (예: support, conditional_support, opposition, conditional_opposition)")

    benefits: List[str] = Field(default_factory=list, description="기대 효과 / 장점")
    concerns: List[str] = Field(default_factory=list, description="우려사항")
    required_conditions: List[str] = Field(default_factory=list, description="필요 요구 / 수용 조건")
    evidence_ids: List[str] = Field(default_factory=list, description="근거 ID 목록")


class StakeholderModeResult(BaseModel):
    project_id: str = Field(..., description="프로젝트 ID")
    topic: str = Field(..., description="토론 주제")
    personas: List[PersonaOpinion] = Field(default_factory=list, description="페르소나별 평가 결과 목록")
