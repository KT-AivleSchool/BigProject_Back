from pydantic import BaseModel, Field
from typing import List


class AhpWeightsRequest(BaseModel):
    matrix_size: int = Field(
        ..., description="쌍대비교 행렬 크기 (동적으로 도출된 가중치 요인 개수 기준)"
    )
    pairwise_matrix: List[List[float]] = Field(
        ...,
        description="N x N 쌍대비교 역수행렬 (각 대각 성분은 1.0이며 대칭 요소는 역수 관계)",
    )


class AhpCalculateResponse(BaseModel):
    status: str = Field("success", description="연산 성공 여부")
    consistency_ratio: float = Field(
        ..., description="일관성 비율 (C.R.) 값. 0.1 미만일 때 신뢰성 충족"
    )
    is_locked_allowed: bool = Field(
        ..., description="C.R. < 0.1 만족에 따른 가중치 동결 저장 가능 여부"
    )
    weights: List[float] = Field(
        ..., description="동적으로 도출된 입지 요인별 정규화 가중치 벡터"
    )


class AhpSaveRequest(BaseModel):
    pairwise_matrix: List[List[float]] = Field(
        ..., description="입력 쌍대비교 대칭행렬"
    )
    weights: List[float] = Field(..., description="산출 가중치 벡터")
    consistency_ratio: float = Field(..., description="일관성 비율")


class AhpSaveResponse(BaseModel):
    ahp_model_id: int = Field(..., description="DB에 저장된 AHP 모델 레코드 고유 ID")
    is_locked: bool = Field(True, description="저장 및 잠금(Lock) 완료 여부")
    saved_at: str = Field(..., description="저장 완료 시각")
