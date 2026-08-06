import json
from sqlalchemy import func, select, case
from sqlalchemy.ext.asyncio import AsyncSession

# spatial.py는 PR #59(feature/#9-postgis-orm-models) 머지 후 활성화되는 의존성 모델입니다.
# PR #59 머지 전 환경에서도 서버가 기동될 수 있도록 조건부 임포트 처리합니다.
try:
    from app.db.models.spatial import DongBoundary, CadastralLand, RestrictedZone
except ImportError:
    DongBoundary = None  # type: ignore
    CadastralLand = None  # type: ignore
    RestrictedZone = None  # type: ignore


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
        geojson_str = result.scalar()

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

    @staticmethod
    async def screen_available_lands(
        db: AsyncSession, district_id: int, exclusion_meters: float = 10.0
    ) -> list:
        """
        [장천명 풀스택] 자치구 내 법정 규제구역(RestrictedZone)의 exclusion_meters 반경을
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

        # 2. 지적도 필지(CadastralLand)와 배제 다각형 간의 공간 차집합 계산 쿼리
        # - [성능 최적화] case 절을 사용하여 규제 버퍼 구역과 공간적으로 교차(ST_Intersects)하는 필지만
        #   비싼 ST_Difference 차집합 연산을 수행하고, 겹치지 않는 대다수의 필지는 원본 도형 그대로 리턴합니다.
        # - [비즈니스 가드 추가] 잘려나간 잔여 가용지 면적이 원래 필지 면적의 30% 이상인 곳만 선별합니다.
        geom_3857 = func.ST_Transform(CadastralLand.geom, 3857)
        buffer_empty = func.coalesce(
            buffer_subquery,
            func.ST_GeomFromText("GEOMETRYCOLLECTION EMPTY", 3857),
        )

        diff_geom = case(
            (
                func.ST_Intersects(geom_3857, buffer_empty),
                func.ST_Difference(geom_3857, buffer_empty),
            ),
            else_=geom_3857,
        )

        stmt = (
            select(
                CadastralLand.id,
                CadastralLand.pnu,
                CadastralLand.jibun,
                func.ST_Area(CadastralLand.geom).label("orig_area"),
                func.ST_AsGeoJSON(func.ST_Transform(diff_geom, 4326)).label(
                    "usable_geojson"
                ),
            )
            .where(CadastralLand.district_id == district_id)
            .where((func.ST_Area(diff_geom) / func.ST_Area(geom_3857)) >= 0.3)
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

    @staticmethod
    async def get_poi_context_from_db(db: AsyncSession, parcel_id: int) -> str:
        """
        [DB Real-time] PostGIS를 이용해 필지(Parcel) 주변의 주요 시설물을 조회합니다.
        """
        from app.db.models.simulation import Parcel
        from app.db.models.spatial import (
            BusStop,
            StreetTrashBin,
            Park,
            CigaretteLitterHotspot,
            FireWaterFacility,
            SmokingArea,
        )
        from sqlalchemy import select, func

        try:
            # geom에서 위경도를 추출합니다.
            result = await db.execute(
                select(
                    Parcel,
                    func.ST_Y(Parcel.geom).label("lat"),
                    func.ST_X(Parcel.geom).label("lng"),
                ).where(Parcel.id == parcel_id)
            )
            row = result.first()
            if not row:
                return "필지 정보를 찾을 수 없습니다."

            parcel = row[0]
            lng = row.lng
            lat = row.lat

            # 타겟 포인트 지오메트리 생성 (경도, 위도) EPSG:4326 -> 3857 투영
            target_geom_3857 = func.ST_Transform(
                func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326), 3857
            )

            context_lines = []
            search_radius_m = 300

            # 1. 버스정류장
            stmt_bus = (
                select(
                    BusStop.stop_name,
                    func.ST_Distance(
                        func.ST_Transform(BusStop.geom, 3857), target_geom_3857
                    ).label("dist"),
                )
                .where(
                    func.ST_DWithin(
                        func.ST_Transform(BusStop.geom, 3857),
                        target_geom_3857,
                        search_radius_m,
                    )
                )
                .order_by("dist")
                .limit(1)
            )
            for row in (await db.execute(stmt_bus)).all():
                context_lines.append(
                    f"📍 대중교통: 가장 가까운 버스정류장({row.stop_name})까지 약 {int(row.dist)}m 거리"
                )

            # 2. 공원
            stmt_park = (
                select(
                    Park.facility_name,
                    func.ST_Distance(
                        func.ST_Transform(Park.geom, 3857), target_geom_3857
                    ).label("dist"),
                )
                .where(
                    func.ST_DWithin(
                        func.ST_Transform(Park.geom, 3857),
                        target_geom_3857,
                        search_radius_m,
                    )
                )
                .order_by("dist")
                .limit(1)
            )
            for row in (await db.execute(stmt_park)).all():
                context_lines.append(
                    f"📍 시민공간: 가장 가까운 공원({row.facility_name})까지 약 {int(row.dist)}m 거리"
                )

            # 3. 쓰레기통
            stmt_trash = (
                select(
                    func.ST_Distance(
                        func.ST_Transform(StreetTrashBin.geom, 3857), target_geom_3857
                    ).label("dist"),
                )
                .where(
                    func.ST_DWithin(
                        func.ST_Transform(StreetTrashBin.geom, 3857),
                        target_geom_3857,
                        search_radius_m,
                    )
                )
                .order_by("dist")
                .limit(1)
            )
            for row in (await db.execute(stmt_trash)).all():
                context_lines.append(
                    f"📍 기존인프라: 가장 가까운 가로 쓰레기통까지 약 {int(row.dist)}m 거리"
                )

            # 4. 상습 무단투기 구역
            stmt_litter = (
                select(
                    func.ST_Distance(
                        func.ST_Transform(CigaretteLitterHotspot.geom, 3857),
                        target_geom_3857,
                    ).label("dist"),
                )
                .where(
                    func.ST_DWithin(
                        func.ST_Transform(CigaretteLitterHotspot.geom, 3857),
                        target_geom_3857,
                        search_radius_m,
                    )
                )
                .order_by("dist")
                .limit(1)
            )
            for row in (await db.execute(stmt_litter)).all():
                context_lines.append(
                    f"📍 현장상황: 가장 가까운 상습 무단투기 구역까지 약 {int(row.dist)}m 거리"
                )

            # 5. 기존 흡연구역
            stmt_smoking = (
                select(
                    func.ST_Distance(
                        func.ST_Transform(SmokingArea.geom, 3857), target_geom_3857
                    ).label("dist"),
                )
                .where(
                    func.ST_DWithin(
                        func.ST_Transform(SmokingArea.geom, 3857),
                        target_geom_3857,
                        search_radius_m,
                    )
                )
                .order_by("dist")
                .limit(1)
            )
            for row in (await db.execute(stmt_smoking)).all():
                context_lines.append(
                    f"📍 기존흡연구역: 가장 가까운 기존 흡연구역까지 약 {int(row.dist)}m 거리"
                )

            # 6. 소방용수시설
            stmt_fire = (
                select(
                    func.ST_Distance(
                        func.ST_Transform(FireWaterFacility.geom, 3857),
                        target_geom_3857,
                    ).label("dist"),
                )
                .where(
                    func.ST_DWithin(
                        func.ST_Transform(FireWaterFacility.geom, 3857),
                        target_geom_3857,
                        search_radius_m,
                    )
                )
                .order_by("dist")
                .limit(1)
            )
            for row in (await db.execute(stmt_fire)).all():
                context_lines.append(
                    f"📍 소방/안전: 가장 가까운 소방용수시설까지 약 {int(row.dist)}m 거리"
                )

            if context_lines:
                return "\n".join([f"- {msg}" for msg in context_lines])
            return "반경 300m 이내에 인접한 주요 POI(대중교통, 공원, 쓰레기통 등)가 없습니다."

        except Exception as e:
            print(f"POI Context Load Error (DB): {e}")
            import traceback

            traceback.print_exc()
            await db.rollback()
            return ""


# 서비스 싱글톤 인스턴스 배포
gis_service = GisService()
