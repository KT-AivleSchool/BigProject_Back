import json
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.db.models.spatial import DongBoundary, CadastralLand


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


# 서비스 싱글톤 인스턴스 배포
gis_service = GisService()
