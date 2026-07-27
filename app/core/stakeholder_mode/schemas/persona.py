from typing import List
from pydantic import BaseModel, Field


class PersonaConfig(BaseModel):
    persona_id: str = Field(..., description="페르소나 고유 ID (예: PERSONA-001)")
    display_name: str = Field(..., description="표시 이름 (예: 후보지 인근 주민)")
    stakeholder_type: str = Field(..., description="이해관계자 유형 (예: resident)")
    relationship_to_topic: str = Field(..., description="주제와의 관계 및 입장 배경")

    interests: List[str] = Field(default_factory=list, description="주요 관심사 목록")
    concerns: List[str] = Field(default_factory=list, description="주요 우려사항 목록")
    initial_position: str = Field(..., description="초기 입장 (예: conditional_opposition, support)")
    acceptable_conditions: List[str] = Field(default_factory=list, description="수용 가능한 조건 목록")

    evidence_ids: List[str] = Field(default_factory=list, description="참조한 데이터 근거 ID 목록")
    ordinance_chunk_ids: List[str] = Field(default_factory=list, description="참조한 조례 청크 ID 목록")
