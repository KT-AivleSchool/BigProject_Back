-- ==============================================================================
-- 앱 기본 테이블 DDL (schema.sql)
-- ==============================================================================
--
-- 🔴 이 파일은 원래 「OmniSite PostGIS 16대 물리 데이터베이스 마스터 스키마」였다.
--    2026-08-11 에 19개 선언 중 **17개를 들어냈다**(아래 경위). 남은 것은
--    `districts` · `dong_boundaries` 둘뿐이고, 이 둘은 **여기가 유일한 DDL** 이다.
--
--    `README.md` 는 빈 DB 에 이 파일을 주입하라고 안내한다 —
--    즉 여기 적힌 것은 **실행된다.** 낡은 선언을 남겨두면 실행되는 함정이 된다.
-- ==============================================================================
--
-- 🔴 무엇을 왜 들어냈나 (2026-08-11, 실측)
--
-- ① **실 DB 에 없고 코드 참조도 0회인 11개** — 만들 이유가 없다.
--    nosmoking_zones · childcare_centers · transit_stations · transit_passengers ·
--    population_stats · commercial_shops · civil_complaints · trash_bins ·
--    age_demographics · cigarette_dumping_zones · ahp_models
--
--    `app/` · `scripts/` · `tests/` 전수로 참조 **0회**다. `ahp_models` 는 특히
--    `/ahp` 라우터·`ahp_service` 와 함께 2026-08-04 에 **폐기**된 계통이다
--    (CLAUDE.md: "미구현이 아니라 폐기 — 만들면 안 된다").
--    `commercial_shops` 는 여기 정의(shop_name·category_code·district_id)와
--    `schema_cleaned_data.sql` 정의(road_address·business_category)가 **컬럼부터
--    달랐다** — 같은 이름의 다른 스키마가 두 파일에 있었다.
--
-- ② **정본이 ORM 인 5개** — `users` · `rag_feedback_log` · `audit_rules` ·
--    `conflict_simulations` · `verified_precedents`.
--    정본은 `app/db/models/`, 생성은 `python scripts/create_missing_tables.py --yes`.
--
--    앞의 둘은 실 DB·ORM 과 **컬럼이 정확히 같았다** — 같아도 지운다.
--    같은 스키마를 두 곳에서 정의하면 언젠가 한쪽만 바뀐다. 실제로 뒤의 셋이 그랬다:
--      · `audit_rules`          여기 **7컬럼** ↔ 실 DB·ORM **20컬럼**
--        (`domain`·`run_id`·`role_index`·`target_facility`·`confirmed`… 전부 없었다)
--      · `conflict_simulations` 여기 **9컬럼** ↔ 실 DB **12컬럼**. 필지 참조가
--        `cadastral_land_id` 인데 실제로 쓰는 건 `parcel_id`(FK→`booth_candidates.id`)다
--        (경위: CLAUDE.md 「대조 대상이 둘인 줄 알았는데 셋」 · 처치: `schema_step5.sql`)
--      · `verified_precedents`  여기 **6컬럼** ↔ 실 DB **9컬럼**
--        (`document_no`·`similarity_score`·`classification_status` 없음)
--
--    🔴 `CREATE TABLE IF NOT EXISTS` 라서 **빈 DB 에 이 파일을 먼저 돌리면 틀린
--       스키마가 만들어지고**, 뒤이은 `create_all(checkfirst=True)` 이 「이미 있다」며
--       건너뛴다. 이름이 같아 `SELECT` 를 짤 때까지 안 보인다.
--
-- ③ **정본이 다른 `.sql` 인 1개** — `cadastral_lands`.
--    정본은 `schema_cadastral.sql`(로더 `scripts/load_cadastral.py`).
--    여기 정의는 8컬럼(district_id·dong_id·land_use_code·ownership_type)인데
--    실 DB 는 9컬럼(pnu·jibun·sigungu_cd·sgg_oid·bchk·base_ym·geom·geom_5186)으로
--    **겹치는 컬럼이 id·pnu·jibun·geom 넷뿐**이다. 44,459행이 들어 있다.
--
-- ⚠ `CREATE EXTENSION` 은 남긴다 — 다른 스키마 파일들의 전제이고 멱등이다.
-- ==============================================================================

-- 0. 핵심 지리 공간 및 벡터 유사도 검색 확장팩 활성화
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. 자치구역 마스터
--    `dong_boundaries.district_id` 가 참조한다.
CREATE TABLE IF NOT EXISTS districts (
    id SERIAL PRIMARY KEY,
    district_name VARCHAR(100) NOT NULL, -- 예: "서울특별시 용산구"
    sig_cd VARCHAR(5) UNIQUE NOT NULL,   -- 법정 시군구 코드 (예: "11170")
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. 행정동 경계 (앱 전용 · 용산 16행)
--
--    🔴 1계층 경계 3종(`adm_dong_boundaries` 등, `schema_region_boundaries.sql`)과
--       **다른 테이블**이다. 이쪽은 앱이 쓰는 자치구 1개짜리 사본이고 `geom_5186` 도 없다.
--
--    🔴 지우면 안 된다 — ORM 이 **FK 로 가리키는데 ORM 에 선언은 없는** 테이블이라
--       `scripts/create_missing_tables.py:44` 가 실 DB 에서 reflect 해 온다.
--       없으면 `create_all` 이 `NoReferencedTableError` 로 죽는다
--       (CLAUDE.md 「FK 는 DB 가 아니라 metadata 에서 풀린다」).
CREATE TABLE IF NOT EXISTS dong_boundaries (
    id SERIAL PRIMARY KEY,
    district_id INT REFERENCES districts (id) ON DELETE CASCADE,
    dong_code VARCHAR(10) UNIQUE NOT NULL, -- 외래키 참조를 위한 UNIQUE 제약
    dong_name VARCHAR(100) NOT NULL,
    geom GEOMETRY (MultiPolygon, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dong_geom ON dong_boundaries USING GIST (geom);
