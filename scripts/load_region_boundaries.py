# -*- coding: utf-8 -*-
"""
1계층 전국 공용 경계 3종 + 행정동 크로스워크 적재 (popzap #179 요청).

- DDL: 프로젝트 루트 schema_region_boundaries.sql 실행 (CREATE IF NOT EXISTS)
- 원본 SHP(EPSG:5186) → geopandas to_crs(4326) → geom(4326) 저장, geom_5186 자동생성
- invalid 폴리곤은 ST_MakeValid + CollectionExtract(3) + ST_Multi 로 정리
- 멱등 가드: 대상 테이블 0행일 때만 적재 (재실행 안전)

원본 위치: datasets/region_data/  (BND_*_PG.shp, 행정동_크로스워크.csv)
  ※ region_data 는 .gitignore 대상 — 원본은 각자 Teams/감리팀에서 받아 배치할 것.

사용:
  python scripts/load_region_boundaries.py           # DRY-RUN (건수만 확인)
  python scripts/load_region_boundaries.py --commit   # DDL 실행 + 실제 적재
"""
import os
import sys
import io
from pathlib import Path

# line_buffering: 리다이렉트(백그라운드 실행·로그 수집) 시에도 진행 상황이 즉시 보이게 한다.
# 재래핑이 python -u 를 무력화하므로 여기서 다시 켜 준다.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

import pandas as pd
import geopandas as gpd
import psycopg

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[1]
# 🔴 기본값을 두지 않는다(2026-08-09). 예전 기본값이 `postgres:postgres` 였고,
#    2026-08-07 로컬 DB 침해가 정확히 그 조합이었다. 없으면 멈춘다(원칙 1).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 🔴 호스트 정규화(localhost→127.0.0.1)와 기본값 금지 판단은 `app/config.py` 한 곳에서
#    한다. 여기서 다시 구현하면 두 벌이 되고 한쪽만 고쳐진다(2026-08-10).
from app.config import DB_CONNECT_TIMEOUT, settings  # noqa: E402

DSN = settings.DATABASE_URL

SRC = ROOT / "datasets" / "region_data"
DDL = ROOT / "schema_region_boundaries.sql"
COMMIT = "--commit" in sys.argv

# (SHP, 테이블, [(shp컬럼 -> db컬럼)])  ※ base_date/geom 은 자동 처리
BND_SPEC = [
    ("BND_SIDO_PG.shp", "sido_boundaries",
     [("SIDO_CD", "sido_cd"), ("SIDO_NM", "sido_nm")]),
    ("BND_SIGUNGU_PG.shp", "sigungu_boundaries",
     [("SIGUNGU_CD", "sigungu_cd"), ("SIGUNGU_NM", "sigungu_nm")]),
    ("BND_ADM_DONG_PG.shp", "adm_dong_boundaries",
     [("ADM_CD", "adm_cd"), ("ADM_NM", "adm_nm")]),
]
GEOM_TMPL = "ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromText(%s,4326)),3))"


def load_boundary(conn, shp, table, mapping):
    with conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{table}"')
        if cur.fetchone()[0] != 0:
            print(f"[SKIP] {table}: 이미 데이터 있음")
            return
    gdf = gpd.read_file(SRC / shp)
    src_crs = gdf.crs
    gdf = gdf.to_crs(4326)
    src_cols = [s for s, _ in mapping]
    db_cols = [d for _, d in mapping] + ["base_date", "geom"]
    rows = []
    for _, r in gdf.iterrows():
        vals = tuple(str(r[c]) for c in src_cols)
        bdate = (
            str(r["BASE_DATE"])
            if "BASE_DATE" in gdf.columns and pd.notna(r["BASE_DATE"])
            else None
        )
        rows.append(vals + (bdate, r.geometry.wkt))
    ph = ",".join(["%s"] * len(src_cols))
    tmpl = f"({ph},%s,{GEOM_TMPL})"
    print(f"[{table}] {shp}: src_crs={src_crs} rows={len(rows)}")
    if not COMMIT:
        print(f"   (DRY-RUN) 샘플 속성: {rows[0][:-1]}")
        return
    with conn.cursor() as cur:
        cur.executemany(
            f'INSERT INTO "{table}" ({",".join(db_cols)}) VALUES {tmpl}', rows
        )
    conn.commit()
    print(f"   OK {len(rows)}행 커밋")


