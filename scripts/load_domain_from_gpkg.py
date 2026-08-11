# -*- coding: utf-8 -*-
"""
STEP2 정제 gpkg → 도메인 공간 테이블 적재 (흡연/용산구).

- 소스: data_임시/step2_output/흡연_1차/*.gpkg (EPSG:4326)
  ※ 기존 CSV 로더(load_cleaned_data.py)가 읽던 app/data/04.최종_데이터/ 는 삭제됨.
    현재 유일한 도메인 원본은 이 gpkg 세트.
- geom 은 경도/위도로 재구성(ST_MakePoint), geom_5186 은 GENERATED 라 건드리지 않음.
- 멱등 가드: 대상 테이블 0행일 때만 적재 (이미 값 있으면 skip).

매핑(흡연/용산):
  clean_01 → smoking_areas      (상세위치)
  clean_02 → commercial_shops   (도로명주소, 상권업종대분류명)
  clean_12 → street_trash_bins  (설치주소)
  ※ clean_06(버스)·clean_07(지하철)은 이미 적재돼 있어 대상 아님.
    clean_03/04/05(학교/금연구역/어린이집)은 대응 테이블이 스키마에 없어 제외.

사용:
  python scripts/load_domain_from_gpkg.py           # DRY-RUN
  python scripts/load_domain_from_gpkg.py --commit   # 실제 적재
"""
import os
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

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

SRC = ROOT / "data_임시" / "step2_output" / "흡연_1차"
COMMIT = "--commit" in sys.argv

# (gpkg, 대상테이블, [(gpkg컬럼 후보들 -> db컬럼)])  ※ lng/lat/geom 자동
SPEC = [
    ("흡연_clean_01.gpkg", "smoking_areas",
     [(["상세위치", "지번주소", "도로명주소"], "installation_location")]),
    ("흡연_clean_02.gpkg", "commercial_shops",
     [(["도로명주소", "지번주소"], "road_address"),
      (["상권업종대분류명"], "business_category")]),
    ("흡연_clean_12.gpkg", "street_trash_bins",
     [(["설치주소", "상세위치"], "installation_address")]),
]


def pick(row, cands):
    for c in cands:
        if c in row and pd.notna(row[c]):
            v = str(row[c]).strip()
            if v and v.lower() != "nan":
                return v
    return None


def main():
    if not SRC.exists():
        print(f"[중단] 소스 폴더 없음: {SRC}")
        sys.exit(1)
    with psycopg.connect(DSN, connect_timeout=DB_CONNECT_TIMEOUT) as conn:
        for fname, table, mapping in SPEC:
            with conn.cursor() as cur:
                cur.execute(f'SELECT count(*) FROM "{table}"')
                n = cur.fetchone()[0]
            if n != 0:
                print(f"[SKIP] {table}: 이미 {n}행")
                continue

            gdf = gpd.read_file(SRC / fname)
            gdf["_lng"] = pd.to_numeric(gdf["경도"], errors="coerce")
            gdf["_lat"] = pd.to_numeric(gdf["위도"], errors="coerce")
            before = len(gdf)
            gdf = gdf.dropna(subset=["_lng", "_lat"])
            dropped = before - len(gdf)

            db_val_cols = [db for _, db in mapping]
            rows, nullskip = [], 0
            for _, r in gdf.iterrows():
                vals = [pick(r, cands) for cands, _ in mapping]
                if any(v is None for v in vals):
                    nullskip += 1
                    continue
                rows.append(
                    tuple(vals)
                    + (float(r["_lng"]), float(r["_lat"]), float(r["_lng"]), float(r["_lat"]))
                )

            cols_sql = ",".join(db_val_cols + ["longitude", "latitude", "geom"])
            ph = ",".join(["%s"] * len(db_val_cols))
            tmpl = f"({ph},%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326))"

            print(f"[{table}] {fname}: 원본 {before} / 좌표결측 {dropped} / "
                  f"필수결측 {nullskip} / 적재 {len(rows)}")
            if not COMMIT:
                print(f"   (DRY-RUN) 샘플: {rows[0] if rows else '없음'}")
                continue
            with conn.cursor() as cur:
                cur.executemany(
                    f'INSERT INTO "{table}" ({cols_sql}) VALUES {tmpl}', rows
                )
            conn.commit()
            print(f"   OK {len(rows)}행 커밋")

        if COMMIT:
            print("\n=== 적재 후 검증 ===")
            with conn.cursor() as cur:
                for _, table, _ in SPEC:
                    cur.execute(f'SELECT count(*), count(geom_5186) FROM "{table}"')
                    tot, g5 = cur.fetchone()
                    print(f"   {table:22s} rows={tot:6d} geom_5186={g5}")

    if not COMMIT:
        print("\n※ DRY-RUN. 실제 적재는 --commit 로 재실행.")


if __name__ == "__main__":
    main()
