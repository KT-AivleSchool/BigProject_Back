-- =============================================================================
-- 용산구 흡연부스 입지 분석용 16종 데이터 스키마
-- PostgreSQL + PostGIS
-- 주의: 아래 16개 기존 테이블을 삭제한 후 다시 생성합니다.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;

-- -----------------------------------------------------------------------------
-- 기존에 잘못 생성된 테이블 초기화
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS bus_stop_passenger_stats CASCADE;
DROP TABLE IF EXISTS street_trash_bins CASCADE;
DROP TABLE IF EXISTS subway_station_passenger_stats CASCADE;
DROP TABLE IF EXISTS living_population_stats CASCADE;
DROP TABLE IF EXISTS candidate_lands CASCADE;
DROP TABLE IF EXISTS parks CASCADE;
DROP TABLE IF EXISTS cigarette_litter_hotspots CASCADE;
DROP TABLE IF EXISTS smoking_area_polygons CASCADE;
DROP TABLE IF EXISTS smoking_areas CASCADE;
DROP TABLE IF EXISTS commercial_shops CASCADE;
DROP TABLE IF EXISTS cctv_locations CASCADE;
DROP TABLE IF EXISTS public_wifi_locations CASCADE;
DROP TABLE IF EXISTS public_toilets CASCADE;
DROP TABLE IF EXISTS fire_water_facilities CASCADE;
DROP TABLE IF EXISTS cultural_event_locations CASCADE;
DROP TABLE IF EXISTS public_parking_lots CASCADE;


-- =============================================================================
-- 01. 버스정류소 유동인구
-- 파일: 01.버스정류소_유동인구.csv
-- 실제 컬럼: 정류소명, 경도, 위도, 월평균승객수
-- =============================================================================

