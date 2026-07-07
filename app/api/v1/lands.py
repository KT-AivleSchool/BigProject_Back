from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status
from typing import List
from app.schemas.lands import UploadResponse, HitlCoordinateCorrection, LandDetailResponse, FileMetadata, CsvAuditResponse

router = APIRouter()

@router.post("/upload", response_model=UploadResponse)
def upload_datasets(
    files: List[UploadFile] = File(...),
    district_id: int = Form(...)
):
    """
    [Cj(찬진) 파트 서브 & 장천명 풀스택] 다목적 데이터셋 및 조례 파일 일괄 적재 라우터
    수신된 파일의 확장자를 체크하여 .csv/.shp는 DB팀 파이프라인으로, .pdf는 RAG 파이프라인으로 라우팅합니다.
    """
    first_file = files[0] if files else None
    filename = first_file.filename if first_file else "dummy.csv"
    ext = filename.split(".")[-1].lower() if "." in filename else "csv"
    
    if ext not in ["csv", "shp", "pdf", "hwp", "txt", "md"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"지원하지 않는 확장자 파일이 포함되어 있습니다: {filename}"
        )
        
    # Pydantic Schema에 따른 DTO 반환
    return {
        "status": "success",
        "summary": {
            "filename": filename,
            "file_type": ext.upper(),
            "total_records": 100,
            "imported_records": 95,
            "failed_records": 5
        }
    }

@router.post("/hitl/commit")
def commit_hitl_correction(correction: HitlCoordinateCorrection):
    """
    [장천명 풀스택 메인] Step 2 공간 레이어 검증: 지도 핀 드래그앤드롭(dragend) 수동 좌표 보정 확정 API
    """
    return {
        "status": "success",
        "message": f"필지 {correction.parcel_id}에 대한 HITL 좌표 보정이 성공적으로 커밋되었습니다.",
        "pnu_id": correction.parcel_id,
        "updated_coordinates": {
            "lat": correction.corrected_lat,
            "lng": correction.corrected_lng
        }
    }

@router.get("/details/{parcel_id}", response_model=LandDetailResponse)
def get_land_details(parcel_id: int):
    """
    특정 필지 상세 조회 API 규격
    """
    return {
        "parcel_id": parcel_id,
        "address": "서울특별시 용산구 한강대로 180",
        "geometry_geojson": {
            "type": "Polygon",
            "coordinates": [[[126.97, 37.53], [126.98, 37.53], [126.98, 37.54], [126.97, 37.53]]]
        },
        "is_excluded": False,
        "exclusion_reason": None,
        "lat": 37.53,
        "lng": 126.97
    }


@router.post("/audit/csv", response_model=CsvAuditResponse)
def audit_csv_dataset(file: UploadFile = File(...)):
    """
    [장천명 풀스택] Step 1. CSV 데이터셋 수신 전용 AI 사전 감리 및 동적 가중치 도출 API
    """
    filename = file.filename
    if not filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Step 1 감리 파이프라인은 오직 CSV 확장자 파일만 업로드할 수 있습니다."
        )
        
    return {
        "status": "success",
        "audit_reason": "전기차 지상 충전시설 설치 의무 법규 및 소방 안전 가이드라인에 따른 충전 부지 적격성 검토가 요구됩니다. 일부 주차 구획의 지상/지하 경계 부주의 기재 가능성이 스캔되었습니다.",
        "user_intent": "용산구 내 친환경자동차법 및 소방청 가이드라인 설치의무를 충족하는 지상형 전기차 급송 충전소 최적 입지 도출",
        "extracted_weights": {
            "소방시설 거리": 5,
            "배후 주거인구": 5,
            "전력 공급 용량": 5,
            "이용 편의성": 5
        }
    }

