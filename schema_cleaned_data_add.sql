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
-- 🔴 멱등 보장: geom_5186 이 이미 있으면 geom 타입 변경 시 FeatureNotSupported 에러가 나므로 임시로 드랍한다.
ALTER TABLE candidate_lands DROP COLUMN IF EXISTS geom_5186;
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

-- 🔴 2026-08-11 — **14줄을 들어냈다.** 여기 있던 `ALTER TABLE` 대상 중 흡연 도메인
--    데이터셋 14개(bus_stop_passenger_stats · street_trash_bins · parks ·
--    cigarette_litter_hotspots · smoking_areas · commercial_shops · cctv_locations ·
--    public_wifi_locations · public_toilets · fire_water_facilities ·
--    cultural_event_locations · public_parking_lots · smoking_area_polygons)는
--    DB 에서 제거됐다(경위는 `schema_cleaned_data.sql` 머리말). 프리셋 원본은 디스크에 둔다.
--    🔴 `ADD COLUMN IF NOT EXISTS` 는 **컬럼**에만 걸리는 조건이다 — 테이블이 없으면
--    그냥 에러다. 그대로 뒀으면 이 파일이 **첫 줄에서 죽는다.**
--    지운 것은 「없어도 되는 줄」이 아니라 **없는 테이블을 가리키던 줄**이다.

-- 점 데이터
ALTER TABLE national_properties       ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;

-- 폴리곤 데이터
ALTER TABLE candidate_lands       ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(MultiPolygon, 5186)
  GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;

-- 5186 인덱스 (실제 공간 연산이 타는 인덱스)
CREATE INDEX IF NOT EXISTS idx_national_5186    ON national_properties       USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_candidate_5186   ON candidate_lands           USING GIST (geom_5186);


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
-- [보완 4] 금지구역 캐시 (휘발성 레이어) — 🔴 2026-08-11 제거
--
-- 여기 있던 `mv_restricted_zones` 매터리얼라이즈드 뷰(소스 `smoking_area_polygons`)는
-- 지웠다. **0행 · 소스도 0행 · 코드 참조 0회**였다.
--
-- 발견 경위를 남긴다: 흡연 도메인 테이블을 `CASCADE` 없이 지우다
-- `DependentObjectsStillExist: materialized view mv_restricted_zones depends on
-- table smoking_area_polygons` 로 **멈춰서** 알았다. 아무도 이 뷰의 존재를 몰랐다 —
-- `CASCADE` 를 붙였으면 조용히 같이 쓸려 나가고 **지웠다는 사실조차 안 남았을 것**이다.
-- 파괴적 DDL 에서 `CASCADE` 는 편의가 아니라 **탐지기를 끄는 스위치**다(원칙 1).
--
-- 배제구역은 이제 DB 뷰가 전면 삭제되고, STEP1 감리 산출물(`audit_rules` 의 `hard_exclusion`)과
-- STEP2 정제본 파일에서 온다. 조례가 바뀌면 `REFRESH` 가 아니라 **파이프라인을 다시 돈다.**
-- =============================================================================


-- =============================================================================
-- [보완 5] 후보지 산출물 테이블 (AHP·멀티에이전트가 읽어감)
-- 지표를 한 번 계산해 캐싱 → 가중치만 바꿔 score를 UPDATE
--
-- 🔴 2026-08-11 — 여기 있던 `DROP TABLE IF EXISTS booth_candidates CASCADE;` 를 지웠다.
--    `schema_cleaned_data.sql` 의 `candidate_lands` DROP 과 **같은 종류의 줄**이다:
--    지금 이 테이블엔 80행이 들어 있고 `hearing_result_a.parcel_id` 가
--    `ON DELETE CASCADE` 로 매달려 있으며 `debate_logs` 가 다시 그걸 따른다
--    → 실측 **공청회 3건 · 발화 42행**이 같이 사라진다. 후보점은 `topN.geojson` 에서
--    다시 만들어지지만 **LLM 토론은 재구성이 안 된다**(발화는 Redis TTL 600초뿐).
--    적재기(`load_topn_candidates.py`)는 같은 손실을 `--force` 없이 **거부**하는데,
--    이 파일을 돌리면 그 방어를 **우회해서** 같은 일이 난다.
--
-- ⚠ `IF NOT EXISTS` 라서 이미 있으면 아래 정의는 **무시된다.** 실 DB 의 이 테이블은
--    `schema_step4_topn.sql` 이 얹은 컬럼(`domain`·`run_id`·`facility_type`·
--    `pnu`·`jibun`·`props_json`)을 더 갖고 있다 — 그쪽이 가산분의 정본이다.
-- =============================================================================

CREATE TABLE IF NOT EXISTS booth_candidates (
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

-- 기존 DB(또는 이전 시드)에 테이블은 있으나 신규 칼럼이 없는 경우를 위한 추가 구문
ALTER TABLE booth_candidates
    ADD COLUMN IF NOT EXISTS land_id INTEGER REFERENCES candidate_lands(id),
    ADD COLUMN IF NOT EXISTS area_m2 DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS width_m DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS is_national BOOLEAN,
    ADD COLUMN IF NOT EXISTS shops_150m INTEGER,
    ADD COLUMN IF NOT EXISTS dist_transit DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS dist_litter DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS dist_existing DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS score DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS rank INTEGER,
    ADD COLUMN IF NOT EXISTS geom GEOMETRY(Point, 4326),
    ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Point, 5186) GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;

CREATE INDEX IF NOT EXISTS idx_booth_candidates_geom  ON booth_candidates USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_booth_candidates_score ON booth_candidates (score DESC);


