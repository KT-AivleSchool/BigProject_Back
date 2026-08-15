import os

import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine, text

# 🔴 2026-08-10 — 여기 비밀번호가 **평문으로 박혀 있었다**(`DB_PASS = "9816"`).
#    2026-08-07 로컬 DB 침해가 정확히 그 계열(약한·공개된 자격증명)이었고,
#    `show_map.py` 는 같은 이유로 이미 제거된 상태였는데 **이 파일만 남아 있었다.**
#    기본값을 두지 않는다 — 없으면 조용히 엉뚱한 곳에 붙는 대신 여기서 멈춘다(원칙 1).
DSN = os.environ.get("DATABASE_URL")
if not DSN:
    raise SystemExit(
        "DATABASE_URL 이 없다. `.env` 를 읽어 환경변수로 넣고 다시 실행할 것 "
        "(이 스크립트는 app.config 를 쓰지 않는 독립 일회성 로더다)."
    )

# 🔴 작성자 PC 절대경로(`C:\\Users\\User\\Desktop\\BP\\data`)였다 — 다른 PC 에서는
#    아무 파일도 못 찾는데 그 사실이 첫 조회까지 안 드러난다. 환경변수로 받는다.
DATA_DIR = Path(os.environ.get("LOAD_DATA_DIR", ""))
if not DATA_DIR.is_dir():
    raise SystemExit(f"LOAD_DATA_DIR 폴더가 없다: {DATA_DIR!s}")

engine = create_engine(DSN, connect_args={"client_encoding": "utf8"})


def find_file(keyword: str) -> Path | None:
    matches = list(DATA_DIR.glob(f"*{keyword}*"))
    return matches[0] if matches else None


def read_any(path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8-sig/cp949 모두 실패", b"", 0, 1, path.name)


# ── 1) 버스정류소: 헤더 텍스트가 뒤죽박죽이라 "위치 기준"으로 강제 지정 ──
bus_path = find_file("버스정류소")
if bus_path:
    bus_df = read_any(bus_path)
    # 실제 값 기준: 1열=정류소명, 2열=경도(127대), 3열=위도(37대), 4열=평균유동인구
    bus_df.columns = ["stop_name", "longitude", "latitude", "monthly_avg_passengers"]
    bus_df.to_sql("bus_stop_passenger_stats", engine, if_exists="append", index=False)
    print(f"성공: bus_stop_passenger_stats  <-  {bus_path.name}  ({len(bus_df)}행)")
else:
    print("실패: bus_stop_passenger_stats - 파일을 못 찾음")

# ── 2) 나머지 테이블들 ─────────────────────────────────
jobs = [
    (
        "street_trash_bins",
        "가로휴지통",
        {"설치주소": "installation_address", "경도": "longitude", "위도": "latitude"},
    ),
    (
        "subway_station_passenger_stats",
        "지하철역",
        {"역명": "station_name", "총승객수": "total_passengers"},
    ),
    (
        "living_population_stats",
        "생활인구",
        {
            "행 레이블": "row_label",
            "평균 성인인구수": "avg_adult_population",
            "평균 미성년자인구수": "avg_minor_population",
            "평균 총생활인구수": "avg_total_population",
        },
    ),
    ("candidate_lands", "흡연부스", {"부지_WKT": "land_wkt"}),
    (
        "parks",
        "공원데이터",
        {"시설이름": "facility_name", "경도": "longitude", "위도": "latitude"},
    ),
    (
        "cigarette_litter_hotspots",
        "담배꽁초",
        {"지번주소": "parcel_address", "경도": "longitude", "위도": "latitude"},
    ),
    (
        "smoking_area_polygons",
        "흡연구역_폴리곤",
        {
            "시설종류": "facility_type",
            "기준": "restriction_standard",
            "게이트_WKT": "gate_wkt",
        },
    ),
    (
        "smoking_areas",
        "흡연구역.csv",
        {
            "서울특별시 용산구 설치 위치": "installation_location",
            "경도": "longitude",
            "위도": "latitude",
        },
    ),
    (
        "commercial_shops",
        "상가",
        {
            "도로명주소": "road_address",
            "상권업종대분류명": "business_category",
            "경도": "longitude",
            "위도": "latitude",
        },
    ),
    (
        "cctv_locations",
        "CCTV",
        {"구분": "location_description", "위도": "latitude", "경도": "longitude"},
    ),
    (
        "public_wifi_locations",
        "와이파이",
        {"구분": "location_description", "위도": "latitude", "경도": "longitude"},
    ),
    (
        "public_parking_lots",
        "공영주차장",
        {
            "주차장명": "parking_lot_name",
            "소재지도로명주소": "road_address",
            "소재지지번주소": "parcel_address",
            "위도": "latitude",
            "경도": "longitude",
        },
    ),
    (
        "public_toilets",
        "공중화장실",
        {"구분": "location_description", "위도": "latitude", "경도": "longitude"},
    ),
    (
        "cultural_event_locations",
        "문화행사",
        {"장소명": "place_name", "위도": "latitude", "경도": "longitude"},
    ),
    (
        "fire_water_facilities",
        "소방용수시설",
        {"소재지도로명주소": "road_address", "경도": "longitude", "위도": "latitude"},
    ),
    (
        "national_owned_properties",
        "국유부동산",
        {
            "주소": "address",
            "지목(공부)": "land_category",
            "대장면적(단위:㎡)": "registered_area_sqm",
            "경도": "longitude",
            "위도": "latitude",
        },
    ),
]

for table, keyword, colmap in jobs:
    path = find_file(keyword)
    if path is None:
        print(f"건너뜀: {table} - '{keyword}' 포함 파일이 data 폴더에 없음")
        continue
    try:
        df = read_any(path)
        df = df.rename(columns=colmap)[list(colmap.values())]
        df.to_sql(table, engine, if_exists="append", index=False)
        print(f"성공: {table}  <-  {path.name}  ({len(df)}행)")
    except Exception as e:
        print(f"실패: {table} ({path.name}) - {e}")

# ── geom 채우기 ─────────────────────────────────────
point_tables = [
    "bus_stop_passenger_stats",
    "street_trash_bins",
    "parks",
    "cigarette_litter_hotspots",
    "smoking_areas",
    "commercial_shops",
    "cctv_locations",
    "public_wifi_locations",
    "public_toilets",
    "fire_water_facilities",
    "cultural_event_locations",
    "public_parking_lots",
    "national_owned_properties",
]

with engine.begin() as conn:
    for t in point_tables:
        conn.execute(
            text(f"""
            UPDATE {t}
            SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
            WHERE geom IS NULL AND longitude IS NOT NULL AND latitude IS NOT NULL
        """)
        )
    conn.execute(
        text("""
        UPDATE candidate_lands
        SET geom = ST_SetSRID(ST_GeomFromText(land_wkt), 4326)
        WHERE geom IS NULL AND land_wkt IS NOT NULL
    """)
    )
    conn.execute(
        text("""
        UPDATE smoking_area_polygons
        SET geom = ST_SetSRID(ST_GeomFromText(gate_wkt), 4326)
        WHERE geom IS NULL AND gate_wkt IS NOT NULL
    """)
    )

print("geom 업데이트 완료")
