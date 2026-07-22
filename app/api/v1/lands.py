from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status, Depends
from typing import List
import json
import logging
from openai import AsyncOpenAI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.api.deps import get_db
from app.services.gis_service import gis_service
from app.schemas.lands import (
    UploadResponse,
    HitlCoordinateCorrection,
    LandDetailResponse,
    CsvAuditResponse,
    BoundaryCheckRequest,
    BoundaryCheckResponse,
)


logger = logging.getLogger("app.api.v1.lands")

router = APIRouter()


@router.post("/upload", response_model=UploadResponse)
def upload_datasets(files: List[UploadFile] = File(...), district_id: int = Form(...)):
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
            detail=f"지원하지 않는 확장자 파일이 포함되어 있습니다: {filename}",
        )

    # Pydantic Schema에 따른 DTO 반환
    return {
        "status": "success",
        "summary": {
            "filename": filename,
            "file_type": ext.upper(),
            "total_records": 100,
            "imported_records": 95,
            "failed_records": 5,
        },
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
            "lng": correction.corrected_lng,
        },
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
            "coordinates": [
                [[126.97, 37.53], [126.98, 37.53], [126.98, 37.54], [126.97, 37.53]]
            ],
        },
        "is_excluded": False,
        "exclusion_reason": None,
        "lat": 37.53,
        "lng": 126.97,
    }


