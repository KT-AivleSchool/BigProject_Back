# [이해관계자 페르소나 모드] Phase 2 확장 PersonaConfig 설정 데이터 모델
from typing import List
from pydantic import BaseModel, Field


class Priority(BaseModel):
    """
    [우선순위 가중치 모델]
    관심 분야별 중요도 기준 및 상대적 가중치(합 1.0 권장 또는 상대 점수)를 정의합니다.
    """
    criterion: str = Field(..., description="우선순위 평가 기준 (예: 주거 소음 방지, 상권 유동인구 확보)")
    weight: float = Field(default=0.5, description="기준별 가중치 (0.0 ~ 1.0)")


class PersonaConfig(BaseModel):
    """
    [Phase 2 확장 PersonaConfig 데이터 모델]
    중요도 등급(A~D), 참여 유형(필수/선택/참고), 관심사별 가중치 및 수용 불가 조건(Non-negotiable)이 확장된 페르소나 설정 객체입니다.
    """
    persona_id: str = Field(..., description="페르소나 고유 식별자 (예: PERSONA-001)")
    display_name: str = Field(..., description="화면 표시용 페르소나 명칭 (예: 후보지 인근 주민)")
    stakeholder_type: str = Field(..., description="이해관계자 유형 (예: resident, merchant, officer)")
    relationship_to_topic: str = Field(..., description="주제와의 관계 및 핵심 입장 배경")

    importance_grade: str = Field(default="A", description="이해관계자 중요도 등급 (A: 핵심, B: 주요, C: 참고, D: 단순관찰)")
    participation_type: str = Field(default="essential", description="참여 유형 (essential: 필수, optional: 선택, reference: 참고)")

    priorities: List[Priority] = Field(default_factory=list, description="관심사별 우선순위 가중치 목록")
    interests: List[str] = Field(default_factory=list, description="주요 관심사 목록")
    concerns: List[str] = Field(default_factory=list, description="주요 우려사항 목록")
    
    initial_position: str = Field(..., description="초기 기본 입장 (support, conditional_support, opposition, conditional_opposition)")
    acceptable_conditions: List[str] = Field(default_factory=list, description="수용 가능한 협상/조정 조건 목록")
    non_negotiable_conditions: List[str] = Field(default_factory=list, description="절대 수용 불가능한 조건 목록 (절대 조건)")

    evidence_ids: List[str] = Field(default_factory=list, description="참조 근거 데이터 ID 목록")
    ordinance_chunk_ids: List[str] = Field(default_factory=list, description="참조 조례 청크 ID 목록")
