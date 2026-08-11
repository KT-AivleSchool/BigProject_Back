# -*- coding: utf-8 -*-
"""
연속지적도(LSMD) 적재 — 2계층 지역 단위 (popzap #203 3️⃣).

- DDL: schema_cadastral.sql 실행 (CREATE IF NOT EXISTS)
- 원본 SHP(5186, 44,459필지) → staging(4326) → cadastral_lands
  (44k 폴리곤이라 staging 경유로 MakeValid/Multi 를 DB 셋 연산으로 처리 = 빠름)
- geom(4326) + geom_5186(GENERATED), invalid 는 ST_MakeValid 처리
- 멱등 가드: 해당 시군구코드가 이미 있으면 skip

원본: datasets/region_data/LSMD_CONT_LDREG_<시군구코드>_<YYYYMM>.shp
사용:
  python scripts/load_cadastral.py            # DRY-RUN
  python scripts/load_cadastral.py --commit    # DDL + 적재
"""
import os
import sys
import io
import re
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import pandas as pd
import geopandas as gpd
import psycopg
from sqlalchemy import create_engine

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 🔴 기본값을 두지 않는다(2026-08-09). 예전 기본값이 `postgres:postgres` 였고,
#    2026-08-07 로컬 DB 침해가 정확히 그 조합이었다. 없으면 멈춘다(원칙 1).
# 🔴 호스트 정규화(localhost→127.0.0.1)를 여기서 다시 구현하지 않는다. 두 벌이 되면
#    한쪽만 고쳐진다 — 판단은 `app/config.py` 한 곳에서 한다(2026-08-10).
from app.config import DB_CONNECT_TIMEOUT, settings  # noqa: E402

DSN = settings.DATABASE_URL
SA_DSN = DSN.replace("postgresql://", "postgresql+psycopg://")
SRC = ROOT / "datasets" / "region_data"
DDL = ROOT / "schema_cadastral.sql"
COMMIT = "--commit" in sys.argv
STAGE = "_cad_stage"

# .shx 누락 대응 (popzap #203 주의4)
os.environ.setdefault("SHAPE_RESTORE_SHX", "YES")


def find_shp():
    hits = sorted(SRC.glob("LSMD_CONT_LDREG_*.shp"))
    return hits[0] if hits else None


def main():
    shp = find_shp()
    if not shp:
        print(f"[중단] LSMD SHP 없음: {SRC}")
        sys.exit(1)

    m = re.search(r"LSMD_CONT_LDREG_(\d+)_(\d+)", shp.stem)
    sigungu_cd = m.group(1)[:5] if m else None
    base_ym = m.group(2)[:6] if m else None
    print(f"파일: {shp.name}  → 시군구코드={sigungu_cd} 기준연월={base_ym}")

    # 멱등 가드
    with psycopg.connect(DSN, connect_timeout=DB_CONNECT_TIMEOUT) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.cadastral_lands')")
        exists = cur.fetchone()[0] is not None
        if exists:
            cur.execute(
                "SELECT count(*) FROM cadastral_lands WHERE sigungu_cd=%s", (sigungu_cd,)
            )
            if cur.fetchone()[0] != 0:
                print(f"[SKIP] cadastral_lands 에 시군구 {sigungu_cd} 이미 적재됨")
                return

    gdf = gpd.read_file(shp)
    print(f"원본: {len(gdf)}행 CRS={gdf.crs} geom={set(gdf.geom_type)}")
    gdf = gdf.to_crs(4326)

    # 컬럼 정리
    out = gpd.GeoDataFrame(
        {
            "pnu": gdf["PNU"].astype(str),
            "jibun": gdf["JIBUN"].astype(str),
            "sigungu_cd": gdf["COL_ADM_SE"].astype(str).str[:5],
            "sgg_oid": pd.to_numeric(gdf["SGG_OID"], errors="coerce").astype("Int64"),
            "bchk": gdf["BCHK"].astype(str),
            "base_ym": base_ym,
            "geometry": gdf.geometry,
        },
        geometry="geometry",
        crs=4326,
    )

    if not COMMIT:
        print(f"[DRY-RUN] 적재 대상 {len(out)}행. 샘플:")
        print("  ", out.drop(columns="geometry").iloc[0].to_dict())
        print("※ 실제 적재는 --commit")
        return

    # 1) DDL
    with psycopg.connect(DSN, connect_timeout=DB_CONNECT_TIMEOUT) as conn:
        conn.cursor().execute(DDL.read_text(encoding="utf-8"))
        conn.commit()
    print("DDL 완료.")

    # 2) staging(4326) 적재 → geopandas to_postgis
    engine = create_engine(SA_DSN, connect_args={"connect_timeout": DB_CONNECT_TIMEOUT})
    out.to_postgis(STAGE, engine, if_exists="replace", index=False)
    print(f"staging({STAGE}) 적재 {len(out)}행.")

    # 3) MakeValid + Multi 로 본 테이블 INSERT (DB 셋 연산)
    with psycopg.connect(DSN, connect_timeout=DB_CONNECT_TIMEOUT) as conn, conn.cursor() as cur:
        cur.execute(f"""
            INSERT INTO cadastral_lands (pnu,jibun,sigungu_cd,sgg_oid,bchk,base_ym,geom)
            SELECT pnu,jibun,sigungu_cd,sgg_oid,bchk,base_ym,
                   ST_Multi(ST_CollectionExtract(ST_MakeValid(geometry),3))
            FROM {STAGE}
        """)
        conn.commit()
        cur.execute(f"DROP TABLE IF EXISTS {STAGE}")
        conn.commit()
        # 검증
        cur.execute("""SELECT count(*), count(geom_5186),
                       count(*) FILTER (WHERE NOT ST_IsValid(geom)) FROM cadastral_lands""")
        n, g5, inv = cur.fetchone()
        print(f"\n=== 검증 === cadastral_lands rows={n} geom_5186={g5} invalid={inv}")
        cur.execute("SELECT pnu,jibun,sigungu_cd FROM cadastral_lands LIMIT 1")
        print("  샘플:", cur.fetchone())


if __name__ == "__main__":
    main()
