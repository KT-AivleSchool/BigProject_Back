from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, Dict, Any, List


class PipelineBaseModel(BaseModel):
    """
    [이슈 #165 Client-Trust Policy]
    프론트엔드 통신 유연성을 위해 추가/변형 필드가 유입되어도 422 에러로 자르지 않고 관대하게 허용
    """

    model_config = ConfigDict(extra="allow")


class PipelineRunRequest(PipelineBaseModel):
    domain_name: str = Field(
        ..., description="도메인 명칭 (예: '흡연', '재활용', 'EV')"
    )
    user_intent: str = Field(
        ..., description="사용자 탐색 의도 및 목적 (예: '용산구 흡연부스 부지 선정')"
    )
    skip_search: bool = Field(
        False, description="상위법 검색(Search Phase) 건너뜀 여부"
    )
    mock: bool = Field(
        False, description="OpenAI LLM 호출 없이 Mock 모드로 실행할지 여부"
    )
    session_id: Optional[str] = Field(
        None, description="진행 상태 스트리밍을 위한 고유 세션 ID"
    )


class PipelineRunResponse(PipelineBaseModel):
    status: str = Field("success", description="파이프라인 실행 결과 상태")
    session_id: str = Field(..., description="발급되거나 유지된 고유 세션 ID")
    domain: str = Field(..., description="실행된 도메인명")
    user_intent: str = Field(..., description="사용자 목적")
    artifacts: Dict[str, str] = Field(..., description="생성된 산출물 파일 경로 목록")
    timer_report: Optional[List[Dict[str, Any]]] = Field(
        None, description="단계별 소요 시간 리포트"
    )


class PipelineCleanRequest(PipelineBaseModel):
    domain_name: str = Field(
        ..., description="도메인 명칭 (예: '흡연', '재활용', 'EV')"
    )
    csv_preview: bool = Field(True, description="CSV 프리뷰 파일도 함께 생성할지 여부")
    no_prune: bool = Field(False, description="기존 산출물 보존 여부")


class PipelineCleanResponse(PipelineBaseModel):
    status: str = Field("success", description="처리 상태")
    domain: str = Field(..., description="도메인명")
    cleaned_files: List[str] = Field(
        ..., description="생성된 정제 파일 목록 (.gpkg, .csv)"
    )
    report_file: str = Field(..., description="정제 결과 보고서 JSON 파일 경로")


class PipelineWeightRequest(PipelineBaseModel):
    domain_name: str = Field(
        ..., description="도메인 명칭 (예: '흡연', '재활용', 'EV')"
    )


class PipelineWeightResponse(PipelineBaseModel):
    status: str = Field("success", description="처리 상태")
    domain: str = Field(..., description="도메인명")
    consistency_ratio: float = Field(
        ..., description="AHP 일관성 비율 (C.R. < 0.1 검증)"
    )
    is_valid: bool = Field(..., description="합리성 판정 임계치 통과 여부")
    weights: Dict[str, float] = Field(
        ..., description="산출된 최종 요소별 정규화 가중치"
    )


class PipelineSessionStateResponse(PipelineBaseModel):
    status: str = Field("success", description="처리 결과 상태")
    session_id: str = Field(..., description="조회된 세션 ID")
    current_step: str = Field(..., description="현재 세션의 파이프라인 단계")
    payload: Dict[str, Any] = Field(..., description="Redis 캐시에 저장된 상태 객체")


class PipelineHitlReviewRequest(PipelineBaseModel):
    session_id: str = Field(..., description="확정할 파이프라인 세션 ID")
    review_data: Dict[str, Any] = Field(
        default_factory=dict,
        description="사람(HITL)이 확정한 배제반경 및 역할 보정 데이터 (Client-Trust 적용)",
    )
