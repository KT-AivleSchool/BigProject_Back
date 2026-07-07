from pydantic import BaseModel, Field
from typing import List, Optional

class FileMetadata(BaseModel):
    filename: str = Field(..., description="업로드된 파일 명")
    file_type: str = Field(..., description="파일 확장자/타입 (CSV, SHP, PDF 등)")
    total_records: int = Field(..., description="파싱된 총 데이터 수 (CSV 기준)")
    imported_records: int = Field(..., description="DB에 정상 적재된 데이터 수")
    failed_records: int = Field(..., description="지오코딩 실패 또는 데이터 결측으로 보정이 필요한 수")

class UploadResponse(BaseModel):
    status: str = Field("success", description="처리 결과 상태")
    summary: FileMetadata = Field(..., description="업로드 요약 메타데이터")

class HitlCoordinateCorrection(BaseModel):
    parcel_id: int = Field(..., description="좌표 보정이 필요한 필지 고유 ID")
    corrected_address: Optional[str] = Field(None, description="수정 보정된 지번 주소")
    corrected_lat: float = Field(..., ge=-90.0, le=90.0, description="보정된 위도 좌표")
    corrected_lng: float = Field(..., ge=-180.0, le=180.0, description="보정된 경도 좌표")

class LandDetailResponse(BaseModel):
    parcel_id: int = Field(..., description="필지 고유 ID")
    address: str = Field(..., description="지번 주소")
    geometry_geojson: dict = Field(..., description="필지 공간 경계 데이터 (GeoJSON)")
    is_excluded: bool = Field(..., description="법정 규제 구역 포함 여부 (배제 여부)")
    exclusion_reason: Optional[str] = Field(None, description="배제 사유 (예: 어린이보호구역 200m 이내)")
    lat: float = Field(..., description="위도")
    lng: float = Field(..., description="경도")
