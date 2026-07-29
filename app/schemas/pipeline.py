from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class PipelineBaseModel(BaseModel):
    """
    [이슈 #165] Client-Trust 정책 반영 베이스 모델
    - extra="allow": 프론트엔드가 보정 데이터나 추가 커스텀 필드를 전송해도 422 에러 없이 유연하게 수용
    """
    model_config = ConfigDict(extra="allow")


class PipelineRunRequest(PipelineBaseModel):
    domain_name: str = Field(default="흡연", description="분석 도메인 명칭")
    user_intent: str = Field(default="용산구 흡연부스 입지 분석", description="분석 의도 및 목적")
    skip_search: bool = Field(default=False, description="상위법 검색 건너뛰기 여부")
    mock: bool = Field(default=False, description="테스트용 가짜 데이터 구동 여부")
    session_id: Optional[str] = Field(default=None, description="기존 세션 재사용 시 ID")


class PipelineRunResponse(PipelineBaseModel):
    status: str = "success"
    session_id: str
    domain: str
    user_intent: str
    artifacts: Dict[str, str] = Field(default_factory=dict)
    timer_report: Optional[List[Any]] = None


class PipelineSessionStateResponse(PipelineBaseModel):
    status: str = "success"
    session_id: str
    current_step: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class PipelineHitlReviewRequest(PipelineBaseModel):
    session_id: str = Field(..., description="세션 ID")
    review_data: Dict[str, Any] = Field(default_factory=dict, description="사람(HITL)이 확정한 보정 데이터")


class PipelineCleanRequest(PipelineBaseModel):
    domain_name: str = Field(default="흡연", description="도메인 명칭")
    csv_preview: bool = Field(default=True, description="CSV 미리보기 파싱 여부")
    no_prune: bool = Field(default=False, description="미사용 컬럼 제거 건너뛰기 여부")


class PipelineCleanResponse(PipelineBaseModel):
    status: str = "success"
    domain: str
    cleaned_files: List[str] = Field(default_factory=list)
    report_file: str = ""


class PipelineWeightRequest(PipelineBaseModel):
    domain_name: str = Field(default="흡연", description="도메인 명칭")


class PipelineWeightResponse(PipelineBaseModel):
    status: str = "success"
    domain: str
    consistency_ratio: float = 0.0
    is_valid: bool = True
    weights: Dict[str, float] = Field(default_factory=dict)