CREATE TABLE bus_stop_passenger_stats (
    id SERIAL PRIMARY KEY,
    stop_name VARCHAR(150) NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    monthly_avg_passengers NUMERIC,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_bus_stop_passenger_stats_geom
ON bus_stop_passenger_stats USING GIST (geom);

CREATE INDEX idx_bus_stop_passenger_stats_name
ON bus_stop_passenger_stats (stop_name);


-- =============================================================================
-- 02. 가로휴지통
-- 파일: 02. 용산구_가로휴지통.csv
-- 실제 컬럼: 설치주소, 경도, 위도
-- =============================================================================

CREATE TABLE street_trash_bins (
    id SERIAL PRIMARY KEY,
    installation_address VARCHAR(300) NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_street_trash_bins_geom
ON street_trash_bins USING GIST (geom);


-- =============================================================================
-- 03. 지하철역 유동인구
-- 파일: 03. 지하철역_유동인구.csv
-- 실제 컬럼: 역명, 총승객수
-- =============================================================================

CREATE TABLE subway_station_passenger_stats (
    id SERIAL PRIMARY KEY,
    station_name VARCHAR(150) NOT NULL,
    total_passengers BIGINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_subway_station_passenger_stats_name
ON subway_station_passenger_stats (station_name);


-- =============================================================================
-- 04. 생활인구
-- 파일: 04. 생활인구.csv
-- 실제 컬럼: 행 레이블, 평균 성인인구수, 평균 미성년자인구수,
--             평균 총생활인구수
-- =============================================================================

CREATE TABLE living_population_stats (
    id SERIAL PRIMARY KEY,
    row_label VARCHAR(150) NOT NULL,
    avg_adult_population NUMERIC,
    avg_minor_population NUMERIC,
    avg_total_population NUMERIC,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_living_population_stats_row_label
ON living_population_stats (row_label);


-- =============================================================================
-- 05. 흡연부스 후보 부지
-- 파일: 05.용산구_부지면적_좌표(흡연부스 후보).csv
-- 실제 컬럼: 부지_WKT
-- =============================================================================

CREATE TABLE candidate_lands (
    id SERIAL PRIMARY KEY,
    land_wkt TEXT NOT NULL,
    geom GEOMETRY(Polygon, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_candidate_lands_geom
ON candidate_lands USING GIST (geom);


-- =============================================================================
-- 06. 용산구 공원
-- 파일: 06. 용산구_공원데이터.xlsx
-- 실제 컬럼: 시설이름, 경도, 위도
-- =============================================================================

CREATE TABLE parks (
    id SERIAL PRIMARY KEY,
    facility_name VARCHAR(200) NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_parks_geom
ON parks USING GIST (geom);

CREATE INDEX idx_parks_name
ON parks (facility_name);


-- =============================================================================
-- 07. 담배꽁초 상습 무단투기
-- 파일: 07. 담배꽁초_상습_무단투기.csv
-- 실제 컬럼: 지번주소, 경도, 위도
-- =============================================================================

CREATE TABLE cigarette_litter_hotspots (
    id SERIAL PRIMARY KEY,
    parcel_address VARCHAR(300) NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cigarette_litter_hotspots_geom
ON cigarette_litter_hotspots USING GIST (geom);


-- =============================================================================
-- 08. 용산구 전체 흡연 제한구역 폴리곤
-- 파일: 08.용산구_전체_흡연구역_폴리곤.csv
-- 실제 컬럼: 시설종류, 기준, 게이트_WKT
-- =============================================================================

CREATE TABLE smoking_area_polygons (
    id SERIAL PRIMARY KEY,
    facility_type VARCHAR(100) NOT NULL,
    restriction_standard VARCHAR(100),
    gate_wkt TEXT NOT NULL,
    geom GEOMETRY(Polygon, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_smoking_area_polygons_geom
ON smoking_area_polygons USING GIST (geom);

CREATE INDEX idx_smoking_area_polygons_type
ON smoking_area_polygons (facility_type);


-- =============================================================================
-- 09. 용산구 기존 흡연구역
-- 파일: 09. 서울특별시_용산구_흡연구역.csv
-- 실제 컬럼: 서울특별시 용산구 설치 위치, 경도, 위도
-- =============================================================================

CREATE TABLE smoking_areas (
    id SERIAL PRIMARY KEY,
    installation_location VARCHAR(300) NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_smoking_areas_geom
ON smoking_areas USING GIST (geom);


-- =============================================================================
-- 10. 소상공인시장진흥공단 상가
-- 파일: 10. 소상공인시장진흥공단_상가.csv
-- 실제 컬럼: 도로명주소, 상권업종대분류명, 경도, 위도
-- =============================================================================

CREATE TABLE commercial_shops (
    id SERIAL PRIMARY KEY,
    road_address VARCHAR(300),
    business_category VARCHAR(150),
    longitude DOUBLE PRECISION NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_commercial_shops_geom
ON commercial_shops USING GIST (geom);

CREATE INDEX idx_commercial_shops_category
ON commercial_shops (business_category);


-- =============================================================================
-- 11. 용산구 CCTV
-- 파일: 용산구_CCTV.csv
-- 실제 컬럼: 구분, 위도, 경도
-- =============================================================================

CREATE TABLE cctv_locations (
    id SERIAL PRIMARY KEY,
    location_description VARCHAR(300) NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cctv_locations_geom
ON cctv_locations USING GIST (geom);


-- =============================================================================
-- 12. 용산구 공공와이파이
-- 파일: 용산구_공공와이파이.csv
-- 실제 컬럼: 구분, 위도, 경도
-- =============================================================================

CREATE TABLE public_wifi_locations (
    id SERIAL PRIMARY KEY,
    location_description VARCHAR(300) NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_public_wifi_locations_geom
ON public_wifi_locations USING GIST (geom);


-- =============================================================================
-- 13. 용산구 공중화장실
-- 파일: 용산구_공중화장실.csv
-- 실제 컬럼: 구분, 위도, 경도
-- =============================================================================

CREATE TABLE public_toilets (
    id SERIAL PRIMARY KEY,
    location_description VARCHAR(300) NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_public_toilets_geom
ON public_toilets USING GIST (geom);


-- =============================================================================
-- 14. 용산구 소방용수시설
-- 파일: 용산구_소방용수시설.csv
-- 실제 컬럼: 소재지도로명주소, 위도, 경도
-- =============================================================================

CREATE TABLE fire_water_facilities (
    id SERIAL PRIMARY KEY,
    road_address VARCHAR(300) NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_fire_water_facilities_geom
ON fire_water_facilities USING GIST (geom);


-- =============================================================================
-- 15. 용산구 문화행사
-- 파일: 용산구_문화행사.csv
-- 실제 컬럼: 장소명, 위도, 경도
-- =============================================================================

CREATE TABLE cultural_event_locations (
    id SERIAL PRIMARY KEY,
    place_name VARCHAR(300) NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cultural_event_locations_geom
ON cultural_event_locations USING GIST (geom);

CREATE INDEX idx_cultural_event_locations_name
ON cultural_event_locations (place_name);


-- =============================================================================
-- 16. 용산구 공영주차장
-- 파일: 용산구_공영주차장.csv
-- 실제 컬럼: 주차장명, 소재지도로명주소, 소재지지번주소, 위도, 경도
-- =============================================================================

CREATE TABLE public_parking_lots (
    id SERIAL PRIMARY KEY,
    parking_lot_name VARCHAR(200) NOT NULL,
    road_address VARCHAR(300),
    parcel_address VARCHAR(300),
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_public_parking_lots_geom
ON public_parking_lots USING GIST (geom);

CREATE INDEX idx_public_parking_lots_name
ON public_parking_lots (parking_lot_name);


-- =============================================================================
-- CSV 적재 후 Point geom 생성
-- =============================================================================

UPDATE bus_stop_passenger_stats
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE geom IS NULL
  AND longitude IS NOT NULL
  AND latitude IS NOT NULL;

UPDATE street_trash_bins
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE geom IS NULL
  AND longitude IS NOT NULL
  AND latitude IS NOT NULL;

UPDATE parks
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE geom IS NULL
  AND longitude IS NOT NULL
  AND latitude IS NOT NULL;

UPDATE cigarette_litter_hotspots
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE geom IS NULL
  AND longitude IS NOT NULL
  AND latitude IS NOT NULL;

UPDATE smoking_areas
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE geom IS NULL
  AND longitude IS NOT NULL
  AND latitude IS NOT NULL;

UPDATE commercial_shops
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE geom IS NULL
  AND longitude IS NOT NULL
  AND latitude IS NOT NULL;

UPDATE cctv_locations
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE geom IS NULL
  AND longitude IS NOT NULL
  AND latitude IS NOT NULL;

UPDATE public_wifi_locations
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE geom IS NULL
  AND longitude IS NOT NULL
  AND latitude IS NOT NULL;

UPDATE public_toilets
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE geom IS NULL
  AND longitude IS NOT NULL
  AND latitude IS NOT NULL;

UPDATE fire_water_facilities
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE geom IS NULL
  AND longitude IS NOT NULL
  AND latitude IS NOT NULL;

UPDATE cultural_event_locations
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE geom IS NULL
  AND longitude IS NOT NULL
  AND latitude IS NOT NULL;

UPDATE public_parking_lots
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE geom IS NULL
  AND longitude IS NOT NULL
  AND latitude IS NOT NULL;


-- =============================================================================
-- WKT geom 생성
-- =============================================================================

UPDATE candidate_lands
SET geom = ST_SetSRID(ST_GeomFromText(land_wkt), 4326)
WHERE geom IS NULL
  AND land_wkt IS NOT NULL;

UPDATE smoking_area_polygons
SET geom = ST_SetSRID(ST_GeomFromText(gate_wkt), 4326)
WHERE geom IS NULL
  AND gate_wkt IS NOT NULL;