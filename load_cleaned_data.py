# -*- coding: utf-8 -*-
"""
용산구 흡연부스 데이터 적재 (17종 → 팀 schema_cleaned_data.sql)

실행 순서
  1) DBeaver:  schema_cleaned_data.sql      (팀 16종 — 이미 적용됐으면 skip)
  2) DBeaver:  schema_cleaned_data_add.sql  (17.국유부동산 + 보완 1~5)
  3) VS Code:  python load_cleaned_data.py

필요:  pip install psycopg2-binary pandas openpyxl python-dotenv
"""

import os
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# .env 사용 시:  from dotenv import load_dotenv; load_dotenv()
DSN = os.getenv(
    "DATABASE_URL",
    "host=localhost port=5432 dbname=omnisite user=postgres password=본인비번",
)
BASE = r"c:/Users/User/Projects/BigProject_Back/app/data/04.최종_데이터"


def rd(fname):
    """인코딩 자동 감지 (파일마다 utf-8 / cp949 섞여 있음)"""
    path = f"{BASE}/{fname}"
    if fname.endswith(".xlsx"):
        return pd.read_excel(path)
    for enc in ("utf-8-sig", "cp949", "utf-8"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"인코딩 실패: {fname}")


def to_num(df, cols):
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=list(cols))


conn = psycopg2.connect(DSN)
cur = conn.cursor()

# ─────────────────────────────────────────────────────────────────
# 점(Point) 데이터 13종
#   (파일, 테이블, [(CSV컬럼, DB컬럼), ...])   ※경도/위도는 자동 처리
# ─────────────────────────────────────────────────────────────────
POINT_SPEC = [
    (
        "01_버스정류소_유동인구_v2.csv",
        "bus_stop_passenger_stats",
        [("정류소명", "stop_name"), ("월평균승객수", "monthly_avg_passengers")],
    ),
    (
        "02. 용산구_가로휴지통.csv",
        "street_trash_bins",
        [("설치주소", "installation_address")],
    ),
    ("06. 용산구_공원데이터.xlsx", "parks", [("시설이름", "facility_name")]),
    (
        "07_담배꽁초_상습_무단투기_v3.csv",
        "cigarette_litter_hotspots",
        [("지번주소", "parcel_address")],
    ),
    (
        "09. 서울특별시_용산구_흡연구역.csv",
        "smoking_areas",
        [("서울특별시 용산구 설치 위치", "installation_location")],
    ),
    (
        "10. 소상공인시장진흥공단_상가.csv",
        "commercial_shops",
        [("도로명주소", "road_address"), ("상권업종대분류명", "business_category")],
    ),
    ("용산구_CCTV.csv", "cctv_locations", [("구분", "location_description")]),
    (
        "용산구_공공와이파이.csv",
        "public_wifi_locations",
        [("구분", "location_description")],
    ),
    ("용산구_공중화장실.csv", "public_toilets", [("구분", "location_description")]),
    (
        "용산구_소방용수시설_v2.csv",
        "fire_water_facilities",
        [("소재지도로명주소", "road_address")],
    ),
    ("용산구_문화행사.csv", "cultural_event_locations", [("장소명", "place_name")]),
    (
        "용산구_공영주차장.csv",
        "public_parking_lots",
        [
            ("주차장명", "parking_lot_name"),
            ("소재지도로명주소", "road_address"),
            ("소재지지번주소", "parcel_address"),
        ],
    ),
    # ★ 신규 17번
    (
        "국유부동산_위경도_v2.csv",
        "national_properties",
        [
            ("소재지(지번)", "parcel_address"),
            ("지목(공부)", "land_category"),
            ("대장면적(단위:㎡)", "registered_area"),
        ],
    ),
]

print("=== 점 데이터 적재 ===")
for fname, table, mapping in POINT_SPEC:
    df = rd(fname)
    df = to_num(df, ("경도", "위도"))

    # 면적 컬럼은 콤마 제거 후 숫자화
    if "대장면적(단위:㎡)" in df.columns:
        df["대장면적(단위:㎡)"] = pd.to_numeric(
            df["대장면적(단위:㎡)"].astype(str).str.replace(",", ""), errors="coerce"
        )

    src_cols = [c for c, _ in mapping]
    db_cols = [d for _, d in mapping] + ["longitude", "latitude", "geom"]

    rows = []
    for _, r in df.iterrows():
        vals = [None if pd.isna(r[c]) else r[c] for c in src_cols]
        rows.append(
            tuple(vals)
            + (float(r["경도"]), float(r["위도"]), float(r["경도"]), float(r["위도"]))
        )

    ph = ",".join(["%s"] * len(src_cols))
    execute_values(
        cur,
        f"INSERT INTO {table} ({','.join(db_cols)}) VALUES %s",
        rows,
        template=f"({ph},%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326))",
    )
    print(f"  {table:30s} {len(rows):6d}")

# ─────────────────────────────────────────────────────────────────
# 폴리곤(WKT) 데이터 2종
#   ST_MakeValid: candidate_lands 22건이 invalid → 그대로 넣으면 공간연산 깨짐
# ─────────────────────────────────────────────────────────────────
print("\n=== 폴리곤 데이터 적재 ===")

