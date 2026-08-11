-- ============================================================================
-- 1계층 전국 공용 경계 + 행정동 크로스워크 스키마 (popzap #179 요청)
-- 원본: datasets/region_data/BND_*_PG.shp (EPSG:5186, prj 포함),
--       행정동_크로스워크.csv (3,555행)
-- 저장 규약: geom = EPSG:4326(표출) / geom_5186 = GENERATED(연산·조인용, 미터)
--            → 다른 STEP2 정제 16종과 동일 패턴.
-- ⚠️ ADM_CD·SIGUNGU_CD·SIDO_CD 는 통계청 행정구역분류코드(행자부와 다름).
--    예) 11170 = 행자부 용산구 / 통계청 구로구. 조인 시 체계 혼동 주의.
--    크로스워크 region_code(=통계청 행정구역코드)로 boundaries 와 조인.
--
-- 🔴 산출물 코드체계별 조인 방법 (#205/#208 확정, 실측 검증 완료):
--   ┌ 공간조인 정제본(ADM_CD, 통계청) → adm_dong_boundaries.adm_cd 직접 조인 OK
--   ├ 생활인구 등 행자부 행정동코드 → admin_crosswalk.adm_code8 → region_code → 경계
--   └ topN·후보필지 → 코드 조인 '불가'. PNU 앞10자리는 '법정동코드'라 크로스워크(행정동)와
--       0/N 매칭됨. 반드시 아래 '공간조인'으로 붙일 것.
--
--   ⚠️ 통계청↔행자부를 '직접' 잘못 조인하면 0건이 아니라 '7/16 부분매칭'(조용히 실패).
--      절반쯤 붙고 9개 동이 소리 없이 빠짐 — "해보니 되던데요" 방지용으로 이 숫자를 남긴다.
--
--   -- topN 후보점(Point 4326)에 행정동 붙이기: ST_Within 권장(경계선 얹힘 방지)
--   -- SELECT t.*, b.adm_cd, b.adm_nm
--   -- FROM topn_points t JOIN adm_dong_boundaries b
--   --   ON ST_Within(ST_Transform(t.geom,5186), b.geom_5186);
--   -- 붙는 adm_cd 는 통계청(11030*) → 행자부 필요 시 region_code→adm_code8 한 번 더.
-- ============================================================================

-- 시도 경계 (17행) ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS sido_boundaries (
    id        SERIAL PRIMARY KEY,
    sido_cd   VARCHAR(2)  NOT NULL,   -- 통계청 시도코드 (예: '11')
    sido_nm   VARCHAR(40) NOT NULL,   -- '서울특별시'
    base_date VARCHAR(8),             -- BASE_DATE (예: '20250630')
    geom      geometry(MultiPolygon, 4326) NOT NULL,
    geom_5186 geometry(MultiPolygon, 5186)
              GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED
);
CREATE INDEX IF NOT EXISTS idx_sido_geom ON sido_boundaries USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_sido_5186 ON sido_boundaries USING GIST(geom_5186);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sido_cd ON sido_boundaries(sido_cd);

-- 시군구 경계 (252행) -------------------------------------------------------
CREATE TABLE IF NOT EXISTS sigungu_boundaries (
    id         SERIAL PRIMARY KEY,
    sigungu_cd VARCHAR(5)  NOT NULL,  -- 통계청 시군구코드 (예: '11010')
    sigungu_nm VARCHAR(60) NOT NULL,  -- '종로구'  ★자치구 필터 필수 컬럼
    base_date  VARCHAR(8),
    geom       geometry(MultiPolygon, 4326) NOT NULL,
    geom_5186  geometry(MultiPolygon, 5186)
               GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED
);
CREATE INDEX IF NOT EXISTS idx_sigungu_geom ON sigungu_boundaries USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_sigungu_5186 ON sigungu_boundaries USING GIST(geom_5186);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sigungu_cd ON sigungu_boundaries(sigungu_cd);

-- 행정동 경계 (3,559행) -----------------------------------------------------
CREATE TABLE IF NOT EXISTS adm_dong_boundaries (
    id        SERIAL PRIMARY KEY,
    adm_cd    VARCHAR(8)  NOT NULL,   -- 통계청 행정동코드(8) (예: '11010530')
    adm_nm    VARCHAR(60) NOT NULL,   -- '사직동'
    base_date VARCHAR(8),
    geom      geometry(MultiPolygon, 4326) NOT NULL,
    geom_5186 geometry(MultiPolygon, 5186)
              GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED
);
CREATE INDEX IF NOT EXISTS idx_adm_dong_geom ON adm_dong_boundaries USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_adm_dong_5186 ON adm_dong_boundaries USING GIST(geom_5186);
CREATE UNIQUE INDEX IF NOT EXISTS uq_adm_dong_cd ON adm_dong_boundaries(adm_cd);

-- 행정동 크로스워크 (3,555행, 좌표 없음) ------------------------------------
--   region_code(통계청) = adm_dong_boundaries.adm_cd 와 조인.
--   adm_code8(행자부) = 도메인 데이터의 행정동코드 계열과 매칭 시 사용.
CREATE TABLE IF NOT EXISTS admin_crosswalk (
    id           SERIAL PRIMARY KEY,
    region_code  VARCHAR(10),  -- 행정구역코드 (통계청, boundaries 조인키)
    adm_code10   VARCHAR(10),  -- 행정동코드 (10자리)
    adm_code8    VARCHAR(8),   -- 행정동코드8 (행자부 계열)
    adm_name     VARCHAR(60),  -- 행정동명
    sido_name    VARCHAR(40),
    sigungu_name VARCHAR(60),
    base_date    VARCHAR(20)   -- 기준일자 (예: '2025-04-01')
);
CREATE INDEX IF NOT EXISTS idx_crosswalk_region ON admin_crosswalk(region_code);
CREATE INDEX IF NOT EXISTS idx_crosswalk_adm8   ON admin_crosswalk(adm_code8);
