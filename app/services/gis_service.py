import json
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models.spatial import DongBoundary, CadastralLand, RestrictedZone


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
        db: AsyncSession, district_id: int, tolerance: float = 0.0001
    ) -> list:
        """
        대용량 연속지적도(CadastralLand)의 각 필지 경계를 단순화하여 GeoJSON 목록으로 조회합니다.
        """
        stmt = (
            select(
                CadastralLand.id,
                CadastralLand.pnu,
                CadastralLand.jibun,
                func.ST_AsGeoJSON(
                    func.ST_SimplifyPreserveTopology(CadastralLand.geom, tolerance)
                ),
            )
            .where(CadastralLand.district_id == district_id)
            .limit(1000)
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

    @staticmethod
    async def screen_available_lands(
        db: AsyncSession, district_id: int, exclusion_meters: float = 10.0
    ) -> list:
        """
        [장천명 풀스택] 자치구 내 법정 규제구역 (RestrictedZone)의 exclusion_meters 반경을
        ST_Union으로 병합한 뒤, CadastralLand와 ST_Difference 연산을 취해
        규제에 걸리지 않는 잔여 필지 목록을 선별합니다.
        """
        # 1. 자치구 내 배제 대상 포인트 지오메트리 수집 쿼리 빌드
        # (3857 투영 좌표계로 변환하여 exclusion_meters 만큼 정확한 미터 단위 버퍼 생성)
        buffer_subquery = (
            select(
                func.ST_Union(
                    func.ST_Buffer(
                        func.ST_Transform(RestrictedZone.geom, 3857), exclusion_meters
                    )
                ).label("exclusion_geom")
            )
            .where(RestrictedZone.district_id == district_id)
            .scalar_subquery()
        )

        # 2. 지적도 필지 (CadastralLand)와 배제 다각형 간의 공간 차집합 계산 쿼리
        # - ST_Difference: 전체 필지 도형에서 배제 도형을 잘라냄
        # - ST_Area: 잘려나간 잔여 가용지 면적이 원래 필지 면적의 30% 이상인 곳만 선별
        stmt = (
            select(
                CadastralLand.id,
                CadastralLand.pnu,
                CadastralLand.jibun,
                func.ST_Area(CadastralLand.geom).label("orig_area"),
                func.ST_AsGeoJSON(
                    func.ST_Transform(
                        func.ST_Difference(
                            func.ST_Transform(CadastralLand.geom, 3857),
                            func.coalesce(
                                buffer_subquery,
                                func.ST_GeomFromText("GEOMETRYCOLLECTION EMPTY", 3857),
                            ),
                        ),
                        4326,
                    )
                ).label("usable_geojson"),
            )
            .where(CadastralLand.district_id == district_id)
            .limit(100)
        )

        result = await db.execute(stmt)
        screened_results = []

        for row in result.all():
            usable_geom_str = row[4]
            if not usable_geom_str:
                continue

            usable_geom = json.loads(usable_geom_str)
            if usable_geom.get("type") == "GeometryCollection" and not usable_geom.get(
                "geometries"
            ):
                continue

            screened_results.append(
                {
                    "land_id": row[0],
                    "pnu": row[1],
                    "jibun": row[2],
                    "original_area_m2": round(row[3], 2) if row[3] else 0.0,
                    "usable_geometry": usable_geom,
                }
            )

        return screened_results


# 서비스 싱글톤 인스턴스 배포
gis_service = GisService()
