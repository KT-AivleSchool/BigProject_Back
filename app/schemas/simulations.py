from pydantic import BaseModel, Field
from typing import List, Dict, Optional


class SimulationRunRequest(BaseModel):
    parcel_id: int = Field(..., description="시뮬레이션할 적격 후보지 필지 ID")
    ahp_model_id: Optional[int] = Field(
        None, description="적용할 가중치 락 세트 ID (생략 시 기본 락 세트 적용)"
    )


class ScenarioDetail(BaseModel):
    scenario_type: str = Field(
        ..., description="시나리오 종류 (A: 주민 우세, B: 상인 우세, C: 공공 조정)"
    )
    title: str = Field(..., description="시나리오 제목")
    probability: float = Field(..., description="도달 확률 (0.0 ~ 1.0)")
    summary: str = Field(..., description="시나리오 전개 요약")
    conflict_risk_index: float = Field(..., description="갈등 위험 지수 (0.0 ~ 100.0)")


class SimulationResultResponse(BaseModel):
    parcel_id: int = Field(..., description="필지 고유 ID")
    conflict_sensitivity_score: float = Field(
        ..., description="갈등 민감도 지수 (CSS - 0.0 ~ 10.0)"
    )
    conflict_factors: Dict[str, float] = Field(
        ...,
        description="상세 갈등 인자 영향도 (예: 소음피해, 임대료상승, 보행혼잡, 경관훼손 등)",
    )
    scenarios: List[ScenarioDetail] = Field(
        ..., description="3대 시나리오 예측 모델 전개 정보"
    )


class SseMessagePacket(BaseModel):
    sender: str = Field(
        ..., description="발화자 구분 (주민대표, 상인대표, 조정공무원, 시스템)"
    )
    message: str = Field(..., description="실시간 출력 텍스트 토큰")
    is_finished: bool = Field(False, description="스트리밍 종료 여부")
