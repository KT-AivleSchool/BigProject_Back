# [이해관계자 페르소나 모드] 독립 평가 출력 및 최종 집계 데이터 모델
from typing import List, Optional
from pydantic import BaseModel, Field


class PersonaOpinion(BaseModel):
    """
    [페르소나별 독립 평가 데이터 모델]
    각 페르소나가 후보지 데이터 및 조례 Context를 바탕으로 제출한 독립적인 평가 결과 객체입니다.
    """
    persona_id: str = Field(..., description="평가를 작성한 페르소나 고유 ID")
    stakeholder_name: str = Field(..., description="이해관계자 명칭")

    preferred_candidate_id: Optional[str] = Field(None, description="가장 선호하는 후보지 ID (보류 시 None)")
    position: str = Field(..., description="최종 정리된 입장 (support, conditional_support, opposition, conditional_opposition)")

    benefits: List[str] = Field(default_factory=list, description="해당 페르소나 관점에서의 주요 기대효과 / 장점 목록")
    concerns: List[str] = Field(default_factory=list, description="해당 페르소나 관점에서의 핵심 우려사항 목록")
    required_conditions: List[str] = Field(default_factory=list, description="사업 추진을 위해 필수로 이행되어야 할 요구조건 목록")
    evidence_ids: List[str] = Field(default_factory=list, description="의견 작성에 활용된 근거 데이터 및 조례 ID 목록")


class StakeholderModeResult(BaseModel):
    """
    [이해관계자 모드 최종 집계 데이터 모델]
    모든 페르소나의 독립 평가 결과를 하나의 구조화된 JSON 결과 객체로 모아 반환합니다.
    """
    project_id: str = Field(..., description="프로젝트 고유 ID")
    topic: str = Field(..., description="토론 안건 주제")
    personas: List[PersonaOpinion] = Field(default_factory=list, description="참여 이해관계자별 독립 평가 결과 목록")