def load_crosswalk(conn):
    table = "admin_crosswalk"
    with conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{table}"')
        if cur.fetchone()[0] != 0:
            print(f"[SKIP] {table}: 이미 데이터 있음")
            return
    df = None
    for enc in ("utf-8-sig", "cp949", "utf-8"):
        try:
            df = pd.read_csv(SRC / "행정동_크로스워크.csv", encoding=enc, dtype=str)
            break
        except Exception:
            df = None
    if df is None:
        print("[ERR] 크로스워크 읽기 실패")
        return
    cols = ["행정구역코드", "행정동코드", "행정동코드8", "행정동명", "시도명", "시군구명", "기준일자"]
    rows = [
        tuple(None if pd.isna(r[c]) else str(r[c]).strip() for c in cols)
        for _, r in df.iterrows()
    ]
    print(f"[{table}] 크로스워크: rows={len(rows)}")
    if not COMMIT:
        print(f"   (DRY-RUN) 샘플: {rows[0]}")
        return
    dbcols = "region_code,adm_code10,adm_code8,adm_name,sido_name,sigungu_name,base_date"
    with conn.cursor() as cur:
        cur.executemany(
            f'INSERT INTO {table} ({dbcols}) VALUES ({",".join(["%s"] * 7)})', rows
        )
    conn.commit()
    print(f"   OK {len(rows)}행 커밋")


def main():
    if not SRC.exists():
        print(f"[중단] 원본 폴더 없음: {SRC}\n  region_data 를 이 경로에 배치 후 재실행하세요.")
        sys.exit(1)
    # connect_timeout: 미지정 시 DB 부재를 260초(실측) 뒤에야 알려준다 — "느림"으로 오인된다.
    with psycopg.connect(DSN, connect_timeout=DB_CONNECT_TIMEOUT) as conn:
        if COMMIT:
            conn.cursor().execute(DDL.read_text(encoding="utf-8"))
            conn.commit()
            print("=== DDL 실행 완료 (CREATE IF NOT EXISTS) ===\n")
        else:
            print("=== DRY-RUN: DDL 미실행 (테이블 없으면 카운트 에러는 정상) ===\n")

        for shp, table, mapping in BND_SPEC:
            try:
                load_boundary(conn, shp, table, mapping)
            except Exception as e:
                conn.rollback()
                print(f"   [ERR] {table}: {e}")
        try:
            load_crosswalk(conn)
        except Exception as e:
            conn.rollback()
            print(f"   [ERR] crosswalk: {e}")

        if COMMIT:
            print("\n=== 적재 후 검증 ===")
            with conn.cursor() as cur:
                for t in ["sido_boundaries", "sigungu_boundaries", "adm_dong_boundaries"]:
                    cur.execute(
                        f'SELECT count(*), count(geom_5186), '
                        f'count(*) FILTER (WHERE NOT ST_IsValid(geom)) FROM "{t}"'
                    )
                    n, g5, inv = cur.fetchone()
                    print(f"   {t:22s} rows={n:5d} geom_5186={g5} invalid={inv}")
                cur.execute(
                    "SELECT count(*), "
                    "(SELECT count(*) FROM admin_crosswalk c "
                    " JOIN adm_dong_boundaries b ON c.region_code=b.adm_cd) "
                    "FROM admin_crosswalk"
                )
                n, joined = cur.fetchone()
                print(f"   admin_crosswalk        rows={n:5d} 경계조인매칭={joined}")

    if not COMMIT:
        print("\n※ DRY-RUN. 실제 적재는 --commit 로 재실행.")


if __name__ == "__main__":
    main()
