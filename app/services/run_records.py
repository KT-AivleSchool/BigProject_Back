"""run 메타데이터(4계층 중 ③)를 Postgres `run_records` 에 적는다.

**정본은 `status.json` 이고 여기는 사본이다.** 이 모듈이 실패해도 파이프라인은
그대로 돈다 — 그래서 모든 함수는 **예외를 던지지 않고 실패 사유 문자열을 돌려준다**
(성공이면 `None`). 🔴 다만 「catch 한다」와 「조용히 삼킨다」는 다르다(원칙 1·4):
사유를 받은 쪽(러너)은 `status.json` 의 `run_record_errors` 와 `warning` 로그에
반드시 남긴다. `except: pass` 로 구현하면 마이페이지에서 **없는 run** 이 되고,
왜 없는지 알 방법이 사라진다.

🔴 **왜 동기 psycopg 인가.** 앱 엔진은 `create_async_engine`(asyncpg) 전용인데
   러너는 `threading.Thread` 위의 **동기 코드**다. 거기서 `asyncio.run()` 으로
   비동기 세션을 쓰면 모듈 전역 엔진의 커넥션 풀이 **닫힌 루프에 묶인 커넥션**을
   들고 있게 되어, 그 뒤 요청이 `Event loop is closed` 로 죽는다
   (2026-08-11 작업일지 §5-1 실측). 인프로세스 동기 경로의 관례는
   `scripts/load_region_boundaries.py:139` 과 같은 **psycopg + connect_timeout** 이다.

🔴 **`connect_timeout` 을 반드시 준다.** 없으면 libpq 가 DB 부재를 260초(실측) 뒤에야
   알려준다 — 기다림이 실패로 드러나지 않으면 사용자에겐 "원래 느린 기능"이 된다.
   DB 가 죽어 있으면 run 시작이 `DB_CONNECT_TIMEOUT`(기본 10초)만큼 **늦어진다.**
   느려지는 것이지 죽지 않는다 — 그 대가로 「행이 없는 run」이 안 생긴다.
"""

from __future__ import annotations

from datetime import datetime

import psycopg

from app.config import DB_CONNECT_TIMEOUT, settings

_TABLE = "run_records"

_COLS = (
    "run_id, user_id, domain, mode, last_known_status, user_input, "
    "started_at, finished_at, loaded_audit_rules, loaded_booth_candidates"
)

# 🔴 UPSERT 여야 하는 이유 — **행이 없는 상태가 정상 경로에 있다.**
#    발급 시 INSERT 가 실패해도 run 은 계속 도니(위 설계), 종료가 단순 UPDATE 면
#    그 run 은 완주하고도 이력에서 통째로 사라진다(원칙 4).
#    ⚠ DO UPDATE 는 **끝나면서 알게 된 것만** 덮는다. `user_id`·`domain`·`mode`·
#    `user_input`·`started_at` 은 발급 시점 값이 정본이라 건드리지 않는다.
_UPSERT_END = f"""
INSERT INTO {_TABLE} ({_COLS})
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id) DO UPDATE SET
    last_known_status       = EXCLUDED.last_known_status,
    finished_at             = EXCLUDED.finished_at,
    loaded_audit_rules      = EXCLUDED.loaded_audit_rules,
    loaded_booth_candidates = EXCLUDED.loaded_booth_candidates
"""

_INSERT_START = f"""
INSERT INTO {_TABLE} ({_COLS})
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id) DO NOTHING
"""


def _connect():
    return psycopg.connect(settings.DATABASE_URL, connect_timeout=DB_CONNECT_TIMEOUT)


def _ts(value: str | None) -> datetime | None:
    """status.json 의 ISO 문자열 → tz 붙은 datetime.

    🔴 `status.json` 의 시각은 `datetime.now().isoformat(timespec="seconds")` 라
       **naive 로컬 시각**이다. 컬럼은 `TIMESTAMPTZ` 이므로 naive 를 그대로 넣으면
       Postgres 가 **세션 타임존으로 해석**한다 — 컨테이너가 UTC 면 KST 값이 9시간
       밀린 채 조용히 저장된다. `.astimezone()` 이 (인자 없이) naive 에 **로컬
       오프셋을 붙여** 주므로 여기서 붙여 보낸다. 값을 바꾸는 게 아니라
       **빠져 있던 정보를 채우는** 것이다.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.astimezone() if dt.tzinfo is None else dt


def _row(doc: dict, user_input: str | None) -> tuple:
    """status.json 문서 한 개 → 이 표의 한 행."""
    loaded = doc.get("loaded") or {}
    return (
        doc["run_id"],
        doc.get("user_id"),
        doc["domain"],
        doc["mode"],
        doc.get("status") or "queued",
        user_input,
        _ts(doc.get("started_at")),
        _ts(doc.get("finished_at")),
        loaded.get("audit_rules"),
        loaded.get("booth_candidates"),
    )


def _reason(ex: Exception) -> str:
    return f"{type(ex).__name__}: {ex}"


def record_run_start(doc: dict, user_input: str | None = None) -> str | None:
    """run 발급을 기록한다(`last_known_status='queued'`). 성공이면 None.

    ⚠ `doc["status"]` 를 그대로 쓴다 — 발급 직후라 `queued` 다. 리터럴로 박으면
      호출 자리가 옮겨졌을 때 사실과 어긋난다(원칙 4).
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(_INSERT_START, _row(doc, user_input))
            if cur.rowcount == 0:
                # 🔴 조용히 성공으로 넘기지 않는다. `_new_run_id` 는 최고수위 원장
                #    (`runs/run_seq.json`)을 쓰므로 같은 run_id 가 두 번 발급될 수
                #    없다 — 여기 걸렸다는 건 원장이 지워졌거나 남이 같은 id 로 행을
                #    넣었다는 뜻이고, 그러면 그 run 의 이력이 남의 것과 섞인다.
                return f"이미 같은 run_id 의 행이 있다: {doc['run_id']}"
        return None
    except Exception as ex:  # noqa: BLE001 — 종류를 안 가린다는 것 자체가 요점이다
        return _reason(ex)


def record_run_end(doc: dict, user_input: str | None = None) -> str | None:
    """run 종료를 기록한다(UPSERT). 성공이면 None."""
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(_UPSERT_END, _row(doc, user_input))
        return None
    except Exception as ex:  # noqa: BLE001
        return _reason(ex)


def record_runs_end(items: list[tuple[dict, str | None]]) -> str | None:
    """여러 run 의 종료를 **한 연결로** 기록한다. 부팅 시 `reap_orphans` 용.

    🔴 왜 한 연결인가 — 고아가 N개면 연결도 N번이고, DB 가 죽어 있으면
       `10초 × N` 만큼 **lifespan 이 멈춘다**(그동안 서버가 안 뜬다).
       한 번만 붙으면 최악이 10초다.
    """
    if not items:
        return None
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.executemany(_UPSERT_END, [_row(d, ui) for d, ui in items])
        return None
    except Exception as ex:  # noqa: BLE001
        return _reason(ex)
