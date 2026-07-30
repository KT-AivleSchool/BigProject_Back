-- =============================================================================
-- schema_cleaned_data.sql 추가분
--   17. 국유부동산 (신규 데이터)
--   + 기존 16종 스키마 보완 (invalid 도형 / 5186 좌표계 / 필지 폭)
--
-- 기존 팀 스키마 명명규칙 준수:
--   테이블=복수형 snake_case, 컬럼=영문 snake_case,
--   longitude/latitude 별도 + geom, created_at 포함
-- =============================================================================


-- =============================================================================
-- 17. 국유부동산  ★신규
-- 파일: 국유부동산_위경도_v2.csv
-- 실제 컬럼: 소재지(지번), 지목(공부), 대장면적(단위:㎡), 경도, 위도
-- 데이터: 2,486건 (대2361 / 잡종지79 / 공원39 / 주차장5 / 체육용지2)
-- 용도: 후보 부지의 소유 근거 — 국유지면 사유지 취득 리스크 없음
-- =============================================================================

DROP TABLE IF EXISTS national_properties CASCADE;

CREATE TABLE national_properties (
    id SERIAL PRIMARY KEY,
    parcel_address VARCHAR(300) NOT NULL,      -- 소재지(지번)
    land_category VARCHAR(50),                 -- 지목(공부): 대/잡종지/공원/주차장/체육용지
    registered_area NUMERIC,                   -- 대장면적(㎡)
    longitude DOUBLE PRECISION NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_national_properties_geom
ON national_properties USING GIST (geom);

CREATE INDEX idx_national_properties_category
ON national_properties (land_category);

-- CSV 적재 후 geom 생성 (기존 16종과 동일 패턴)
UPDATE national_properties
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE geom IS NULL
  AND longitude IS NOT NULL
  AND latitude IS NOT NULL;


-- =============================================================================
-- [보완 1] invalid 폴리곤 복구  ★필수
-- candidate_lands 6,524건 중 22건이 self-intersection 등으로 invalid.
-- 이 상태면 ST_Intersects / ST_Contains 결과가 틀리거나 에러가 남.
-- =============================================================================

UPDATE candidate_lands
SET geom = ST_MakeValid(geom)
WHERE geom IS NOT NULL
  AND NOT ST_IsValid(geom);

-- MakeValid가 GeometryCollection을 반환하는 경우 Polygon만 추출
UPDATE candidate_lands
SET geom = ST_CollectionExtract(geom, 3)
WHERE GeometryType(geom) = 'GEOMETRYCOLLECTION';

-- [보완 1-b] geom 타입을 MultiPolygon으로 확장  ★필수
-- invalid 복구(ST_MakeValid) 시 self-intersection이 여러 폴리곤으로 분리되어
-- 단일 Polygon 컬럼엔 삽입 실패함(실측 6,524건 중 10건이 MULTIPOLYGON).
-- MultiPolygon으로 확장하면 전 지오메트리를 손실 없이 보존하고
-- ST_Area/ST_OrientedEnvelope 등 공간연산도 그대로 정확함.
ALTER TABLE candidate_lands
  ALTER COLUMN geom TYPE geometry(MultiPolygon, 4326) USING ST_Multi(geom);

-- 검증: 0이어야 정상
-- SELECT count(*) FROM candidate_lands WHERE NOT ST_IsValid(geom);


-- =============================================================================
-- [보완 2] EPSG:5186 생성컬럼 + 인덱스  ★성능 필수
--
-- 문제: geom이 4326(경위도)이라 ST_DWithin(geom, x, 150)의 150이
--       '미터'가 아니라 '도(degree)'로 해석됨 → 결과가 전부 매칭되거나 틀림.
--       매 쿼리 ST_Transform 하면 GIST 인덱스를 못 타서 3분 30초 소요(실측).
--
-- 해결: 5186(중부원점, 미터) 생성컬럼을 미리 만들고 인덱스를 검
--       → 거리·교차 연산은 전부 geom_5186 사용. 수초로 단축.
-- =============================================================================

-- 점 데이터
ALTER TABLE bus_stop_passenger_stats  ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
ALTER TABLE street_trash_bins         ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
ALTER TABLE parks                     ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
ALTER TABLE cigarette_litter_hotspots ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
ALTER TABLE smoking_areas             ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
ALTER TABLE commercial_shops          ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
ALTER TABLE cctv_locations            ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
ALTER TABLE public_wifi_locations     ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
ALTER TABLE public_toilets            ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
ALTER TABLE fire_water_facilities     ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
ALTER TABLE cultural_event_locations  ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
ALTER TABLE public_parking_lots       ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
ALTER TABLE national_properties       ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;

-- 폴리곤 데이터
ALTER TABLE candidate_lands       ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(MultiPolygon, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
ALTER TABLE smoking_area_polygons ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Polygon, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;

-- 5186 인덱스 (실제 공간 연산이 타는 인덱스)
CREATE INDEX IF NOT EXISTS idx_bus_stop_5186    ON bus_stop_passenger_stats  USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_trash_5186       ON street_trash_bins         USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_parks_5186       ON parks                     USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_litter_5186      ON cigarette_litter_hotspots USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_smoking_5186     ON smoking_areas             USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_shops_5186       ON commercial_shops          USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_cctv_5186        ON cctv_locations            USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_wifi_5186        ON public_wifi_locations     USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_toilet_5186      ON public_toilets            USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_fire_5186        ON fire_water_facilities     USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_culture_5186     ON cultural_event_locations  USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_parking_5186     ON public_parking_lots       USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_national_5186    ON national_properties       USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_candidate_5186   ON candidate_lands           USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_gate_5186        ON smoking_area_polygons     USING GIST (geom_5186);


-- =============================================================================
-- [보완 3] 후보 부지 면적 · 폭  ★차도 배제 핵심
--
-- 문제: 후보 부지에 면적이 없어 필터를 못 검.
--       면적만으로는 '긴 골목(폭5m×100m=500㎡)'과
--       '차도(폭25m×20m=500㎡)'를 구분할 수 없음.
--
-- 해결: 최소외접사각형(ST_OrientedEnvelope)의 짧은 변 = 실제 폭.
--       실측 분포(샘플500): 중앙값 6.2m / 3~15m가 68% / 15m초과 17%(차도)
--       → width_m BETWEEN 3 AND 15 로 차도·초협소 동시 배제
-- =============================================================================

ALTER TABLE candidate_lands ADD COLUMN IF NOT EXISTS area_m2  DOUBLE PRECISION;
ALTER TABLE candidate_lands ADD COLUMN IF NOT EXISTS width_m  DOUBLE PRECISION;

-- 면적 (5186이라 결과가 바로 ㎡)
UPDATE candidate_lands
SET area_m2 = ST_Area(geom_5186)
WHERE geom_5186 IS NOT NULL;

-- 폭 = 최소외접사각형의 짧은 변
UPDATE candidate_lands c
SET width_m = sub.w
FROM (
    SELECT id,
           LEAST(
             ST_Distance(ST_PointN(ST_ExteriorRing(ST_OrientedEnvelope(geom_5186)), 1),
                         ST_PointN(ST_ExteriorRing(ST_OrientedEnvelope(geom_5186)), 2)),
             ST_Distance(ST_PointN(ST_ExteriorRing(ST_OrientedEnvelope(geom_5186)), 2),
                         ST_PointN(ST_ExteriorRing(ST_OrientedEnvelope(geom_5186)), 3))
           ) AS w
    FROM candidate_lands
    WHERE geom_5186 IS NOT NULL
) sub
WHERE c.id = sub.id;

CREATE INDEX IF NOT EXISTS idx_candidate_lands_area  ON candidate_lands (area_m2);
CREATE INDEX IF NOT EXISTS idx_candidate_lands_width ON candidate_lands (width_m);

COMMENT ON COLUMN candidate_lands.width_m IS
  '최소외접사각형 짧은 변(m). 3m미만=부스설치 불가, 15m초과=차도 추정';


-- =============================================================================
-- [보완 4] 금지구역 캐시 (휘발성 레이어)
-- 조례 개정 시 REFRESH MATERIALIZED VIEW mv_restricted_zones; 한 줄로 갱신
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_restricted_zones CASCADE;

CREATE MATERIALIZED VIEW mv_restricted_zones AS
SELECT id, facility_type, restriction_standard, geom_5186 AS geom
FROM smoking_area_polygons
WHERE geom_5186 IS NOT NULL;

CREATE INDEX idx_mv_restricted_zones_geom
ON mv_restricted_zones USING GIST (geom);


-- =============================================================================
-- [보완 5] 후보지 산출물 테이블 (AHP·멀티에이전트가 읽어감)
-- 지표를 한 번 계산해 캐싱 → 가중치만 바꿔 score를 UPDATE
-- =============================================================================

DROP TABLE IF EXISTS booth_candidates CASCADE;

CREATE TABLE booth_candidates (
    id SERIAL PRIMARY KEY,
    land_id INTEGER REFERENCES candidate_lands(id),
    area_m2 DOUBLE PRECISION,
    width_m DOUBLE PRECISION,
    is_national BOOLEAN,                    -- 국유지 매칭 여부(소유 근거)
    shops_150m INTEGER,                     -- 수요: 반경150m 상가 수
    dist_transit DOUBLE PRECISION,          -- 접근성: 최근접 정류장
    dist_litter DOUBLE PRECISION,           -- 실수요: 최근접 담배꽁초 투기지점
    dist_existing DOUBLE PRECISION,         -- 공백지: 최근접 기존 흡연구역
    score DOUBLE PRECISION,                 -- AHP 가중합 (다음 단계)
    rank INTEGER,
    geom GEOMETRY(Point, 4326),
    geom_5186 GEOMETRY(Point, 5186)
      GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_booth_candidates_geom  ON booth_candidates USING GIST (geom_5186);
CREATE INDEX idx_booth_candidates_score ON booth_candidates (score DESC);
