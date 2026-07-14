from pydantic import BaseModel, Field
from typing import Optional


class FileMetadata(BaseModel):
    filename: str = Field(..., description="업로드된 파일 명")
    file_type: str = Field(..., description="파일 확장자/타입 (CSV, SHP, PDF 등)")
    total_records: int = Field(..., description="파싱된 총 데이터 수 (CSV 기준)")
    imported_records: int = Field(..., description="DB에 정상 적재된 데이터 수")
    failed_records: int = Field(
        ..., description="지오코딩 실패 또는 데이터 결측으로 보정이 필요한 수"
    )


class UploadResponse(BaseModel):
    status: str = Field("success", description="처리 결과 상태")
    summary: FileMetadata = Field(..., description="업로드 요약 메타데이터")


class HitlCoordinateCorrection(BaseModel):
    parcel_id: int = Field(..., description="좌표 보정이 필요한 필지 고유 ID")
    corrected_address: Optional[str] = Field(None, description="수정 보정된 지번 주소")
    corrected_lat: float = Field(..., ge=-90.0, le=90.0, description="보정된 위도 좌표")
    corrected_lng: float = Field(
        ..., ge=-180.0, le=180.0, description="보정된 경도 좌표"
    )


class LandDetailResponse(BaseModel):
    parcel_id: int = Field(..., description="필지 고유 ID")
    address: str = Field(..., description="지번 주소")
    geometry_geojson: dict = Field(..., description="필지 공간 경계 데이터 (GeoJSON)")
    is_excluded: bool = Field(..., description="법정 규제 구역 포함 여부 (배제 여부)")
    exclusion_reason: Optional[str] = Field(
        None, description="배제 사유 (예: 어린이보호구역 200m 이내)"
    )
    lat: float = Field(..., description="위도")
    lng: float = Field(..., description="경도")


class CsvAuditResponse(BaseModel):
    status: str = Field("success", description="처리 결과 상태")
    audit_reason: str = Field(..., description="AI가 감리한 결측 및 규제 제한 사유")
    user_intent: str = Field(
        ..., description="사용자가 추출하고자 하는 탐색 의도 및 목적"
    )
    extracted_weights: dict = Field(
        ..., description="의도에 따른 가중치 슬라이더 후보 항목들 (딕셔너리)"
    )


class BoundaryCheckRequest(BaseModel):
    """HITL 보정 좌표의 자치구 경계 이탈 여부를 검증하기 위한 요청 스키마"""

    district_id: int = Field(..., description="검증할 자치구 고유 ID")
    lat: float = Field(..., ge=-90.0, le=90.0, description="위도 좌표")
    lng: float = Field(..., ge=-180.0, le=180.0, description="경도 좌표")


class BoundaryCheckResponse(BaseModel):
    """자치구 경계 이탈 여부 검증 결과 응답 스키마"""

    district_id: int = Field(..., description="검증을 수행한 자치구 ID")
    is_contained: bool = Field(
        ...,
        description="경계 포함 여부 (True: 자치구 내 안전 위치, False: 자치구 이탈)",
    )


class SimplifiedLandsRequest(BaseModel):
    """프론트엔드 뷰포트 Bounding Box 기반 지적도 조회 요청 스키마"""

    min_lat: float = Field(..., description="뷰포트 최소 위도 (남쪽 경계)")
    max_lat: float = Field(..., description="뷰포트 최대 위도 (북쪽 경계)")
    min_lng: float = Field(..., description="뷰포트 최소 경도 (서쪽 경계)")
    max_lng: float = Field(..., description="뷰포트 최대 경도 (동쪽 경계)")
    tolerance: float = Field(0.0001, description="ST_SimplifyPreserveTopology 오차 허용 범위")

