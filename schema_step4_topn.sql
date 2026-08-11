-- =============================================================================
-- STEP4 Top-N → booth_candidates 적재를 위한 **가산분** (멱등)
-- 2026-08-10
--
-- 왜 필요한가
--   `booth_candidates`(schema_cleaned_data_add.sql:204)는 흡연 도메인 1회 적재를
--   전제로 만들어졌다. 도메인·실행(run)을 구분하는 컬럼이 없어서
--     ① 성동구를 적재하면 흡연 후보와 **한 테이블에 섞인다**(어느 게 어느 도메인인지
--        판별할 방법이 없다)
--     ② 재적재 시 지울 범위를 특정할 수 없어 `TRUNCATE` 밖에 없는데, 그건
--        남의 도메인을 지운다. `audit_rules` 에서 이미 같은 문제를 겪었다(2026-08-09).
--
--   그리고 `topN.geojson` 의 속성은 **도메인마다 다르다**. `gam4_export.export_topn`
--   이 "geometry 외 컬럼은 그대로 속성으로 싣는다"고 되어 있어, 흡연에만 있는
--   `국유_지분율` 같은 값이 갈 자리가 고정 컬럼엔 없다. 버리면 산출물이 줄어든 채
--   DB 에 들어간다(원칙 4) → `props_json` 에 **원본 속성 전체**를 같이 남긴다.
--
-- 무엇을 하지 않는가
--   기존 컬럼(`shops_150m`·`dist_transit`·`dist_litter`·`dist_existing`)은
--   **건드리지 않는다.** 이 넷은 흡연 도메인 지표이고 `topN.geojson` 에 없다.
--   NULL 로 남는다 — 없는 값을 지어내지 않는다. 지우는 것도 이 파일 밖의 결정이다.
--
-- 적용:
--   docker exec -i omnisite-postgres-db psql -U postgres -d omnisite < schema_step4_topn.sql
--   🔴 `-i` 없으면 stdin 이 무시되고 **출력도 없이 exit 0** 이다.
-- =============================================================================

BEGIN;

ALTER TABLE booth_candidates
    ADD COLUMN IF NOT EXISTS domain        VARCHAR(50),
    ADD COLUMN IF NOT EXISTS run_id        VARCHAR(64),
    ADD COLUMN IF NOT EXISTS facility_type VARCHAR(100),
    ADD COLUMN IF NOT EXISTS pnu           VARCHAR(19),
    ADD COLUMN IF NOT EXISTS jibun         VARCHAR(100),
    ADD COLUMN IF NOT EXISTS props_json    JSONB;

COMMENT ON COLUMN booth_candidates.domain IS
    'STEP4 를 돌린 도메인 폴더명(예: 흡연). 재적재는 (domain, run_id) 단위로만 지운다';
COMMENT ON COLUMN booth_candidates.run_id IS
    '산출물 출처. runs/<id> 면 그 id, 정본(datasets/step4_output)이면 ''정본'' — 어느 STEP 폴더에서 왔나가 아니라 어느 실행에서 나왔나다. load_audit_data 와 같은 값이어야 조인된다';
COMMENT ON COLUMN booth_candidates.facility_type IS
    '대상 시설. audit_rules.target_facility 와 같은 어휘를 쓴다(화면5 조회 키)';
COMMENT ON COLUMN booth_candidates.pnu IS
    'topN.geojson 의 PNU(19자리). 필지 식별용 — candidate_lands 에는 이 컬럼이 없다';
COMMENT ON COLUMN booth_candidates.props_json IS
    'topN.geojson 속성 원본 전체. 고정 컬럼에 자리가 없는 도메인별 지표를 잃지 않기 위함';
COMMENT ON COLUMN booth_candidates.rank IS
    'topN.geojson 의 `순위`. **1 이 최상위**다. 다만 점수 내림차순이 아니라 MCLP 커버 기여 그리디 순이다(흡연 실측 4위 0.7793 > 1위 0.7703) — ORDER BY score DESC 로 뽑으면 다른 점이 나온다. rank=1 은 추천이지 강제가 아니다(2026-08-10): 화면4 에서 사람이 고른 후보를 화면5 가 쓴다';
COMMENT ON COLUMN booth_candidates.land_id IS
    '후보점이 놓인 필지(candidate_lands.id). 공간조인으로 유도한다 — PNU 로는 조인이 안 된다. 매칭 실패 시 NULL';

-- `/simulations/candidates` 가 이 도메인의 후보를 **순위대로 나열**하는 경로
-- (1순위 하나를 뽑는 게 아니다 — 고르는 건 사람이다). rank 는 NULL 이 뒤로 가야 한다.
CREATE INDEX IF NOT EXISTS idx_booth_candidates_domain_rank
    ON booth_candidates (domain, rank NULLS LAST);

COMMIT;