-- =============================================================================
-- [보완 6] 2계층(지역단위) 데이터의 시군구 구분  ★2026-08-11 신설
--
-- 문제: `candidate_lands`(6,524) · `national_properties`(2,486) 는 **용산 전용**인데
--       그렇다고 말하는 자리가 없다. 성동구를 넣는 순간 두 지역이 한 테이블에
--       섞이고, 공간조인(`load_topn_candidates.py` 의 `ST_Contains`)은 **가장 먼저
--       걸리는 필지**를 집는다 — 경계에 붙은 남의 구 필지를 집어도 안 터진다.
--
-- 🔴 테이블 이름에 `yongsan_` 을 붙이는 안은 **버렸다**(사람 결정 2026-08-11).
--    이름이 지역이 되면 그건 도메인 값 하드코딩이고(원칙 2), 지자체가 늘 때마다
--    테이블·DDL·로더·FK 가 같이 늘어난다. `cadastral_lands` 가 이미
--    **`sigungu_cd` 컬럼으로** 가르고 있었다(로더의 멱등 가드도 그 컬럼을 본다) —
--    나머지 둘을 그 관례에 맞춘다. 어긋난 짝은 **기존 관례 쪽**으로 맞춘다.
--
-- 값의 출처: **코드 리터럴을 쓰지 않는다.** 지적도(`cadastral_lands`)와 공간으로
--       맞춰 그 필지의 `sigungu_cd` 를 가져온다. 못 맞추면 **NULL 로 남긴다** —
--       「하나뿐이니 그거겠지」로 채우면 없는 정보를 지어내는 것이다(원칙 1·5).
--
-- ⚠ `sigungu_cd` 는 **행자부 코드**다(용산 11170). 1계층 경계 3종의 `SIGUNGU_CD`
--    는 **통계청 코드**라 값이 다르다(11170 = 통계청 기준 구로) — 직접 조인 금지,
--    `admin_crosswalk` 경유.
-- =============================================================================

ALTER TABLE candidate_lands      ADD COLUMN IF NOT EXISTS sigungu_cd VARCHAR(5);
ALTER TABLE national_properties  ADD COLUMN IF NOT EXISTS sigungu_cd VARCHAR(5);

-- ① 포함 — 중심점(면) / 점 그대로가 지적 필지 안에 있으면 그 필지의 코드
UPDATE candidate_lands cl
SET sigungu_cd = s.sgg
FROM (
    SELECT c.id,
           (SELECT ca.sigungu_cd FROM cadastral_lands ca
             WHERE ST_Intersects(ca.geom_5186, ST_Centroid(c.geom_5186))
             LIMIT 1) AS sgg
    FROM candidate_lands c
    WHERE c.sigungu_cd IS NULL AND c.geom_5186 IS NOT NULL
) s
WHERE cl.id = s.id AND s.sgg IS NOT NULL;

UPDATE national_properties np
SET sigungu_cd = s.sgg
FROM (
    SELECT n.id,
           (SELECT ca.sigungu_cd FROM cadastral_lands ca
             WHERE ST_Intersects(ca.geom_5186, n.geom_5186)
             LIMIT 1) AS sgg
    FROM national_properties n
    WHERE n.sigungu_cd IS NULL AND n.geom_5186 IS NOT NULL
) s
WHERE np.id = s.id AND s.sgg IS NOT NULL;

-- ② 최근접 — 도로·필지 틈에 떨어진 것만. **50m 상한**을 둔다.
--    상한이 없으면 아무리 먼 것도 붙어 「가장 가까운 구」가 곧 답이 된다.
--    실측: `candidate_lands` 6,524 중 ① 로 6,522 · ② 로 2(중심점이 필지 밖 0.2m·6.9m).
UPDATE candidate_lands cl
SET sigungu_cd = s.sgg
FROM (
    SELECT c.id,
           (SELECT ca.sigungu_cd FROM cadastral_lands ca
             WHERE ST_DWithin(ca.geom_5186, ST_Centroid(c.geom_5186), 50)
             ORDER BY ca.geom_5186 <-> ST_Centroid(c.geom_5186) LIMIT 1) AS sgg
    FROM candidate_lands c
    WHERE c.sigungu_cd IS NULL AND c.geom_5186 IS NOT NULL
) s
WHERE cl.id = s.id AND s.sgg IS NOT NULL;

UPDATE national_properties np
SET sigungu_cd = s.sgg
FROM (
    SELECT n.id,
           (SELECT ca.sigungu_cd FROM cadastral_lands ca
             WHERE ST_DWithin(ca.geom_5186, n.geom_5186, 50)
             ORDER BY ca.geom_5186 <-> n.geom_5186 LIMIT 1) AS sgg
    FROM national_properties n
    WHERE n.sigungu_cd IS NULL AND n.geom_5186 IS NOT NULL
) s
WHERE np.id = s.id AND s.sgg IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_candidate_lands_sgg     ON candidate_lands (sigungu_cd);
CREATE INDEX IF NOT EXISTS idx_national_properties_sgg ON national_properties (sigungu_cd);

COMMENT ON COLUMN candidate_lands.sigungu_cd IS
  '행자부 시군구코드. 지적도(cadastral_lands) 공간매칭으로 유도 — 못 맞추면 NULL(추측 안 함)';
COMMENT ON COLUMN national_properties.sigungu_cd IS
  '행자부 시군구코드. 지적도(cadastral_lands) 공간매칭으로 유도 — 못 맞추면 NULL(추측 안 함)';

-- 검증: NULL 이 남았으면 그 행은 지적도 50m 안에 짝이 없다는 뜻이다.
-- SELECT sigungu_cd, count(*) FROM candidate_lands     GROUP BY 1;
-- SELECT sigungu_cd, count(*) FROM national_properties GROUP BY 1;
