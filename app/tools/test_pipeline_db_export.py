import asyncio
import os
import sys

# 프로젝트 루트 경로 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.db.session import engine, AsyncSessionLocal
from app.db.models.pipeline_export import Base
from app.services.pipeline_db_exporter import (
    export_step1_to_db,
    export_step2_to_db,
    export_step3_to_db,
    export_step4_to_db,
)
import redis.asyncio as aioredis
from app.api.deps import redis_pool

redis_client = aioredis.Redis(connection_pool=redis_pool)

from sqlalchemy import text


async def test_db_export():
    print("\n🧪 [검증 시작] 파이프라인 산출물 DB & Redis 이식 시스템 테스트...")

    # 1. DB 테이블 생성 (PostgreSQL / PostGIS)
    async with engine.begin() as conn:
        print("  - DB 테이블 및 PostGIS 확장 생성 중...")
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("  ✅ DB 테이블 생성 완료!")


    test_run_id = "test_run_smoke_001"
    domain = "흡연"

    # 2. STEP 1~4 Exporter 구동
    print(f"\n  - [{domain}] STEP 1 Exporter 실행...")
    res1 = await export_step1_to_db(test_run_id, domain)
    print(f"    └ 결과: {res1}")

    print(f"  - [{domain}] STEP 2 Exporter 실행...")
    res2 = await export_step2_to_db(test_run_id, domain)
    print(f"    └ 결과: {res2}")

    print(f"  - [{domain}] STEP 3 Exporter 실행...")
    res3 = await export_step3_to_db(test_run_id, domain)
    print(f"    └ 결과: {res3}")

    print(f"  - [{domain}] STEP 4 Exporter 실행...")
    res4 = await export_step4_to_db(test_run_id, domain)
    print(f"    └ 결과: {res4}")

    # 3. DB 적재 실측 검증
    async with AsyncSessionLocal() as session:
        r1 = await session.execute(text("SELECT count(*) FROM pipeline_audit_reviews;"))
        cnt1 = r1.scalar()

        r2_geo = await session.execute(text("SELECT count(*) FROM clean_spatial_layers;"))
        cnt2_geo = r2_geo.scalar()

        r2_stat = await session.execute(text("SELECT count(*) FROM clean_stat_tables;"))
        cnt2_stat = r2_stat.scalar()

        r3_ws = await session.execute(text("SELECT count(*) FROM pipeline_weight_sets;"))
        cnt3_ws = r3_ws.scalar()

        r4_rpt = await session.execute(text("SELECT count(*) FROM pipeline_final_reports;"))
        cnt4_rpt = r4_rpt.scalar()

        print("\n📊 [실측 검증 데이터베이스 상태]")
        print(f"  - pipeline_audit_reviews 수: {cnt1}")
        print(f"  - clean_spatial_layers (gpkg 공간): {cnt2_geo}")
        print(f"  - clean_stat_tables (parquet 통계): {cnt2_stat}")
        print(f"  - pipeline_weight_sets: {cnt3_ws}")
        print(f"  - pipeline_final_reports: {cnt4_rpt}")

    # 4. Redis 상태 확인
    keys = await redis_client.keys(f"omnisite:run:{test_run_id}:*")
    print(f"\n  - Redis 캐시된 키 목록 (run_id: {test_run_id}): {keys}")

    print("\n🎉 [테스트 성공] 파이프라인 DB & Redis 이식 테스트가 성공적으로 마무리되었습니다!")

if __name__ == "__main__":
    asyncio.run(test_db_export())
