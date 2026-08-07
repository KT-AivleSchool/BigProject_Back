# -*- coding: utf-8 -*-
"""
OmniSite DB & Redis 초기화 도구
================================
  python app/tools/reset_db_redis.py

Redis 전체 키 및 PostgreSQL/PostGIS 파이프라인 테이블을 깨끗이 초기화합니다.
"""

import sys
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine, text
from app.config import settings

def reset_redis():
    print("=" * 70)
    print(" 🟥 [Redis] 초기화 시작...")
    print("=" * 70)
    try:
        import redis
        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.ping()
        keys = r.keys("*")
        print(f"  • 삭제 대상 키: {len(keys)}개")
        if keys:
            r.flushdb()
            print("  ✅ Redis 모든 데이터 초기화(FLUSHDB) 완료!")
        else:
            print("  ⓘ Redis에 삭제할 키가 없습니다.")
    except Exception as e:
        print(f"  🔴 Redis 초기화 실패: {e}")
    print("\n")

def reset_postgres():
    print("=" * 70)
    print(" 🟦 [PostgreSQL / PostGIS] DB 초기화 시작...")
    print("=" * 70)
    try:
        engine = create_engine(settings.DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1))
        with engine.begin() as conn:
            # 1. public 스키마 내 테이블 목록 조회
            tables_query = text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name;
            """)
            # spatial_ref_sys 등 PostGIS 메타데이터 시스템 테이블 제외
            tables = [row[0] for row in conn.execute(tables_query).fetchall() if row[0] != "spatial_ref_sys"]
            
            print(f"  • 삭제 대상 테이블: {len(tables)}개 ({', '.join(tables) if tables else '없음'})")
            
            if not tables:
                print("  ⓘ PostgreSQL에 삭제할 테이블이 없습니다.")
                return

            for t in tables:
                conn.execute(text(f'DROP TABLE IF EXISTS "{t}" CASCADE;'))
                print(f"  🗑️  테이블 삭제 완료: [{t}]")

            print("  ✅ PostgreSQL/PostGIS 모든 파이프라인 테이블 초기화 완료!")
            
    except Exception as e:
        print(f"  🔴 PostgreSQL 초기화 실패: {e}")
    print("=" * 70)

if __name__ == "__main__":
    confirm = input("⚠️ WARNING: Redis 및 PostgreSQL의 모든 파이프라인 데이터가 삭제됩니다. 진행하시겠습니까? (y/N): ")
    if confirm.lower() == 'y':
        reset_redis()
        reset_postgres()
        print("\n✨ DB 및 Redis 초기화가 정상적으로 완료되었습니다.")
    else:
        print("\n[취소됨] 초기화를 진행하지 않았습니다.")
