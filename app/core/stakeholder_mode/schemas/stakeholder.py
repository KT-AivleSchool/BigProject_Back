# [이해관계자 페르소나 모드] 입력 데이터 및 추천 이해관계자 스키마 정의서
from typing import Any, Dict, List
from pydantic import BaseModel, Field


class CandidateSite(BaseModel):
    """
    [후보지 데이터 모델]
    감리 AI가 정제한 후보지별 정량적/정성적 속성 데이터를 정의합니다.
    """
    candidate_id: str = Field(..., description="후보지 고유 식별자 (예: SITE-A)")
    name: str = Field(..., description="후보지 명칭 (예: 후보지 A - 구도심 인근 부지)")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="후보지 상세 속성 데이터 (접근성 점수, 예상 예산, 주거 밀집도 등)")


class OrdinanceContext(BaseModel):
    """
    [조례 RAG Context 데이터 모델]
    벡터 DB 검색 결과로 전달받은 관련 조례 조항 및 텍스트 정보입니다.
    """
    chunk_id: str = Field(..., description="조례 청크 고유 식별자 (예: ORD-001)")
    ordinance_name: str = Field(..., description="조례 명칭 (예: 도시계획 조례 제5조)")
    content: str = Field(..., description="조례 텍스트 상세 내용")


class StakeholderCandidate(BaseModel):
    """
    [이해관계자 추천 후보 데이터 모델]
    LLM이 안건 및 조례 분석 후 자동 추천하는 이해관계자 정보입니다.
    """
    name: str = Field(..., description="이해관계자 명칭 (예: 후보지 인근 주민대표)")
    stakeholder_type: str = Field(..., description="이해관계자 유형 코드 (예: resident, merchant, officer)")
    relationship_to_topic: str = Field(..., description="토론 주제와의 관계 및 영향성 설명")
    recommendation_reason: str = Field(..., description="LLM이 해당 이해관계자를 추천한 명확한 이유")


class StakeholderModeInput(BaseModel):
    """
    [이해관계자 모드 전체 입력 데이터 모델]
    감리 AI 정제 데이터 및 조례 RAG 결과를 포함하는 모드 실행 최상위 입력 규격입니다.
    """
    project_id: str = Field(..., description="프로젝트 고유 식별 코드 (예: PROJECT-001)")
    topic: str = Field(..., description="토론 및 평가 대상 주제 (예: 공공시설 입지 선정)")
    candidate_sites: List[CandidateSite] = Field(..., description="평가 대상 후보지 목록")
    ordinance_contexts: List[OrdinanceContext] = Field(default_factory=list, description="참조 조례 RAG Context 목록")
