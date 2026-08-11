# -*- coding: utf-8 -*-
"""
OmniSite DB & Redis 초기화 도구
================================
  계획만 출력(기본):  python app/tools/reset_db_redis.py
  실제 삭제:          python app/tools/reset_db_redis.py --yes [--redis-only|--db-only]

🔴 **이 도구는 `public` 스키마의 테이블을 전부 DROP 한다.** "파이프라인 테이블"만
   지우는 게 아니다 — 2026-08-10 실측 기준 39테이블 707MB 이고 여기엔 다시 만드는 데
   몇 시간 걸리는 지적도·경계·정제 데이터가 들어 있다(`omnisite_seed.sql.gz` 로도
   경계 3종은 안 돌아온다. `scripts/load_region_boundaries.py` 를 따로 돌려야 한다).
   원 PR(#220)은 이걸 `input()` 한 번으로 지웠고 문구는 "파이프라인 데이터"였다 —
   범위를 축소해서 말하는 확인 문구는 확인이 아니다(원칙 4).

   그래서 저장소 관례(`scripts/create_missing_tables.py`·`load_topn_candidates.py`)를
   따라 **계획만 출력이 기본**이고 `--yes` 로만 실행한다.
   실패는 삼키지 않고 그대로 터뜨린다 — "초기화 실패" 를 print 하고 rc=0 으로 끝나면
   지워진 줄 알고 다음 단계를 밟는다(원칙 1).
"""

import argparse
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import DB_CONNECT_TIMEOUT, settings  # noqa: E402

# PostGIS 메타데이터. 지우면 공간 타입이 통째로 깨진다.
_SYSTEM_TABLES = {"spatial_ref_sys"}


def _redis_client():
    import redis

    r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    r.ping()
    return r


def _db_tables(engine):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, "
                "       pg_total_relation_size(quote_ident(table_name)) AS bytes "
                "  FROM information_schema.tables "
                " WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                " ORDER BY 2 DESC"
            )
        ).fetchall()
    return [(t, b) for t, b in rows if t not in _SYSTEM_TABLES]


def main() -> None:
    p = argparse.ArgumentParser(description="OmniSite DB·Redis 초기화")
    p.add_argument("--yes", action="store_true", help="실제로 삭제한다. 없으면 계획만 출력")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--redis-only", action="store_true")
    g.add_argument("--db-only", action="store_true")
    a = p.parse_args()

    do_redis = not a.db_only
    do_db = not a.redis_only
    engine = None

    print("=" * 74)
    print(f" 삭제 계획  (모드: {'🔴 실행' if a.yes else 'dry-run — 아무것도 안 지운다'})")
    print("=" * 74)

    if do_redis:
        keys = sorted(_redis_client().keys("*"))
        print(f"\n[Redis]  FLUSHDB — 키 {len(keys)}개")
        for k in keys[:20]:
            print(f"    • {k}")
        if len(keys) > 20:
            print(f"    … 외 {len(keys) - 20}개")

    if do_db:
        engine = create_engine(
            settings.DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1),
            # 🔴 psycopg 는 `connect_timeout`(libpq 이름)이다. asyncpg 의 `timeout` 을
            #    넘기면 조용히 무시되고 DB 부재를 260초 뒤에야 안다(2026-08-10 실측).
            connect_args={"connect_timeout": DB_CONNECT_TIMEOUT},
        )
        tables = _db_tables(engine)
        total = sum(b for _, b in tables)
        print(f"\n[PostgreSQL]  DROP TABLE CASCADE — {len(tables)}개 · {total / 1024 / 1024:.1f} MB")
        for t, b in tables:
            print(f"    • {t:<40} {b / 1024 / 1024:>10.1f} MB")
        print(f"    (제외: {', '.join(sorted(_SYSTEM_TABLES))})")

    if not a.yes:
        print("\n" + "=" * 74)
        print(" dry-run 이라 아무것도 지우지 않았다. 실제로 지우려면 --yes 를 붙인다.")
        print("=" * 74)
        return

    print("\n" + "=" * 74)
    typed = input(" 위 전부를 지운다. 되돌릴 수 없다. 진행하려면 'DELETE' 를 입력: ")
    if typed.strip() != "DELETE":
        raise SystemExit("[취소됨] 입력이 'DELETE' 가 아니다.")

    if do_redis:
        _redis_client().flushdb()
        print("  ✅ Redis FLUSHDB 완료")

    if do_db:
        with engine.begin() as conn:
            for t, _ in _db_tables(engine):
                conn.execute(text(f'DROP TABLE IF EXISTS "{t}" CASCADE;'))
                print(f"  🗑️  {t}")
        print("  ✅ PostgreSQL DROP 완료")

    print("\n재구축: docker compose down -v && docker compose up -d")
    print("        python scripts/create_missing_tables.py --yes")
    print("        python scripts/load_region_boundaries.py   (경계 3종은 seed 에 없다)")


if __name__ == "__main__":
    main()
