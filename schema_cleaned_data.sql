-- =============================================================================
-- 2계층(지역단위) 후보 부지 스키마
-- PostgreSQL + PostGIS
--
-- 🔴 이 파일은 원래 「용산구 흡연부스 입지 분석용 17종 데이터 스키마」였다.
--    2026-08-11 에 그 17종 중 16개를 **통째로 들어냈다**(아래 경위). 남은 것은
--    `candidate_lands` 하나이고, 그것도 여기서 만들기만 하고 컬럼 추가·인덱스·
--    유효성 보정은 `schema_cleaned_data_add.sql` 이 이어서 한다.
--
-- 실행 순서: schema_cleaned_data.sql → schema_cleaned_data_add.sql
-- =============================================================================
--
-- 🔴 무엇을 왜 들어냈나 (2026-08-11, 사람 결정)
--
-- ① **흡연 도메인 데이터셋 16종** — 프리셋 원본은 이제 DB 가 아니라 **디스크**에 둔다
--    (이슈 #215 계층 구분: 1계층 전국 경계·크로스워크 / 2계층 지역단위 지적·공유지 /
--    산출물). 파이프라인은 이 테이블들을 **한 번도 읽은 적이 없다** — STEP2 정제본
--    파일(`datasets/step2_output/`, `runs/<id>/step2/`)을 직접 읽고, 화면5 주변
--    POI 문맥도 `app/services/poi_context.py` 가 같은 파일을 읽는다.
--    실 DB 에서도 같은 날 지웠고 삭제 직전 덤프는 `datasets/_db_dump_20260811/*.csv`.
--
--    들어낸 이름: bus_stop_passenger_stats · street_trash_bins ·
--    subway_station_passenger_stats · living_population_stats · parks ·
--    cigarette_litter_hotspots · smoking_area_polygons · smoking_areas ·
--    commercial_shops · cctv_locations · public_wifi_locations · public_toilets ·
--    fire_water_facilities · cultural_event_locations · public_parking_lots ·
--    national_owned_properties
--
--    ⚠ `national_owned_properties` 는 이슈 #205 의 남은 되묻기였다 —
--    `schema_cleaned_data_add.sql` 의 **`national_properties`** 와 같은 원본
--    (국유부동산_위경도.csv 2,486행)을 컬럼명만 다르게 두 번 정의하고 있었다.
--    실 DB 에 있는 쪽은 `national_properties` 다. **정본은 `_add.sql` 쪽**으로
--    정하고 여기 것을 지운다 — 둘 다 두면 에러가 안 나서 영원히 안 갈린다.
--
-- ② **`DROP TABLE IF EXISTS candidate_lands CASCADE;`** — 이건 안 쓰는 테이블이
--    아니라 **살아 있는 2계층 데이터**(6,524행)를 지우는 줄이었다. 게다가
--    `booth_candidates.land_id` 가 이 테이블을 참조하므로 `CASCADE` 가 후보점까지
--    끌고 간다. 「기존에 잘못 생성된 테이블 초기화」라는 이름 아래 17개 DROP 이
--    나란히 있어서 **한 줄만 다르다는 게 안 보였다.**
--    이 파일은 이제 **아무것도 DROP 하지 않는다.**
--
-- ③ **`rag_feedback_log` · `audit_rules`** — 정본은 ORM 이다
--    (`app/db/models/`, 생성은 `python scripts/create_missing_tables.py --yes`).
--    여기 있던 정의는 **낡아 있었다**: `audit_rules` 가 7컬럼인데 실 DB·ORM 은
--    20컬럼이다(`domain`·`run_id`·`role_index`·`target_facility`·`confirmed` …
--    전부 없었다). `CREATE TABLE IF NOT EXISTS` 라서 빈 DB 에 이 파일을 먼저 돌리면
--    **틀린 스키마가 만들어지고** 뒤이은 `create_all(checkfirst=True)` 이 「이미
--    있다」며 건너뛴다 — 이름이 같아 `SELECT` 를 짤 때까지 안 보인다.
--    같은 스키마를 두 곳에서 정의하지 않는다.
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS postgis;

-- =============================================================================
-- 후보 부지 (2계층 · 지역단위)
-- 파일: 05_용산구_부지면적_좌표_흡연부스_후보_.csv
-- 실제 컬럼: 부지_WKT
--
-- ⚠ 파일명이 「흡연부스 후보」지만 내용은 **도메인 무관한 필지 폴리곤**이다
--   (시설 종류와 무관하게 그 지역의 부지 형상이다). 그래서 ① 의 도메인 데이터셋과
--   달리 남긴다. 소비처: `scripts/load_topn_candidates.py` 가 `ST_Contains` 공간조인
--   으로 `booth_candidates.land_id` 를 유도한다 — PNU 컬럼이 없어 코드 조인이 안 된다.
-- =============================================================================
CREATE TABLE IF NOT EXISTS candidate_lands (
    id SERIAL PRIMARY KEY,
    land_wkt TEXT NOT NULL,
    geom GEOMETRY (Polygon, 4326),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_candidate_lands_geom ON candidate_lands USING GIST (geom);

-- CSV 적재 후 WKT → geom
UPDATE candidate_lands
SET
    geom = ST_SetSRID (
        ST_GeomFromText (land_wkt),
        4326
    )
WHERE
    geom IS NULL
    AND land_wkt IS NOT NULL;
