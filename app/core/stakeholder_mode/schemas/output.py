# [이해관계자 페르소나 모드] Phase 2 확장 독립 평가 및 결과 집계 스키마
from typing import List, Optional
from pydantic import BaseModel, Field


class CandidateScore(BaseModel):
    """
    [후보지별 평가 점수 모델]
    페르소나가 각 후보지에 대해 정량적으로 산출한 점수 및 사유입니다.
    """
    candidate_id: str = Field(..., description="평가 대상 후보지 고유 ID (예: SITE-A)")
    score: float = Field(..., description="후보지 산출 점수 (100점 만점 기준)")
    reasoning: str = Field(..., description="점수 부여 사유 및 종합 평가")


class PersonaOpinion(BaseModel):
    """
    [Phase 2 확장 페르소나 독립 평가 데이터 모델]
    후보지별 정량 점수(CandidateScore) 및 수용 불가 조건 위반 검토가 포함된 독립 평가 결과입니다.
    """
    persona_id: str = Field(..., description="평가를 작성한 페르소나 고유 ID")
    stakeholder_name: str = Field(..., description="이해관계자 명칭")

    preferred_candidate_id: Optional[str] = Field(None, description="가장 선호하는 후보지 ID")
    position: str = Field(..., description="최종 정리된 입장 (support, conditional_support, opposition, conditional_opposition)")

    candidate_scores: List[CandidateScore] = Field(default_factory=list, description="후보지별 정량 평가 점수 목록")
    benefits: List[str] = Field(default_factory=list, description="기대효과 / 장점 목록")
    concerns: List[str] = Field(default_factory=list, description="우려사항 목록")
    required_conditions: List[str] = Field(default_factory=list, description="필요 요구 / 수용 조건 목록")
    non_negotiable_violations: List[str] = Field(default_factory=list, description="후보지별 수용 불가 조건 위반 항목 목록")
    evidence_ids: List[str] = Field(default_factory=list, description="근거 ID 및 조례 청크 ID 목록")


class ValidationIssue(BaseModel):
    """
    [근거 및 조례 검증 항목 모델]
    존재하지 않는 근거 ID 사용 등의 검증 이슈를 기록합니다.
    """
    persona_id: str = Field(..., description="이슈 발생 페르소나 ID")
    issue_type: str = Field(..., description="이슈 유형 (invalid_evidence_id, invalid_ordinance_id 등)")
    message: str = Field(..., description="이슈 상세 설명")


class StakeholderModeResult(BaseModel):
    """
    [Phase 2 확장 최종 집계 데이터 모델]
    페르소나 의견 목록과 함께 근거/조례 검증 리포트(ValidationIssue)를 포함합니다.
    """
    project_id: str = Field(..., description="프로젝트 고유 ID")
    topic: str = Field(..., description="토론 안건 주제")
    personas: List[PersonaOpinion] = Field(default_factory=list, description="참여 이해관계자별 독립 평가 결과 목록")
    validation_issues: List[ValidationIssue] = Field(default_factory=list, description="근거 및 조례 검증 이슈 리포트 목록")
