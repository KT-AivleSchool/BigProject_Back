-- ============================================================================
-- STEP5 공청회 시뮬레이션 저장 구조 정합 (2026-08-09, B안)
--
-- 왜 필요한가
--   `conflict_simulations` 는 ORM · `schema.sql` · 실제 DB 가 **셋 다 달랐다.**
--   공통 컬럼이 `id`·`created_at` 둘뿐이라 INSERT 가 100% 실패했고
--   (`UndefinedColumnError: column "parcel_id" does not exist`),
--   그래서 이 테이블도 `verified_precedents` 도 **0행**이었다.
--
--   컬럼 이름만 맞춰선 안 끝난다는 게 2026-08-09 실측으로 드러났다:
--     ① 엔진은 시나리오를 **1개만** 낸다. 컬럼은 A/B/C 3칸이다
--     ② `debate_logs` 14행이 갈 곳이 없다. Redis TTL 600초가 지나면 재구성 불가다
--     ③ 런타임 `parcel_id` 는 `booth_candidates.id` 인데 기존 FK 는
--        `candidate_lands` 다 — **id 공간이 다르다**(1행 ↔ 6,524행).
--        그냥 넣으면 FK 는 통과하고 **다른 필지**를 가리킨다
--
-- 방침 — 우리 코드의 산출물을 우선한다(사람 지시 2026-08-09).
--   기존 컬럼은 **하나도 지우지 않는다.** 남의 팀이 만든 것이고 지금 0행이라
--   지워서 얻을 게 없다. 채울 수 있는 것(css_score·css_vector·시나리오 1칸·
--   candidate_land_id)은 실제로 채우고, 지어낼 수 없는 것은 NULL 로 둔다.
--   NOT NULL 은 **완화하지 않는다** — 반쪽짜리 결과를 저장하지 않기 위한 방어다.
--
-- 적용:
--   docker exec -i omnisite-postgres-db psql -U postgres -d omnisite < schema_step5.sql
--   🔴 `docker exec` 에 **`-i` 가 없으면 stdin 이 무시되고 exit 0** 이다(조용한 실패).
-- ============================================================================

BEGIN;

-- ── 1. conflict_simulations — 우리 산출물이 갈 컬럼 3개 추가 ─────────────────
--
-- parcel_id : 런타임 값 그대로. `app/db/models/simulation.py` 의 `Parcel` 은
--             `__tablename__ = "booth_candidates"` 이므로 FK 대상도 거기다.
--             기존 `candidate_land_id`(→ candidate_lands) 는 **다른 축**이라
--             대체가 아니라 병기한다. 값은 booth_candidates.land_id 로 채운다.
ALTER TABLE conflict_simulations
    ADD COLUMN IF NOT EXISTS parcel_id     INTEGER,
    ADD COLUMN IF NOT EXISTS facility_type VARCHAR(100),
    ADD COLUMN IF NOT EXISTS result_json   JSONB;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'conflict_simulations_parcel_id_fkey'
    ) THEN
        ALTER TABLE conflict_simulations
            ADD CONSTRAINT conflict_simulations_parcel_id_fkey
            FOREIGN KEY (parcel_id) REFERENCES booth_candidates(id) ON DELETE CASCADE;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_conflict_simulations_parcel
    ON conflict_simulations(parcel_id);

-- 🔴 parcel_id 를 NOT NULL 로 조인다 (2026-08-11, 사람 승인).
--
--   이 테이블에는 `run_id` 컬럼이 **없다.** run 에 닿는 경로는
--     parcel_id → booth_candidates.id → booth_candidates.run_id
--   조인 **하나뿐**이다. parcel_id 가 NULL 이면 「어느 실행의 어느 입지를
--   토론했나」를 알 방법이 아예 없어진다.
--
--   왜 run_id 컬럼을 대신 넣지 않았나 — 값이 이미 두 곳에 있다:
--     ⓐ 위 조인 (= 이 후보점이 지금 속한 run)
--     ⓑ result_json->'basis'->>'run_id' (= 토론할 때 근거로 삼은 run)
--   컬럼은 **세 번째 사본**이 되고, ⓐ·ⓑ 는 원래 다른 문장이라 어느 쪽을
--   담을지 정할 수 없다(함정: 필드 하나로 두 의미). FK 가 ON DELETE CASCADE 라
--   ⓐ 는 dangling 이 구조적으로 불가능한데, 컬럼에는 그 보증이 없다.
--
--   ⚠ 옛 행 중 result_json->'basis' 가 없는 것이 있다(basis_snapshot 은
--     2026-08-11 신설). 그 행들에게는 ⓐ 조인이 **유일한** 경로다.
--
--   ⚠ 이 구문은 멱등이다(이미 NOT NULL 이면 no-op). NULL 이 하나라도 있으면
--     **일부러 터진다** — 조용히 건너뛰면 조일 이유가 사라진다(원칙 1).
--     그때는 먼저 그 행의 대상 후보점을 찾아 채우거나 지운 뒤 다시 친다.
--
--   ⚠ 새 DB 는 이 파일이 아니라 ORM(`app/db/models/simulation.py`)의
--     `nullable=False` 로 만들어진다 — 이 테이블은 어느 .sql 에도
--     CREATE TABLE 이 없고 `create_missing_tables.py` 가 만든다. **양쪽을 같이 고칠 것.**
ALTER TABLE conflict_simulations ALTER COLUMN parcel_id SET NOT NULL;

