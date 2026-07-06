from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status
from pydantic import BaseModel
from typing import List

router = APIRouter()

class HitlCoordinateCorrection(BaseModel):
    parcel_id: int        # cadastral_lands id
    corrected_lat: float  # 보정된 위도
    corrected_lng: float  # 보정된 경도

@router.post("/upload")
def upload_datasets(
    files: List[UploadFile] = File(...),
    district_id: int = Form(...)
):
    """
    [Cj(찬진) 파트 서브 & 장천명 풀스택] 다목적 데이터셋 및 조례 파일 일괄 적재 라우터
    수신된 파일의 확장자를 체크하여 .csv/.shp는 DB팀 파이프라인으로, .pdf는 RAG 파이프라인으로 라우팅합니다.
    """
    uploaded_summaries = []
    for file in files:
        filename = file.filename
        ext = filename.split(".")[-1].lower() if "." in filename else ""
        
        if ext in ["csv", "shp"]:
            routing_target = "DB/PostGIS Pipeline"
        elif ext in ["pdf", "hwp", "txt", "md"]:
            routing_target = "RAG Vector DB Pipeline"
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"지원하지 않는 확장자 파일이 포함되어 있습니다: {filename}"
            )
            
        uploaded_summaries.append({
            "filename": filename,
            "size_bytes": len(file.file.read()),
            "routed_to": routing_target
        })
        
    return {
        "status": "success",
        "message": "파일 일괄 업로드 및 RAG/DB 라우팅 성공.",
        "district_id": district_id,
        "files_processed": uploaded_summaries
    }

@router.post("/hitl/commit")
def commit_hitl_correction(correction: HitlCoordinateCorrection):
    """
    [장천명 풀스택 메인] Step 2 공간 레이어 검증: 지도 핀 드래그앤드롭(dragend) 수동 좌표 보정 확정 API
    """
    # 임시 mock 데이터 반영 확인
    return {
        "status": "success",
        "message": f"필지 {correction.parcel_id}에 대한 HITL 좌표 보정이 성공적으로 커밋되었습니다.",
        "pnu_id": correction.parcel_id,
        "updated_coordinates": {
            "lat": correction.corrected_lat,
            "lng": correction.corrected_lng
        }
    }
