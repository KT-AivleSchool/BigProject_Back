-- ============================================================================
-- 화면5 B 다인 토론 결과 저장 (2026-08-11, 사람 승인)
--
-- 왜 별도 테이블인가 — `conflict_simulations` 를 재사용하지 않는 이유는 셋이다.
--   ① `css_score`·`css_vector` 가 **NOT NULL** 이다. 둘 다 A 대립 토론의 지표
--      (갈등민감도 · 요인별 AHP 가중치)이고 B 엔진은 그 값을 내지 않는다.
--      넣으려면 NOT NULL 을 완화해야 하는데, 그건 **A 의 반쪽짜리 결과도 저장
--      가능해진다**는 뜻이다 — 2026-08-09 B안이 일부러 세워둔 방어를 B 를 넣겠다고
--      허무는 셈이다.
--   ② 시나리오 칸이 `optimal/normal/worst` 3개다. B 는 페르소나별 최종 시나리오를
--      내므로 3칸에 안 들어간다. 억지로 접으면 없는 대응관계를 지어내게 된다.
--   ③ 두 엔진은 **합치지 않기로 한 것**이다(2026-08-10). 저장을 합치면 조회에서
--      다시 갈라야 하고, 그 갈라내는 규칙이 곧 「엔진이 하나인 척」이 된다.
--
-- 🔴 `run_id` 컬럼을 두지 않는다. A 와 **같은 경로**로 잇는다:
--      hearing_results_b.parcel_id → booth_candidates.id → booth_candidates.run_id
--    여기에 run_id 를 복사해 두면 후보점 쪽과 어긋날 수 있고, 어긋나도 안 터진다.
--    (`conflict_simulations` 에 run_id 가 없는 것과 같은 이유다 — 그건 결함이
--     아니라 선택이다.)
--
-- 🔴 `result_json` 은 **통짜**다. B 의 산출물 모양은 아직 움직이고 있고
--    (`final_scenarios`·`evaluations`·`css_levels` 전부 B 담당자 소유),
--    지금 컬럼으로 쪼개면 그쪽이 키를 하나 바꿀 때마다 **조용히 NULL** 이 된다.
--    쪼개는 건 모양이 굳은 뒤에 한다. 발화(`messages`)도 여기 안에 들어간다 —
--    A 의 `debate_logs` 처럼 행으로 펴는 건 「발화 1건」의 경계가 정해진 뒤다.
--
-- 적용:
--   docker exec -i omnisite-postgres-db psql -U postgres -d omnisite < schema_step5_b.sql
--   🔴 `docker exec` 에 **`-i` 가 없으면 stdin 이 무시되고 exit 0** 이다(조용한 실패).
--   적용 후 `\d hearing_results_b` 로 확인할 것. rc=0 은 "명령이 돌았다"만 뜻한다.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS hearing_results_b (
    id            SERIAL PRIMARY KEY,

    -- 대상 후보점. A 와 **같은 id 공간**이다(booth_candidates.id).
    -- ON DELETE CASCADE 인 이유: 후보점을 덮어쓰면 그 후보점의 토론은 가리킬 곳이
    -- 없어진다. 남겨두면 dangling 이 되고, 그건 "토론이 있다"고 거짓말한다.
    -- 🔴 대신 지워지는 게 **적재기에서 보여야 한다** — `load_topn_candidates.py` 의
    --    `[CASCADED]` 줄이 이 테이블도 센다. 안 세면 5분짜리 토론이 소리 없이 사라진다.
    parcel_id     INTEGER NOT NULL
                  REFERENCES booth_candidates(id) ON DELETE CASCADE,

    -- 아래 셋은 요청이 아니라 **조달된 값**이다(`candidate_context.build_site_context`).
    -- 사람이 적은 topic/purpose 는 그대로, 안 적었으면 후보점 문맥에서 조립한 값.
    facility_type VARCHAR(100),
    topic         TEXT,
    purpose       TEXT,

    -- 사람이 고르고 고친 페르소나 배열(HITL 결과). 누가 토론했는지 없이는
    -- 결과를 읽을 수 없다 — result_json 안에 묻지 않고 밖으로 뺀다.
    personas      JSONB NOT NULL,

    -- 통짜 산출물. 위 docstring ③ 참조.
    result_json   JSONB NOT NULL,

    -- 발화 수. result_json 을 안 펴고도 "빈 토론인지"를 목록에서 판별하려고 둔다.
    -- A 의 `debate_logs` 행 수와 같은 자리다.
    message_count INTEGER NOT NULL DEFAULT 0,

    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_hearing_results_b_parcel
    ON hearing_results_b(parcel_id);

COMMENT ON TABLE  hearing_results_b IS
    '화면5 B 다인 토론(이해관계자 페르소나) 결과 1회 = 1행. A 는 conflict_simulations 다 — 합치지 않는다';
COMMENT ON COLUMN hearing_results_b.parcel_id IS
    '토론 대상 후보점(booth_candidates.id). run_id 는 여기를 조인해서 얻는다 — 복사해두지 않는다';
COMMENT ON COLUMN hearing_results_b.personas IS
    '사람이 확정한 페르소나 배열. /stakeholders/generate 제안을 사람이 고친 결과다';
COMMENT ON COLUMN hearing_results_b.result_json IS
    'B 산출물 통짜(final_scenarios·evaluations·css_levels·messages). 모양이 굳기 전엔 컬럼으로 안 쪼갠다';
COMMENT ON COLUMN hearing_results_b.message_count IS
    '완성 발화 수. 0 이면 토론이 한 마디도 안 나온 것이다 — 행이 없는 것과 다르다';

COMMIT;
