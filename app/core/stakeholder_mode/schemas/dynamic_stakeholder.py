"""
동적 이해관계자 모델 스키마 (Dynamic Stakeholder Schema)
- 주제, GIS 기반 주변 인프라, 조례 등 다양한 데이터를 기반으로 LLM이 도출해낸 이해관계자의 구조화된 데이터를 정의합니다.
- 발굴된 각 후보군의 중요도, 신뢰도 및 추천 사유를 포함합니다.
"""
from typing import List
from pydantic import BaseModel, Field

class StakeholderCandidate(BaseModel):
    """
    [동적 이해관계자 후보 모델]
    주제, GIS, 조례 등 데이터 기반으로 도출된 이해관계자 후보 객체입니다.
    """
    display_name: str = Field(..., description="이해관계자 표출 이름 (예: 후보지 인근 주민)")
    stakeholder_type: str = Field(..., description="분류 태그 (예: resident, parent, merchant, admin 등)")
    relationship_to_topic: str = Field(..., description="주제와의 연관성 및 이익/피해/책임 등의 영향 관계")
    recommendation_reason: str = Field(..., description="이 후보가 추천된 구체적인 근거 설명")
    importance_grade: str = Field(default="C", description="중요도 평가 등급 (예: A, B, C)")
    evidence_confidence: str = Field(default="medium", description="근거 신뢰도 (high, medium, low)")
    related_candidate_ids: List[str] = Field(default_factory=list, description="관련된 후보지 ID 목록")
    evidence_ids: List[str] = Field(default_factory=list, description="근거가 되는 GIS나 조례 데이터 ID 목록")
