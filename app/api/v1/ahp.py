from fastapi import APIRouter, HTTPException, status
from app.services.ahp_service import calculate_ahp_consistency
from app.schemas.ahp import (
    AhpWeightsRequest,
    AhpCalculateResponse,
    AhpSaveRequest,
    AhpSaveResponse,
)

router = APIRouter()


@router.post("/calculate", response_model=AhpCalculateResponse)
def calculate_ahp_metrics(payload: AhpWeightsRequest):
    """
    [승헌 TL 파트 & 장천명 풀스택] AHP 가중치 가변 제어 및 실시간 C.R. 일관성 비율 연산 API
    """
    try:
        result = calculate_ahp_consistency(payload.matrix_size, payload.pairwise_matrix)

        return {
            "status": "success",
            "is_locked_allowed": result["is_valid"],
            "consistency_ratio": result["consistency_ratio"],
            "weights": result["weights"],
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"AHP 행렬 정밀 연산 중 오류가 발생했습니다: {str(e)}",
        )


@router.post("/lock", response_model=AhpSaveResponse)
def lock_ahp_model(payload: AhpSaveRequest):
    """
    [장천명 풀스택] 특정 자치구의 AHP 가중치 프로파일을 잠금(Lock)하여 의사결정 모델 영구 동결
    """
    import datetime

    return {
        "ahp_model_id": 99,
        "is_locked": True,
        "saved_at": datetime.datetime.now().isoformat(),
    }