COMMENT ON COLUMN conflict_simulations.parcel_id IS
    '토론 대상 후보점. booth_candidates.id (런타임 값). candidate_land_id 와 id 공간이 다르다. '
    'NOT NULL — 이 테이블엔 run_id 컬럼이 없어 run 에 닿는 경로가 이 조인 하나뿐이다';
COMMENT ON COLUMN conflict_simulations.candidate_land_id IS
    '위 후보점이 놓인 필지. booth_candidates.land_id 에서 유도해 채운다';
COMMENT ON COLUMN conflict_simulations.result_json IS
    'STEP5 최종 산출물 전체(원본). 아래 개별 컬럼은 여기서 뽑아낸 사본이다';
COMMENT ON COLUMN conflict_simulations.normal_scenario IS
    '시나리오 B(조건부 타결). 엔진은 매 실행마다 A/B/C 중 1개만 낸다 — 나머지 둘은 NULL 이 정상이다';
COMMENT ON COLUMN conflict_simulations.optimal_scenario IS
    '시나리오 A(원만한 타결). 도출되지 않았으면 NULL';
COMMENT ON COLUMN conflict_simulations.worst_scenario IS
    '시나리오 C(협상 결렬). 도출되지 않았으면 NULL';
COMMENT ON COLUMN conflict_simulations.confidence_score IS
    '미사용. 엔진이 내는 final_acceptance_score 는 「수용도」라 의미가 다르다 — 지어내 채우지 않는다';


-- ── 2. debate_logs — 토론 발화를 행 단위로 ──────────────────────────────────
--
-- 왜 별도 테이블인가: result_json 에도 통짜로 들어가지만, 발화 단위 조회·
-- 인용·화면6 보고서 재구성이 통짜 JSON 으로는 안 된다. STEP1~4 주요 산출물을
-- 테이블로 둔 것과 같은 방침이다.
-- 실측 규모: 1회 토론 = 완성 발화 14행 (SSE 패킷 1,434건은 토큰 단위 조각이다).
CREATE TABLE IF NOT EXISTS debate_logs (
    id            SERIAL PRIMARY KEY,
    simulation_id INTEGER NOT NULL
                  REFERENCES conflict_simulations(id) ON DELETE CASCADE,
    turn_index    INTEGER NOT NULL,           -- 0-base 발화 순서
    sender        VARCHAR(50)  NOT NULL,      -- 찬성 / 반대 / 정부 / 시스템
    message       TEXT         NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_debate_logs_turn UNIQUE (simulation_id, turn_index)
);

CREATE INDEX IF NOT EXISTS idx_debate_logs_simulation ON debate_logs(simulation_id);

COMMENT ON TABLE  debate_logs IS
    'STEP5 공청회 토론 발화 1건 = 1행. conflict_simulations.result_json 의 debate_logs 와 같은 내용이다';
COMMENT ON COLUMN debate_logs.turn_index IS
    '발화 순서. UNIQUE(simulation_id, turn_index) 로 중복 적재를 막는다';


-- ── 3. verified_precedents — 코드가 쓰는 컬럼 3개 추가 ──────────────────────
--
-- ⚠ 여기는 **DB 이름이 맞고 코드 이름이 틀렸다.** `audit.py:94` 가
--   `VerifiedPrecedent(parcel_id=simulation_id, ...)` 로 넣는다 — 이름은
--   parcel_id 인데 값은 simulation_id 다. 실 DB 의 `conflict_simulation_id`
--   가 실제 의미다. ORM 쪽을 DB 이름에 맞춘다.
-- `matched_scenario` → 기존 `actual_scenario` · `extracted_text` →
-- 기존 `document_ocr_text` 로 흡수한다. 같은 값을 담을 컬럼을 두 개 만들지 않는다.
ALTER TABLE verified_precedents
    ADD COLUMN IF NOT EXISTS document_no           VARCHAR(100),
    ADD COLUMN IF NOT EXISTS similarity_score      NUMERIC,
    ADD COLUMN IF NOT EXISTS classification_status VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_verified_precedents_simulation
    ON verified_precedents(conflict_simulation_id);

COMMENT ON COLUMN verified_precedents.conflict_simulation_id IS
    '예측을 낸 시뮬레이션. audit.py 가 simulation_id 를 넘긴다 (예전 ORM 이름 parcel_id 는 오기였다)';
COMMENT ON COLUMN verified_precedents.actual_scenario IS
    '준공 공문에서 분류기가 판정한 실제 시나리오(A/B/C). 코드의 matched_scenario 가 이 값이다';
COMMENT ON COLUMN verified_precedents.document_ocr_text IS
    'PDF 텍스트 레이어 원문. 코드의 extracted_text 가 이 값이다';

COMMIT;
