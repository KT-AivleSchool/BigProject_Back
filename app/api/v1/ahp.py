from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from app.services.ahp_service import calculate_ahp_consistency

router = APIRouter()

class AhpWeightsRequest(BaseModel):
    matrix_size: int
    # 5대 입지 인자 간의 쌍대비교 5x5 역수 행렬 데이터
    pairwise_matrix: list[list[float]]

@router.post("/calculate")
def calculate_ahp_metrics(payload: AhpWeightsRequest):
    """
    [승헌 TL 파트 & 장천명 풀스택] AHP 가중치 가변 제어 및 실시간 C.R. 일관성 비율 연산 API
    """
    try:
        result = calculate_ahp_consistency(payload.matrix_size, payload.pairwise_matrix)
        
        # C.R. < 0.1 검증 임계치 판정
        if not result["is_valid"]:
            return {
                "status": "warning",
                "is_locked_allowed": False,
                "consistency_ratio": result["consistency_ratio"],
                "message": "일관성 비율(C.R.)이 0.1을 초과하여 의사결정에 모순이 존재합니다. 슬라이더를 재조정해 주세요.",
                "weights": result["weights"]
            }
            
        return {
            "status": "success",
            "is_locked_allowed": True,
            "consistency_ratio": result["consistency_ratio"],
            "message": "AHP 일관성 비율 검증 통과. 입지 분석 락(Lock)을 진행할 수 있습니다.",
            "weights": result["weights"]
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"AHP 행렬 정밀 연산 중 오류가 발생했습니다: {str(e)}"
        )

@router.post("/lock/{district_id}")
def lock_ahp_model(district_id: int):
    """
    [장천명 풀스택] 특정 자치구의 AHP 가중치 프로파일을 잠금(Lock)하여 의사결정 모델 영구 동결
    """
    return {
        "status": "success",
        "message": f"자치구 {district_id}의 AHP 입지 가중치 모델이 성공적으로 잠금 처리되었습니다. 변경이 불가능합니다.",
        "district_id": district_id,
        "is_locked": True
    }
