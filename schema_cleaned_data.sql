-- =============================================================================
-- SDSS Cleaned Data PostGIS Schema
-- 정제 CSV 데이터 적재용 테이블 생성 SQL
-- =============================================================================
-- 기준 데이터:
-- 00.버스정류소_위치.csv
-- 01.버스정류소_유동인구.csv
-- 02. 지하철_출입구_위치.csv
-- 03. 지하철역_유동인구.csv
-- 04. 생활인구.csv
-- 05.용산구_부지면적_좌표(흡연부스 후보).csv
-- 06. 금연구역_통합본(광장제외).csv
-- 07. 담배꽁초_상습_무단투기.csv
-- 08.용산구_전체_흡연구역_폴리곤.csv
-- 09. 서울특별시_용산구_흡연구역.csv
-- 10. 소상공인시장진흥공단_상가_YONGSAN.csv
-- 11. G1_서울특별시 용산구_가로휴지통_20240630_geocoded.csv

CREATE EXTENSION IF NOT EXISTS postgis;

-- 00. 버스정류소 위치
CREATE TABLE IF NOT EXISTS bus_stop_locations (
    id SERIAL PRIMARY KEY,
    standard_bus_stop_id VARCHAR(50) NOT NULL,
    stop_name VARCHAR(150) NOT NULL,
    stop_type VARCHAR(50),
    longitude DOUBLE PRECISION NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    install_status VARCHAR(20),
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bus_stop_locations_geom
ON bus_stop_locations USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_bus_stop_locations_standard_id
ON bus_stop_locations (standard_bus_stop_id);

-- 01. 버스정류소 유동인구
CREATE TABLE IF NOT EXISTS bus_stop_passenger_stats (
    id SERIAL PRIMARY KEY,
    standard_bus_stop_id VARCHAR(50) NOT NULL,
    stop_name VARCHAR(150) NOT NULL,
    longitude DOUBLE PRECISION,
    latitude DOUBLE PRECISION,
    monthly_avg_passengers NUMERIC,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bus_stop_passenger_stats_geom
ON bus_stop_passenger_stats USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_bus_stop_passenger_stats_standard_id
ON bus_stop_passenger_stats (standard_bus_stop_id);

-- 02. 지하철 출입구 위치
CREATE TABLE IF NOT EXISTS subway_exit_locations (
    id SERIAL PRIMARY KEY,
    station_name VARCHAR(100) NOT NULL,
    exit_no VARCHAR(50) NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_subway_exit_locations_geom
ON subway_exit_locations USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_subway_exit_locations_station
ON subway_exit_locations (station_name);

-- 03. 지하철역 유동인구
CREATE TABLE IF NOT EXISTS subway_station_passenger_stats (
    id SERIAL PRIMARY KEY,
    station_name VARCHAR(100) NOT NULL,
    total_passengers INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_subway_station_passenger_stats_station
ON subway_station_passenger_stats (station_name);

-- 04. 생활인구
CREATE TABLE IF NOT EXISTS living_population_stats (
    id SERIAL PRIMARY KEY,
    base_date DATE NOT NULL,
    time_slot INTEGER NOT NULL,
    dong_code VARCHAR(20) NOT NULL,
    total_living_population NUMERIC,
    minor_population NUMERIC,
    adult_population NUMERIC,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_living_population_stats_dong_code
ON living_population_stats (dong_code);

CREATE INDEX IF NOT EXISTS idx_living_population_stats_base_date_time
ON living_population_stats (base_date, time_slot);

-- 05. 용산구 부지면적 좌표(흡연부스 후보)
CREATE TABLE IF NOT EXISTS candidate_lands (
    id SERIAL PRIMARY KEY,
    pnu VARCHAR(30) NOT NULL,
    jibun VARCHAR(100),
    land_category VARCHAR(50),
    area_m2 NUMERIC,
    booth_available BOOLEAN,
    longitude DOUBLE PRECISION,
    latitude DOUBLE PRECISION,
    land_wkt TEXT,
    geom GEOMETRY(Geometry, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_candidate_lands_geom
ON candidate_lands USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_candidate_lands_pnu
ON candidate_lands (pnu);

-- 06. 금연구역 통합본(광장제외)
CREATE TABLE IF NOT EXISTS restricted_facilities (
    id SERIAL PRIMARY KEY,
    facility_name VARCHAR(200),
    facility_type VARCHAR(100) NOT NULL,
    address VARCHAR(300),
    longitude DOUBLE PRECISION,
    latitude DOUBLE PRECISION,
    designation_range VARCHAR(50),
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_restricted_facilities_geom
ON restricted_facilities USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_restricted_facilities_type
ON restricted_facilities (facility_type);

-- 07. 담배꽁초 상습 무단투기
CREATE TABLE IF NOT EXISTS cigarette_litter_hotspots (
    id SERIAL PRIMARY KEY,
    road_address VARCHAR(300),
    parcel_address VARCHAR(300),
    longitude DOUBLE PRECISION,
    latitude DOUBLE PRECISION,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cigarette_litter_hotspots_geom
ON cigarette_litter_hotspots USING GIST (geom);

-- 08. 용산구 전체 흡연구역 폴리곤
CREATE TABLE IF NOT EXISTS smoking_area_polygons (
    id SERIAL PRIMARY KEY,
    facility_type VARCHAR(100) NOT NULL,
    standard VARCHAR(100),
    gate_wkt TEXT,
    geom GEOMETRY(Geometry, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_smoking_area_polygons_geom
ON smoking_area_polygons USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_smoking_area_polygons_type
ON smoking_area_polygons (facility_type);

-- 09. 서울특별시 용산구 흡연구역
CREATE TABLE IF NOT EXISTS smoking_areas (
    id SERIAL PRIMARY KEY,
    installation_location VARCHAR(300) NOT NULL,
    longitude DOUBLE PRECISION,
    latitude DOUBLE PRECISION,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_smoking_areas_geom
ON smoking_areas USING GIST (geom);

-- 10. 소상공인시장진흥공단 상가
CREATE TABLE IF NOT EXISTS commercial_shops (
    id SERIAL PRIMARY KEY,
    shop_name VARCHAR(200) NOT NULL,
    main_category_name VARCHAR(100),
    middle_category_name VARCHAR(100),
    sub_category_code VARCHAR(50),
    sub_category_name VARCHAR(100),
    industry_code VARCHAR(50),
    industry_name VARCHAR(150),
    road_address VARCHAR(300),
    longitude DOUBLE PRECISION,
    latitude DOUBLE PRECISION,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_commercial_shops_geom
ON commercial_shops USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_commercial_shops_category
ON commercial_shops (main_category_name, middle_category_name);

-- 11. 가로휴지통
CREATE TABLE IF NOT EXISTS street_trash_bins (
    id SERIAL PRIMARY KEY,
    installation_address VARCHAR(300) NOT NULL,
    longitude DOUBLE PRECISION,
    latitude DOUBLE PRECISION,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_street_trash_bins_geom
ON street_trash_bins USING GIST (geom);

-- =============================================================================
-- 좌표/WKT 기반 geom 컬럼 생성용 예시 SQL
-- CSV 적재 후 아래 UPDATE 문을 실행하면 PostGIS 공간 컬럼을 채울 수 있습니다.
-- =============================================================================

-- UPDATE bus_stop_locations
-- SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
-- WHERE geom IS NULL AND longitude IS NOT NULL AND latitude IS NOT NULL;

-- UPDATE bus_stop_passenger_stats
-- SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
-- WHERE geom IS NULL AND longitude IS NOT NULL AND latitude IS NOT NULL;

-- UPDATE subway_exit_locations
-- SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
-- WHERE geom IS NULL AND longitude IS NOT NULL AND latitude IS NOT NULL;

-- UPDATE candidate_lands
-- SET geom = ST_SetSRID(ST_GeomFromText(land_wkt), 4326)
-- WHERE geom IS NULL AND land_wkt IS NOT NULL;

-- UPDATE restricted_facilities
-- SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
-- WHERE geom IS NULL AND longitude IS NOT NULL AND latitude IS NOT NULL;

-- UPDATE cigarette_litter_hotspots
-- SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
-- WHERE geom IS NULL AND longitude IS NOT NULL AND latitude IS NOT NULL;

-- UPDATE smoking_area_polygons
-- SET geom = ST_SetSRID(ST_GeomFromText(gate_wkt), 4326)
-- WHERE geom IS NULL AND gate_wkt IS NOT NULL;

-- UPDATE smoking_areas
-- SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
-- WHERE geom IS NULL AND longitude IS NOT NULL AND latitude IS NOT NULL;

-- UPDATE commercial_shops
-- SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
-- WHERE geom IS NULL AND longitude IS NOT NULL AND latitude IS NOT NULL;

-- UPDATE street_trash_bins
-- SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
-- WHERE geom IS NULL AND longitude IS NOT NULL AND latitude IS NOT NULL;
