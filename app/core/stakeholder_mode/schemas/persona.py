# [이해관계자 페르소나 모드] PersonaConfig 설정 데이터 모델
from typing import List
from pydantic import BaseModel, Field


class PersonaConfig(BaseModel):
    """
    [PersonaConfig 데이터 모델]
    사용자가 선택/수정한 이해관계자를 바탕으로 생성되는 페르소나의 동적 역할극 설정 객체입니다.
    Jinja2 프롬프트 템플릿에 주입되어 페르소나별 고유한 페르소나 시스템 프롬프트로 변환됩니다.
    """
    persona_id: str = Field(..., description="페르소나 고유 식별자 (예: PERSONA-001)")
    display_name: str = Field(..., description="화면 표시용 페르소나 명칭 (예: 후보지 인근 주민)")
    stakeholder_type: str = Field(..., description="이해관계자 유형 (예: resident, merchant, officer)")
    relationship_to_topic: str = Field(..., description="주제와의 관계 및 핵심 입장 배경")

    interests: List[str] = Field(default_factory=list, description="페르소나의 주요 관심사 목록 (예: 주거환경, 교통안전)")
    concerns: List[str] = Field(default_factory=list, description="페르소나의 주요 우려사항 목록 (예: 차량 소음 증가)")
    initial_position: str = Field(..., description="초기 기본 입장 (예: support, conditional_support, opposition, conditional_opposition)")
    acceptable_conditions: List[str] = Field(default_factory=list, description="수용 가능한 협상/조정 조건 목록")

    evidence_ids: List[str] = Field(default_factory=list, description="참조 근거 데이터 ID 목록 (예: SITE-A)")
    ordinance_chunk_ids: List[str] = Field(default_factory=list, description="참조 조례 청크 ID 목록 (예: ORD-001)")
