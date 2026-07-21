import folium
import psycopg2
from shapely import wkt as shapely_wkt

DB_USER = "postgres"
DB_PASS = "9816"
DB_HOST = "127.0.0.1"   # localhost 말고 이걸로 (IPv4 고정)
DB_PORT = "5432"
DB_NAME = "postgres"

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
cur = conn.cursor()

# 용산구 중심 좌표로 지도 시작
m = folium.Map(location=[37.5326, 126.9906], zoom_start=14)

# (테이블명, 이름컬럼, 색상) — 필요한 것만 남기거나 추가하세요
layers = [
    ("cctv_locations", "location_description", "red"),
    ("public_wifi_locations", "location_description", "blue"),
    ("parks", "facility_name", "green"),
    ("smoking_areas", "installation_location", "orange"),
    ("cigarette_litter_hotspots", "parcel_address", "black"),
]

for table, name_col, color in layers:
    fg = folium.FeatureGroup(name=table)
    cur.execute(f"SELECT {name_col}, latitude, longitude FROM {table} WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
    rows = cur.fetchall()
    for name, lat, lon in rows:
        folium.CircleMarker(
            location=[lat, lon],
            radius=4,
            popup=str(name),
            color=color,
            fill=True,
            fill_opacity=0.7,
        ).add_to(fg)
    fg.add_to(m)
    print(f"{table}: {len(rows)}개 점 추가")

# ── 폴리곤 레이어 추가 ─────────────────────────────
polygon_layers = [
    ("candidate_lands", "land_wkt", "purple", "흡연부스 후보부지"),
    ("smoking_area_polygons", "gate_wkt", "gray", "흡연 제외구역(게이트)"),
]

for table, wkt_col, color, layer_name in polygon_layers:
    fg = folium.FeatureGroup(name=layer_name)
    cur.execute(f"SELECT {wkt_col} FROM {table} WHERE {wkt_col} IS NOT NULL LIMIT 200")
    rows = cur.fetchall()
    success, failed = 0, 0
    for (wkt_text,) in rows:
        try:
            geom = shapely_wkt.loads(wkt_text)
            # 외곽선(exterior) 좌표만 사용 (구멍이 있어도 바깥 테두리는 항상 그려짐)
            exterior_coords = list(geom.exterior.coords)
            latlon_points = [(lat, lon) for lon, lat in exterior_coords]
            folium.Polygon(
                locations=latlon_points,
                color=color,
                weight=1,
                fill=True,
                fill_opacity=0.3,
            ).add_to(fg)
            success += 1
        except Exception as e:
            failed += 1
    fg.add_to(m)
    print(f"{table}: {success}개 폴리곤 추가 (실패 {failed}개)")

folium.LayerControl().add_to(m)  # 오른쪽 위에서 레이어 켜고 끄기 가능

m.save("map.html")
print("완료! map.html 파일이 생성됐습니다.")

cur.close()
conn.close()