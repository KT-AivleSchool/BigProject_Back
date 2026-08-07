# [이해관계자 페르소나 모드] Phase 2 확장 PersonaConfig 설정 데이터 모델
from typing import List
from pydantic import BaseModel, Field


class PersonaConfig(BaseModel):
    """
    [PersonaConfig 데이터 모델]
    실제 LLM이 역할을 수행하기 위한 실행 설정으로, StakeholderInterestProfile을 기반으로 생성됩니다.
    """
    persona_id: str = Field(..., description="페르소나 고유 식별자 (예: PERSONA-001)")
    stakeholder_id: str = Field(default="UNKNOWN", description="이해관계자 고유 식별자")

    display_name: str = Field(..., description="화면 표시용 페르소나 명칭")
    constituency: str = Field(default="일반", description="대표 집단")
    relationship_to_topic: str = Field(default="이해관계 있음", description="주제와의 관계")

    primary_goal: str = Field(default="최선의 결과 도출", description="가장 중요한 목표")
    success_definition: List[str] = Field(default_factory=list, description="성공의 구체적 상태")

    expected_benefits: List[str] = Field(default_factory=list, description="기대 이익")
    expected_costs: List[str] = Field(default_factory=list, description="예상 비용/불편")
    major_risks: List[str] = Field(default_factory=list, description="주요 위험")

    protected_interests: List[str] = Field(default_factory=list, description="보호해야 할 가치/권익")
    red_lines: List[str] = Field(default_factory=list, description="절대 수용할 수 없는 조건")
    negotiable_conditions: List[str] = Field(default_factory=list, description="협상 가능한 조건")

    spatial_focus_tags: List[str] = Field(default_factory=list, description="공간 관심 항목")
    required_evidence_types: List[str] = Field(default_factory=list, description="필요 근거 유형")
    preferred_metrics: List[str] = Field(default_factory=list, description="선호 지표")
    unique_questions: List[str] = Field(default_factory=list, description="고유 질문")

    decision_authority: str = Field(default="의견 제시", description="의사결정 권한 역할")
    risk_tolerance: str = Field(default="보통", description="위험 수용 성향")
    time_horizon: str = Field(default="단기", description="시간 관점")

    likely_initial_position: str = Field(default="조건부 찬성/반대", description="초기 기본 입장")

    related_candidate_ids: List[str] = Field(default_factory=list, description="관련 후보지 ID 목록")
    evidence_ids: List[str] = Field(default_factory=list, description="참조 근거 데이터 ID 목록")
    ordinance_chunk_ids: List[str] = Field(default_factory=list, description="참조 조례 청크 ID 목록")

    human_approved: bool = Field(default=False, description="사용자 승인 여부")
    human_modified_fields: List[str] = Field(default_factory=list, description="사용자가 직접 수정한 필드 목록")
