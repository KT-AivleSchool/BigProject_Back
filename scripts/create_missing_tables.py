"""ORM 이 선언한 테이블 중 실 DB 에 **없는 것만** 만든다.

왜 필요한가 — 2026-08-07 침해로 `DROP DATABASE omnisite` 가 실행됐고,
재생성 때 `schema_cleaned_data.sql` 의 뒤쪽 3개(§17~19)가 빠졌다.
그중 `audit_rules` 가 없어서 STEP5 공청회 토론이 **첫 줄에서** `UndefinedTableError`
로 죽고 있었다(`app/api/v1/simulations.py:179`).

동작:
  - `Base.metadata.create_all(checkfirst=True)` — **이미 있는 테이블은 건드리지 않는다.**
    즉 컬럼이 다른 기존 테이블(`conflict_simulations` 등)을 고치지 않는다. 그건 별건이다.
  - 실행 전에 무엇을 만들지 **먼저 출력**한다. `--yes` 없이는 만들지 않는다.

사용:
  python scripts/create_missing_tables.py            # 계획만 출력 (dry-run)
  python scripts/create_missing_tables.py --yes      # 실제 생성
"""

import asyncio
import io
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", line_buffering=True
    )

from sqlalchemy import inspect  # noqa: E402

from app.db.base import Base  # noqa: E402  (모든 모델을 등록시키는 진입점)
from app.db.session import engine  # noqa: E402


async def main() -> int:
    apply = "--yes" in sys.argv

    async with engine.begin() as conn:
        existing = set(
            await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        )

        # ORM 이 FK 로 가리키는데 ORM 에 선언은 없는 테이블(dong_boundaries·
        # transit_stations)이 있다. SQLAlchemy 는 DB 가 아니라 **metadata** 에서
        # FK 대상을 찾으므로, 없으면 create_all 이 NoReferencedTableError 로 죽는다.
        # 실 DB 에 이미 있는 것만 골라 metadata 로 읽어들인다(생성은 안 한다).
        fk_targets = {
            fk.target_fullname.split(".")[0]
            for t in Base.metadata.tables.values()
            for c in t.columns
            for fk in c.foreign_keys
        }
        to_reflect = sorted((fk_targets & existing) - set(Base.metadata.tables))
        if to_reflect:
            print(f"[reflect] FK 대상 {to_reflect} 를 metadata 로 읽는다(생성 아님)")
            await conn.run_sync(Base.metadata.reflect, only=to_reflect)

    declared = set(Base.metadata.tables)
    missing = sorted(declared - existing)

    print(f"ORM 선언 테이블 : {len(declared)}개")
    print(f"실 DB 테이블     : {len(existing)}개")
    print(f"생성 대상        : {len(missing)}개")
    for t in missing:
        cols = ", ".join(c.name for c in Base.metadata.tables[t].columns)
        print(f"  + {t}\n      ({cols})")

    if not missing:
        print("\n생성할 것이 없다.")
        return 0

    if not apply:
        print("\n[dry-run] 실제로 만들려면 --yes 를 붙일 것.")
        return 0

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)

    async with engine.begin() as conn:
        after = set(
            await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        )

    created = sorted(after - existing)
    still = sorted(set(missing) - after)
    print(f"\n생성됨 : {len(created)}개 → {created}")
    if still:
        # 조용히 넘어가지 않는다(원칙 1).
        print(f"🔴 만들려 했으나 없는 것 : {still}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
