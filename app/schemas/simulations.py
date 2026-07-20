from pydantic import BaseModel, Field
from typing import List, Dict, Optional


class SimulationRunRequest(BaseModel):
    parcel_id: int = Field(..., description="시뮬레이션할 적격 후보지 필지 ID")
    ahp_model_id: Optional[int] = Field(
        None, description="적용할 가중치 락 세트 ID (생략 시 기본 락 세트 적용)"
    )


class ScenarioDetail(BaseModel):
    scenario: str = Field(..., description="시나리오 종류 (A, B, C)")
    scenario_description: str = Field(..., description="시나리오 제목/설명")
    final_acceptance_score: float = Field(..., description="최종 수용도 점수")
    reason: str = Field(..., description="시나리오 도출 이유")
    summary: str = Field(..., description="시나리오 전개 요약")
    conflict_risk_index: float = Field(..., description="갈등 위험 지수 (0.0 ~ 100.0)")
    risk_reason: str = Field(..., description="갈등 위험 지수 산출 이유")


class SimulationResultResponse(BaseModel):
    parcel_id: int = Field(..., description="필지 고유 ID")
    conflict_sensitivity_score: float = Field(
        ..., description="갈등 민감도 지수 (CSS - 0.0 ~ 10.0)"
    )
    conflict_factors: Dict[str, float] = Field(
        ...,
        description="상세 갈등 인자 영향도 (예: 소음피해, 임대료상승, 보행혼잡, 경관훼손 등)",
    )
    scenario: ScenarioDetail = Field(
        ..., description="토론 결과로 도출된 최종 1개의 시나리오 정보"
    )


class SseMessagePacket(BaseModel):
    sender: str = Field(
        ..., description="발화자 구분 (주민대표, 상인대표, 조정공무원, 시스템)"
    )
    message: str = Field(..., description="실시간 출력 텍스트 토큰")
    is_finished: bool = Field(False, description="스트리밍 종료 여부")
