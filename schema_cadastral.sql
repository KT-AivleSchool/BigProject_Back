-- ============================================================================
-- 2계층 연속지적도 (지역 단위) — popzap #203 3️⃣ 요청
-- 원본: region_data/LSMD_CONT_LDREG_<시군구코드>_<YYYYMM>.shp (EPSG:5186, prj 포함)
-- 저장: geom = 4326(표출) / geom_5186 = GENERATED(거리·조인). 보관단위=지역.
-- ⚠️ 파일명/`COL_ADM_SE`의 시군구코드가 식별자 (폴더명 아님 — 11170=용산).
--    두 구 지적도가 함께 있을 때 코드로 골라야 조용한 오적재를 막는다.
-- ============================================================================
CREATE TABLE IF NOT EXISTS cadastral_lands (
    id         SERIAL PRIMARY KEY,
    pnu        VARCHAR(19) NOT NULL,       -- 필지고유번호(PNU)
    jibun      VARCHAR(50),                -- 지번+지목 (예: '409-115대')
    sigungu_cd VARCHAR(5),                 -- COL_ADM_SE 행정구역코드 (행자부, '11170')
    sgg_oid    BIGINT,                     -- 원본 SGG_OID
    bchk       VARCHAR(2),                 -- 원본 플래그(대부분 '1')
    base_ym    VARCHAR(6),                 -- 파일 기준연월 (예: '202607')
    geom       geometry(MultiPolygon, 4326) NOT NULL,
    geom_5186  geometry(MultiPolygon, 5186)
               GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED
);

-- 기존 DB(또는 이전 시드)에 테이블은 있으나 신규 칼럼이 없는 경우를 위한 추가 구문
ALTER TABLE cadastral_lands 
    ADD COLUMN IF NOT EXISTS sigungu_cd VARCHAR(5),
    ADD COLUMN IF NOT EXISTS sgg_oid BIGINT,
    ADD COLUMN IF NOT EXISTS bchk VARCHAR(2),
    ADD COLUMN IF NOT EXISTS base_ym VARCHAR(6),
    ADD COLUMN IF NOT EXISTS geom_5186 geometry(MultiPolygon, 5186) GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;

CREATE INDEX IF NOT EXISTS idx_cadastral_geom    ON cadastral_lands USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_cadastral_5186    ON cadastral_lands USING GIST(geom_5186);
CREATE INDEX IF NOT EXISTS idx_cadastral_pnu     ON cadastral_lands(pnu);
CREATE INDEX IF NOT EXISTS idx_cadastral_sigungu ON cadastral_lands(sigungu_cd);