c = rd("05.용산구_부지면적_좌표(흡연부스 후보).csv")
rows = [(w, w) for w in c["부지_WKT"]]
execute_values(
    cur,
    "INSERT INTO candidate_lands (land_wkt, geom) VALUES %s",
    rows,
    template="(%s, ST_Multi(ST_CollectionExtract("
    "ST_MakeValid(ST_SetSRID(ST_GeomFromText(%s),4326)),3)))",
)
print(f"  candidate_lands                {len(rows):6d}  (invalid 22건 MakeValid 처리)")

g = rd("08.용산구_전체_흡연구역_폴리곤.csv")
rows = [
    (r["시설종류"], r["기준"], r["게이트_WKT"], r["게이트_WKT"])
    for _, r in g.iterrows()
]
execute_values(
    cur,
    "INSERT INTO smoking_area_polygons "
    "(facility_type, restriction_standard, gate_wkt, geom) VALUES %s",
    rows,
    template="(%s,%s,%s, ST_SetSRID(ST_GeomFromText(%s),4326))",
)
print(f"  smoking_area_polygons          {len(rows):6d}")

# ─────────────────────────────────────────────────────────────────
# 집계 데이터 2종 (좌표 없음)
# ─────────────────────────────────────────────────────────────────
print("\n=== 집계 데이터 적재 ===")

s = rd("03. 지하철역_유동인구.csv")
rows = [
    (str(r["역명"]).strip(), int(pd.to_numeric(r["총승객수"], errors="coerce") or 0))
    for _, r in s.iterrows()
    if pd.notna(r["역명"])
]
execute_values(
    cur,
    "INSERT INTO subway_station_passenger_stats "
    "(station_name, total_passengers) VALUES %s",
    rows,
)
print(f"  subway_station_passenger_stats {len(rows):6d}")

p = rd("04. 생활인구.csv")
rows = []
for _, r in p.iterrows():
    label = str(r["행 레이블"]).strip()
    if not label or label.lower() == "nan":
        continue
    rows.append(
        (
            label,
            pd.to_numeric(r["평균 성인인구수"], errors="coerce"),
            pd.to_numeric(r["평균 미성년자인구수"], errors="coerce"),
            pd.to_numeric(r["평균 총생활인구수"], errors="coerce"),
        )
    )
execute_values(
    cur,
    "INSERT INTO living_population_stats "
    "(row_label, avg_adult_population, avg_minor_population, avg_total_population) "
    "VALUES %s",
    rows,
)
print(f"  living_population_stats        {len(rows):6d}")

conn.commit()

# ─────────────────────────────────────────────────────────────────
# 적재 후처리 (스키마의 보완 3·4를 다시 실행 — 새로 넣은 행에 적용)
# ─────────────────────────────────────────────────────────────────
print("\n=== 후처리 ===")

cur.execute(
    "UPDATE candidate_lands SET area_m2 = ST_Area(geom_5186) "
    "WHERE area_m2 IS NULL AND geom_5186 IS NOT NULL"
)
cur.execute("""
UPDATE candidate_lands c SET width_m = sub.w FROM (
  SELECT id, LEAST(
    ST_Distance(ST_PointN(ST_ExteriorRing(ST_OrientedEnvelope(geom_5186)),1),
                ST_PointN(ST_ExteriorRing(ST_OrientedEnvelope(geom_5186)),2)),
    ST_Distance(ST_PointN(ST_ExteriorRing(ST_OrientedEnvelope(geom_5186)),2),
                ST_PointN(ST_ExteriorRing(ST_OrientedEnvelope(geom_5186)),3))
  ) AS w FROM candidate_lands WHERE geom_5186 IS NOT NULL
) sub WHERE c.id = sub.id AND c.width_m IS NULL
""")
cur.execute("REFRESH MATERIALIZED VIEW mv_restricted_zones")
conn.commit()
print("  면적·폭 계산 / 금지구역 캐시 갱신 완료")

# ─────────────────────────────────────────────────────────────────
# 검증
# ─────────────────────────────────────────────────────────────────
print("\n=== 검증 ===")
TABLES = [
    "bus_stop_passenger_stats",
    "street_trash_bins",
    "subway_station_passenger_stats",
    "living_population_stats",
    "candidate_lands",
    "parks",
    "cigarette_litter_hotspots",
    "smoking_area_polygons",
    "smoking_areas",
    "commercial_shops",
    "cctv_locations",
    "public_wifi_locations",
    "public_toilets",
    "fire_water_facilities",
    "cultural_event_locations",
    "public_parking_lots",
    "national_properties",
]
for t in TABLES:
    cur.execute(f"SELECT count(*) FROM {t}")
    print(f"  {t:32s} {cur.fetchone()[0]:6d}")

cur.execute("SELECT count(*) FROM candidate_lands WHERE NOT ST_IsValid(geom)")
print(f"\n  invalid 폴리곤: {cur.fetchone()[0]}건  (0이어야 정상)")

cur.execute("""
SELECT count(*) FILTER (WHERE width_m < 3)                    AS 협소,
       count(*) FILTER (WHERE width_m BETWEEN 3 AND 15)       AS 적정,
       count(*) FILTER (WHERE width_m > 15)                   AS 차도추정
FROM candidate_lands
""")
narrow, ok, road = cur.fetchone()
print(f"  필지 폭 분포 — 3m미만 {narrow} / 3~15m {ok} / 15m초과(차도) {road}")

cur.close()
conn.close()
print("\n적재 완료")
