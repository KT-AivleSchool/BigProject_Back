from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class PipelineRunRequest(BaseModel):
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


class PipelineRunResponse(BaseModel):
    status: str = Field("success", description="파이프라인 실행 결과 상태")
    domain: str = Field(..., description="실행된 도메인명")
    user_intent: str = Field(..., description="사용자 목적")
    artifacts: Dict[str, str] = Field(..., description="생성된 산출물 파일 경로 목록")
    timer_report: Optional[List[Dict[str, Any]]] = Field(
        None, description="단계별 소요 시간 리포트"
    )


class PipelineCleanRequest(BaseModel):
    domain_name: str = Field(
        ..., description="도메인 명칭 (예: '흡연', '재활용', 'EV')"
    )
    csv_preview: bool = Field(True, description="CSV 프리뷰 파일도 함께 생성할지 여부")
    no_prune: bool = Field(False, description="기존 산출물 보존 여부")


class PipelineCleanResponse(BaseModel):
    status: str = Field("success", description="처리 상태")
    domain: str = Field(..., description="도메인명")
    cleaned_files: List[str] = Field(
        ..., description="생성된 정제 파일 목록 (.gpkg, .csv)"
    )
    report_file: str = Field(..., description="정제 결과 보고서 JSON 파일 경로")


class PipelineWeightRequest(BaseModel):
    domain_name: str = Field(
        ..., description="도메인 명칭 (예: '흡연', '재활용', 'EV')"
    )


class PipelineWeightResponse(BaseModel):
    status: str = Field("success", description="처리 상태")
    domain: str = Field(..., description="도메인명")
    consistency_ratio: float = Field(
        ..., description="AHP 일관성 비율 (C.R. < 0.1 검증)"
    )
    is_valid: bool = Field(..., description="합리성 판정 임계치 통과 여부")
    weights: Dict[str, float] = Field(
        ..., description="산출된 최종 요소별 정규화 가중치"
    )