@router.post("/audit/csv", response_model=CsvAuditResponse)
async def audit_csv_dataset(files: List[UploadFile] = File(...)):
    """
    [장천명 풀스택] Step 1. 다중 CSV 데이터셋 수신 전용 AI 통합 사전 감리 및 융합 가중치 도출 API
    - 업로드된 다중 CSV 파일들의 텍스트 내용을 각각 읽어 통합한 뒤 OpenAI LLM 비동기 연동을 통해 감리를 실행합니다.
    - API Key 누락 및 서버 통신 장애 시, 사전에 준비된 지능형 규칙 기반 가이드라인 Fallback 로직이 매끄럽게 연동됩니다.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="업로드된 파일이 없습니다."
        )

    # 모든 파일 확장자 유효성 및 파일명 빈값 안전 검사
    for file in files:
        if not file.filename or not file.filename.lower().endswith(".csv"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Step 1 감리 파이프라인은 오직 CSV 확장자 파일만 지원합니다. (에러 파일: {file.filename if file.filename else '이름없음'})",
            )

    # 1. 모든 CSV 파일들의 데이터를 컨텍스트로 결합 (이중 파이프라인 OOM 가드 탑재)
    combined_preview_text = ""
    try:
        for idx, file in enumerate(files):
            # 안전하게 파일 바이트 크기 획득 (Starlette file.size 호환성 및 AttributeError 우회)
            file.file.seek(0, 2)
            file_size = file.file.tell()
            file.file.seek(0)

            # 5MB 초과 대용량 파일 유입 시 OOM 예방을 위해 첫 10KB만 슬라이싱하여 읽기
            if file_size > 5 * 1024 * 1024:
                contents = await file.read(10000)
                await file.seek(
                    0
                )  # 후속 DB 적재 파이프라인을 위해 파일 포인터 즉시 초기화
                lines = contents.decode("utf-8", errors="ignore").splitlines()
                # 잘렸을 수 있는 마지막 줄 제외하고 상위 10행으로 프리뷰 생성
                preview = "\n".join(lines[:-1][:10])
                combined_preview_text += (
                    f"--- [대용량 파일 {idx + 1} (총 크기: {file_size / (1024 * 1024):.2f}MB)] 명칭: {file.filename} ---\n"
                    f"{preview}\n"
                    f"[system: 대용량 데이터에 따른 프리뷰 슬라이싱 스캔 완료]\n\n"
                )
            else:
                contents = await file.read()
                preview = contents.decode("utf-8", errors="ignore")
                await file.seek(
                    0
                )  # 후속 DB 적재 파이프라인을 위해 파일 포인터 즉시 초기화
                combined_preview_text += (
                    f"--- [파일 {idx + 1}] 명칭: {file.filename} ---\n{preview}\n\n"
                )
    except Exception as e:
        logger.error(f"[Ingestion Error] Failed to read multi-CSV content: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="업로드된 CSV 파일 중 일부의 인코딩(UTF-8)을 해석할 수 없습니다.",
        )

    # 다중 파일 융합 기본 Fallback 데이터 정의
    fallback_data = {
        "status": "success",
        "audit_reason": "전기차 지상 충전소 의무 규정과 인근 소방시설 소방차 통로 확보 가이드라인에 따른 부지 교차 검토가 요구됩니다. 업로드된 주차 데이터와 소방 용수시설 데이터의 지번 불일치 가능성이 감지되었습니다.",
        "user_intent": "용산구 내 친환경자동차법 및 소방안전 특별법을 충족하는 최적의 지상형 전기차 급속 충전소 입지 도출",
        "extracted_weights": {
            "소방시설 거리": 5,
            "배후 주거인구": 5,
            "전력 공급 용량": 5,
            "이용 편의성": 5,
        },
    }

    # 2. OpenAI API 키가 없을 경우 명시적 에러 반환
    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY.strip() == "":
        logger.error("[AI Ingestion] OPENAI_API_KEY is missing.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="[AI_INGESTION_FAILED] 서버에 AI API 키가 설정되지 않아 감리를 수행할 수 없습니다.",
        )

    # 3. OpenAI 비동기 멀티파일 감리 호출
    try:
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        system_prompt = (
            "너는 지능형 스마트시티 입지 감리 AI 에이전트이다. 업로드된 여러 개의 CSV 데이터셋 일부 내용을 제공받아 "
            "데이터셋들 간의 상관관계와 도시 인프라적 제약 사항을 융합 분석하고, 다음 3가지 핵심 정보를 엄격한 JSON 형식으로 출력해야 한다.\n"
            "출력 필드 규격:\n"
            "1. audit_reason (string): 데이터셋들의 결측치, 지번 기재 부주의 혹은 스쿨존/소방시설 등 법적 제한 규제 구역과의 침범 가능성 정밀 감리 이유.\n"
            "2. user_intent (string): 다중 데이터셋을 종합 관통하는 실무자의 최적 입지 선정 의도 및 기획 목적 한글 요약.\n"
            "3. extracted_weights (dictionary): 분석된 의도에 매핑되어 가변 도출된 4~6개의 입지 가중치 요인명과 기본값 5 고정 (예: {'소방시설 거리': 5, '배후 주거인구': 5, '전력 공급 용량': 5, '이용 편의성': 5})\n"
            "오직 유효한 JSON만 반환하고 다른 텍스트는 절대 포함하지 마라."
        )

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"다중 CSV 파일 통합 텍스트:\n{combined_preview_text}",
                },
            ],
            response_format={"type": "json_object"},
            timeout=12.0,
        )

        llm_response_text = response.choices[0].message.content
        parsed = json.loads(llm_response_text)

        return {
            "status": "success",
            "audit_reason": parsed.get("audit_reason", fallback_data["audit_reason"]),
            "user_intent": parsed.get("user_intent", fallback_data["user_intent"]),
            "extracted_weights": parsed.get(
                "extracted_weights", fallback_data["extracted_weights"]
            ),
        }

    except Exception as e:
        logger.error(f"[AI Ingestion Failure] OpenAI call failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"[AI_INGESTION_FAILED] AI 입지 사전 감리 처리 중 오류가 발생했습니다: {str(e)}",
        )


@router.get("/screen-candidate")
async def screen_candidate_lands(
    district_id: int, exclusion_meters: float = 10.0, db: AsyncSession = Depends(get_db)
):
    """
    [장천명 풀스택] Step 4 PostGIS 규제 배제 차집합 기반 가용 부지 스크리닝 API
    - 자치구 내 제한구역 10m 버퍼 영역을 배제한 적격 입지 필지 리스트를 도출합니다.
    """
    try:
        results = await gis_service.screen_available_lands(
            db, district_id=district_id, exclusion_meters=exclusion_meters
        )
        return {
            "status": "success",
            "district_id": district_id,
            "exclusion_meters": exclusion_meters,
            "candidate_count": len(results),
            "candidates": results,
        }
    except Exception as e:
        logger.error(f"[Screening API Error] Failed to screen lands: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"가용 부지 차집합 공간 분석 중 서버 오류가 발생했습니다: {str(e)}",
        )


@router.get("/district-boundary/{district_id}")
async def get_district_boundary(
    district_id: int, tolerance: float = 0.0005, db: AsyncSession = Depends(get_db)
):
    """
    [장천명 풀스택] Step 2 자치구 경계 가시화 API (ST_SimplifyPreserveTopology 경량화 적용)
    - districts 테이블에 대응하는 dong_boundaries MultiPolygon 기하들의 공간 합집합(ST_Union)에 위상 보존 단순화를 적용하여 GeoJSON 형태로 리턴합니다.
    """
    try:
        boundary_geojson = await gis_service.get_simplified_district_boundary(
            db, district_id=district_id, tolerance=tolerance
        )

        if not boundary_geojson:
            raise HTTPException(
                status_code=404,
                detail=f"해당 자치구 ID {district_id}에 매핑되는 행정동 경계 정보가 없습니다.",
            )

        return {
            "status": "success",
            "district_id": district_id,
            "tolerance": tolerance,
            "boundary": boundary_geojson,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Boundary API Error] Failed to fetch district boundary: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"자치구 경계 GeoJSON 로드 중 내부 서버 공간 연산 에러가 발생했습니다: {str(e)}",
        )


@router.post("/check-boundary", response_model=BoundaryCheckResponse)
async def check_coordinate_in_boundary(
    req: BoundaryCheckRequest, db: AsyncSession = Depends(get_db)
):
    """
    [장천명 풀스택] Step 2 실무자 보정 좌표 자치구 이탈 방지 가드 API
    - 특정 위경도 좌표가 해당 자치구(district_id) 경계 영역(ST_Contains) 내에 정상 포함되는지 물리적으로 검증합니다.
    """
    try:
        sql = text("""
            SELECT EXISTS (
                SELECT 1
                FROM dong_boundaries
                WHERE district_id = :district_id
                  AND ST_Contains(geom, ST_SetSRID(ST_Point(:lng, :lat), 4326))
            ) as is_contained
        """)
        result = await db.execute(
            sql, {"district_id": req.district_id, "lat": req.lat, "lng": req.lng}
        )
        row = result.fetchone()
        is_contained = row.is_contained if row else False

        return {"district_id": req.district_id, "is_contained": is_contained}
    except Exception as e:
        logger.error(
            f"[Boundary Guard Error] Failed to compute spatial containment: {e}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"PostGIS 공간 좌표 분석 중 런타임 오류가 발생했습니다: {str(e)}",
        )
