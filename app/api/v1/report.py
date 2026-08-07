from fastapi import APIRouter, Response, HTTPException, status
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from app.services.hwpx_generator import build_hwpx_report

router = APIRouter()

class HwpxReportRequest(BaseModel):
    parcel_id: Optional[int] = None
    candidate_jibun: str
    candidate_address: Optional[str] = None
    candidate_lat: float
    candidate_lng: float
    facility_type: str
    intensity_level: str
    conflict_sensitivity_score: Optional[float] = None
    conflict_factors: Optional[Dict[str, float]] = None
    ahp_weights: Dict[str, float]
    timestamp: str
    scenarios: List[Dict[str, Any]]
    debate_logs: Optional[List[Dict[str, Any]]] = None

@router.post("/download/hwpx", status_code=status.HTTP_200_OK)
async def download_hwpx_report(request: HwpxReportRequest):
    """
    파이프라인 심의 데이터를 수신하여 HWPX (한글) 문서를 생성 후 파일 다운로드 바이너리를 전달합니다.
    """
    try:
        data = request.model_dump()
        hwpx_bytes = build_hwpx_report(data)
        
        headers = {
            "Content-Disposition": "attachment; filename*=UTF-8''site_hearing_report.hwpx",
            "Content-Type": "application/hwp+zip"
        }
        return Response(content=hwpx_bytes, media_type="application/hwp+zip", headers=headers)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"HWPX 문서 생성 중 오류가 발생했습니다: {str(e)}"
        )
