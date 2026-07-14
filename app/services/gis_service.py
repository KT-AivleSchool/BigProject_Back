import json
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

# spatial.py는 PR #59(feature/#9-postgis-orm-models) 머지 후 활성화되는 의존성 모델입니다.
# PR #59 머지 전 환경에서도 서버가 기동될 수 있도록 조건부 임포트 처리합니다.
try:
    from app.db.models.spatial import DongBoundary, CadastralLand
except ImportError:
    DongBoundary = None  # type: ignore
    CadastralLand = None  # type: ignore


class GisService:
    @staticmethod
    async def get_simplified_district_boundary(
        db: AsyncSession, district_id: int, tolerance: float = 0.0005
    ) -> dict:
        """
        [장천명 풀스택] 자치구 내 행정동들의 공간 합집합(ST_Union)을 구한 뒤,
        ST_SimplifyPreserveTopology를 적용해 경량화된 외곽선 GeoJSON으로 반환합니다.
        """
        stmt = select(
            func.ST_AsGeoJSON(
                func.ST_SimplifyPreserveTopology(
                    func.ST_Union(DongBoundary.geom), tolerance
                )
            )
        ).where(DongBoundary.district_id == district_id)

        result = await db.execute(stmt)
        geojson_str = result.scalar_first()

        if not geojson_str:
            return {}

        return json.loads(geojson_str)

    @staticmethod
    async def get_simplified_lands(
        db: AsyncSession,
        min_lat: float,
        max_lat: float,
        min_lng: float,
        max_lng: float,
        tolerance: float = 0.0001,
    ) -> list:
        """
        대용량 연속지적도(CadastralLand)의 각 필지 경계를 단순화하여 GeoJSON 목록으로 조회합니다.
        - [개선] 프론트엔드 뷰포트 Bounding Box(min/max lat/lng) 파라미터를 수신하여
          ST_Intersects 공간 교차 필터로 화면 내 필지만 조회합니다.
          기존 .limit(1000) Hard Limit을 제거하여 지역별 필지 누락 현상을 방지합니다.
        """
        # 뷰포트 Bounding Box를 PostGIS 공간 경계 사각형(ST_MakeEnvelope)으로 변환
        bbox_envelope = func.ST_MakeEnvelope(min_lng, min_lat, max_lng, max_lat, 4326)

        stmt = select(
            CadastralLand.id,
            CadastralLand.pnu,
            CadastralLand.jibun,
            func.ST_AsGeoJSON(
                func.ST_SimplifyPreserveTopology(CadastralLand.geom, tolerance)
            ),
        ).where(
            # 화면 내 필지만 조회 (ST_Intersects 공간 인덱스 활용)
            func.ST_Intersects(CadastralLand.geom, bbox_envelope)
        )

        result = await db.execute(stmt)
        lands_list = []
        for row in result.all():
            lands_list.append(
                {
                    "id": row[0],
                    "pnu": row[1],
                    "jibun": row[2],
                    "geometry": json.loads(row[3]) if row[3] else None,
                }
            )
        return lands_list


# 서비스 싱글톤 인스턴스 배포
gis_service = GisService()
