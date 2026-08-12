# -*- coding: utf-8 -*-
"""run 메타데이터(4계층 ③) `run_records` 대조 — **실 DB 로** 친다.

    python app\\tools\\check_run_records.py

🔴 실 DB 에 행을 **넣고 지운다.** 끝에 지워졌는지까지 보고, 남의 행은 건드리지
   않는다(시작·끝 전체 행수를 대조한다). run_id 는 `r_YYYYMMDD_NNN` 패턴을 피해
   `_new_run_id` 의 번호 매김과 섞이지 않게 짓는다.

🔴 확인하려는 건 「INSERT 가 되는가」가 아니다. 그건 되는 게 당연하다.
   이 대조기가 보는 것은 **틀렸을 때 조용한 자리** 넷이다 —
     ① 시각이 9시간 밀리지 않는가 (naive 로컬 ↔ TIMESTAMPTZ)
     ② 종료가 **UPSERT** 인가 (행이 없는 상태가 정상 경로에 있다)
     ③ 종료가 발급 시점 값(domain·mode·user_input·started_at)을 **안 덮는가**
     ④ DB 가 죽었을 때 **raise 하지 않고 사유를 돌려주는가**
        (「catch 한다」≠「조용히 삼킨다」 — 러너가 그 사유를 status.json 에 남긴다)
   LLM 호출은 0회다.
"""
import io
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  line_buffering=True)

import psycopg  # noqa: E402

from app.config import DB_CONNECT_TIMEOUT, settings  # noqa: E402
from app.services import pipeline_runner as R  # noqa: E402
from app.services import run_records as RR  # noqa: E402

ok = fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label} {extra}")
    else:
        fail += 1
        print(f"  [!!] {label} {extra}")


def q(sql, params=None, one=True):
    with psycopg.connect(settings.DATABASE_URL,
                         connect_timeout=DB_CONNECT_TIMEOUT) as c, c.cursor() as cur:
        cur.execute(sql, params or ())
        # DELETE·UPDATE 는 결과집합이 없다 — fetchall 이 ProgrammingError 로 터진다.
        rows = cur.fetchall() if cur.description is not None else []
    return (rows[0] if rows else None) if one else rows


RUN_A = "r_대조_run_records_A"   # 발급 → 종료
RUN_B = "r_대조_run_records_B"   # 발급 없이 종료만 (UPSERT 복구)
RUNS = (RUN_A, RUN_B)

COLS = ["run_id", "user_id", "domain", "mode", "last_known_status", "user_input",
        "started_at", "finished_at", "loaded_audit_rules", "loaded_booth_candidates"]

before_total = q("select count(*) from run_records")[0]
print(f"시작 시점 run_records 행수 = {before_total} (남의 행은 안 건드린다)")

# ══════════════════════════════════════════════════════════════════
print("\n--- 1) 스키마")
cols = q("""select column_name, data_type, is_nullable
            from information_schema.columns where table_name='run_records'
            order by ordinal_position""", one=False)
names = [c[0] for c in cols]
types = {c[0]: c[1] for c in cols}
nulls = {c[0]: c[2] for c in cols}
chk("컬럼 10개·순서 일치", names == COLS, str(names))
chk("started_at 은 timestamptz", types.get("started_at") == "timestamp with time zone",
    types.get("started_at", ""))
chk("finished_at 은 timestamptz", types.get("finished_at") == "timestamp with time zone")
chk("user_id 는 nullable (익명 실행이 정상 상태다)", nulls.get("user_id") == "YES")
chk("domain·mode·last_known_status·started_at 은 NOT NULL",
    all(nulls.get(k) == "NO" for k in
        ("domain", "mode", "last_known_status", "started_at")))
cons = q("""select conname, pg_get_constraintdef(oid) from pg_constraint
            where conrelid='run_records'::regclass""", one=False)
defs = [c[1] for c in cons]
chk("PK 는 run_id (대리키 없음)", "PRIMARY KEY (run_id)" in defs, str(defs))
chk("user_id FK → users(id)",
    any(d.startswith("FOREIGN KEY (user_id) REFERENCES users(id)") for d in defs))
idx = q("""select indexdef from pg_indexes where tablename='run_records'""", one=False)
chk("마이페이지 인덱스 (user_id, started_at)",
    any("(user_id, started_at)" in i[0] for i in idx), str([i[0] for i in idx]))

# ══════════════════════════════════════════════════════════════════
print("\n--- 2) 발급 — 행은 시작할 때 만든다(`queued`)")
q("delete from run_records where run_id = any(%s)", (list(RUNS),))
doc = R._new_status(RUN_A, "흡연", R.MODE_FULL)
chk("`_new_status` 에 user_id 키가 있다", "user_id" in doc)
chk("그 값은 None (익명이 정상)", doc["user_id"] is None)
chk("`run_record_errors` 는 없다 (실패했을 때만 생긴다)",
    "run_record_errors" not in doc)

reason = RR.record_run_start(doc, "용산구 흡연부스 부지 선정")
chk("record_run_start 성공(None)", reason is None, str(reason))
row = q("select %s from run_records where run_id=%%s"
        % ", ".join(COLS), (RUN_A,))
chk("행이 1개 생겼다", row is not None)
r = dict(zip(COLS, row or [None] * len(COLS)))
chk("last_known_status = queued", r["last_known_status"] == "queued",
    str(r["last_known_status"]))
chk("user_id 는 NULL", r["user_id"] is None)
chk("domain·mode 기록", (r["domain"], r["mode"]) == ("흡연", "full"))
chk("user_input 기록", r["user_input"] == "용산구 흡연부스 부지 선정")
chk("finished_at 은 아직 NULL", r["finished_at"] is None)
chk("loaded_* 는 아직 NULL",
    r["loaded_audit_rules"] is None and r["loaded_booth_candidates"] is None)

print("\n--- 3) 시각 — naive 로컬을 TIMESTAMPTZ 에 넣어도 밀리지 않는다")
# 🔴 DB 세션 타임존은 UTC 다(실측). naive 를 그대로 넣으면 KST 가 UTC 로 해석돼
#    9시간 밀린 채 조용히 저장된다. `_ts` 가 로컬 오프셋을 붙여 보내는지 본다.
back = r["started_at"].astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
chk("status.json 의 started_at 과 초 단위로 같다", back == doc["started_at"],
    f"{back} ↔ {doc['started_at']}")

print("\n--- 4) 같은 run_id 로 두 번 발급 — 조용히 성공하지 않는다")
dup = dict(doc, domain="재활용")
reason = RR.record_run_start(dup, "다른 안건")
chk("사유를 돌려준다(None 아님)", reason is not None, str(reason))
chk("사유에 run_id 가 있다", RUN_A in (reason or ""))
r2 = dict(zip(COLS, q("select %s from run_records where run_id=%%s"
                      % ", ".join(COLS), (RUN_A,))))
chk("기존 행은 안 바뀐다", r2["domain"] == "흡연" and r2["user_input"] == r["user_input"],
    str((r2["domain"], r2["user_input"])))
chk("행수는 그대로 1", q("select count(*) from run_records where run_id=%s",
                         (RUN_A,))[0] == 1)

# ══════════════════════════════════════════════════════════════════
print("\n--- 5) 종료 — 끝나면서 알게 된 것만 덮는다")
end_doc = dict(
    doc,
    status="succeeded",
    finished_at=R._now_iso(),
    # 🔴 `cascaded` 는 「넣은 수」가 아니라 재적재로 **지워진 수**다. 옮기면 안 된다.
    loaded={"run_id": RUN_A, "audit_rules": 13, "booth_candidates": 20,
            "cascaded": {"hearing_result_a": 8, "debate_logs": 112}},
    # 발급 시점 값과 **다르게** 준다. 덮이면 안 된다.
    domain="재활용", mode="fixture",
)
reason = RR.record_run_end(end_doc, "덮이면 안 되는 문장")
chk("record_run_end 성공(None)", reason is None, str(reason))
r3 = dict(zip(COLS, q("select %s from run_records where run_id=%%s"
                      % ", ".join(COLS), (RUN_A,))))
chk("last_known_status = succeeded", r3["last_known_status"] == "succeeded")
chk("finished_at 채워짐", r3["finished_at"] is not None)
chk("loaded_audit_rules = 13", r3["loaded_audit_rules"] == 13)
chk("loaded_booth_candidates = 20", r3["loaded_booth_candidates"] == 20)
chk("domain 은 발급 값 유지(재활용 아님)", r3["domain"] == "흡연", str(r3["domain"]))
chk("mode 는 발급 값 유지", r3["mode"] == "full", str(r3["mode"]))
chk("user_input 은 발급 값 유지", r3["user_input"] == "용산구 흡연부스 부지 선정")
chk("started_at 은 발급 값 유지", r3["started_at"] == r["started_at"])
chk("행수는 여전히 1 (UPSERT 지 INSERT 가 아니다)",
    q("select count(*) from run_records where run_id=%s", (RUN_A,))[0] == 1)
chk("cascaded 를 담을 컬럼이 없다 (뜻이 정반대다)",
    not any("cascad" in c for c in COLS))

print("\n--- 6) UPSERT 복구 — 발급 기록이 실패했어도 완주한 run 은 남는다")
# 🔴 「행이 없는 상태」는 정상 경로에 있다. 발급 INSERT 가 실패해도 run 은 계속
#    도니, 종료가 단순 UPDATE 면 그 run 은 완주하고도 이력에서 사라진다.
chk("사전 조건 — RUN_B 행이 없다",
    q("select count(*) from run_records where run_id=%s", (RUN_B,))[0] == 0)
b = R._new_status(RUN_B, "흡연", R.MODE_HITL)
b.update(status="failed", finished_at=R._now_iso())
reason = RR.record_run_end(b, None)
chk("종료만 불러도 성공(None)", reason is None, str(reason))
rb = dict(zip(COLS, q("select %s from run_records where run_id=%%s"
                      % ", ".join(COLS), (RUN_B,)) or [None] * len(COLS)))
chk("행이 생겼다", rb["run_id"] == RUN_B)
chk("last_known_status = failed", rb["last_known_status"] == "failed")
chk("domain·mode 도 채워진다(INSERT 갈래)", (rb["domain"], rb["mode"]) == ("흡연", "hitl"))
chk("user_input 은 NULL (hitl 은 안건 문장이 없다)", rb["user_input"] is None)

# ══════════════════════════════════════════════════════════════════
print("\n--- 7) DB 가 죽었을 때 — 던지지 않고 **사유를 돌려준다**")
_real_connect = RR._connect


def _dead_connect():
    # 아무도 안 듣는 포트. 진짜 psycopg 예외를 낸다(가짜 예외를 던지면
    # "우리가 만든 예외만 잡는" 대조가 된다).
    return psycopg.connect(
        "postgresql://nobody:nobody@127.0.0.1:5433/nodb", connect_timeout=2)


RR._connect = _dead_connect
try:
    r_s = RR.record_run_start(doc, "x")
    r_e = RR.record_run_end(end_doc, "x")
    r_m = RR.record_runs_end([(end_doc, None), (b, None)])
finally:
    RR._connect = _real_connect
chk("record_run_start 가 사유를 돌려준다", isinstance(r_s, str) and r_s, str(r_s)[:60])
chk("record_run_end 가 사유를 돌려준다", isinstance(r_e, str) and r_e, str(r_e)[:60])
chk("record_runs_end 가 사유를 돌려준다", isinstance(r_m, str) and r_m, str(r_m)[:60])
# 🔴 `"Error" in 사유` 로 재면 **거짓 빨간불**이 난다 — psycopg 는 접속 지연을
#    `ConnectionTimeout` 으로 던지고 그 이름엔 `Error` 가 없다(OperationalError 하위지만
#    `type(ex).__name__` 은 하위 이름이다). 재야 할 건 이름의 철자가 아니라 **형식**
#    (`<예외종류>: <메시지>`)이다 — 그래야 사유만 보고 무슨 일이 났는지 갈린다.
_kind = (r_s or "").split(":")[0]
chk("사유가 `<예외종류>: <메시지>` 형식이다",
    ": " in (r_s or "") and _kind.isidentifier(), str(r_s)[:40])
chk("살아 있는 연결로 되돌아왔다", RR._connect is _real_connect)
chk("빈 목록이면 접속조차 안 한다", RR.record_runs_end([]) is None)

# ══════════════════════════════════════════════════════════════════
print("\n--- 8) 러너 배선 — 실패해도 run 은 안 죽고, 사유는 남는다")
tmp = Path(tempfile.mkdtemp(prefix="chk_run_records_"))
_real_root, _real_rr = R.RUNS_ROOT, R.run_records


class _Fake:
    """러너가 무엇을 어떻게 부르는지 센다. 진짜 DB 는 안 쓴다."""

    def __init__(self, reason=None):
        self.reason, self.calls, self.items = reason, 0, None

    def record_runs_end(self, items):
        self.calls += 1
        self.items = items
        return self.reason


try:
    R.RUNS_ROOT = tmp
    old = (datetime.now().replace(microsecond=0)
           .replace(year=2000).isoformat(timespec="seconds"))
    for rid in ("r_고아_1", "r_고아_2"):
        d = tmp / rid
        d.mkdir(parents=True)
        (d / "status.json").write_text(json.dumps(
            {"run_id": rid, "domain": "흡연", "mode": "full", "status": "running",
             "steps": [{"id": "2", "status": "running"}], "artifacts": {},
             "started_at": old, "finished_at": None}, ensure_ascii=False),
            encoding="utf-8")
        (d / "params.json").write_text(
            json.dumps({"user_input": f"{rid} 안건", "topn": 20}, ensure_ascii=False),
            encoding="utf-8")

    fake = _Fake(reason=None)
    R.run_records = fake
    R.reap_orphans()
    chk("고아 2개인데 DB 호출은 **1회**(연결 1개)", fake.calls == 1, f"calls={fake.calls}")
    chk("2건을 한 번에 넘긴다", fake.items is not None and len(fake.items) == 2)
    chk("params.json 의 user_input 을 같이 넘긴다",
        sorted(ui for _, ui in fake.items) == ["r_고아_1 안건", "r_고아_2 안건"],
        str([ui for _, ui in fake.items]))
    saved = json.loads((tmp / "r_고아_1" / "status.json").read_text(encoding="utf-8"))
    chk("status.json 은 failed 로 닫혔다", saved["status"] == "failed")
    chk("성공했으면 run_record_errors 키가 없다", "run_record_errors" not in saved)

    # 같은 상황에서 DB 만 죽은 경우
    for rid in ("r_고아_1", "r_고아_2"):
        p = tmp / rid / "status.json"
        s = json.loads(p.read_text(encoding="utf-8"))
        s.update(status="running", finished_at=None)
        p.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    fake2 = _Fake(reason="OperationalError: 연결 실패")
    R.run_records = fake2
    R.reap_orphans()
    saved = json.loads((tmp / "r_고아_2" / "status.json").read_text(encoding="utf-8"))
    chk("DB 가 죽어도 reap 은 끝까지 돈다", saved["status"] == "failed")
    chk("사유가 status.json 에 남는다", saved.get("run_record_errors"),
        str(saved.get("run_record_errors")))
    e = (saved.get("run_record_errors") or [{}])[0]
    chk("어디서 실패했는지 적는다", e.get("at") == "reap", str(e.get("at")))
    chk("사유 원문이 그대로 있다", "OperationalError" in (e.get("reason") or ""))

    print("\n--- 9) 옛 run 의 status.json (키가 없던 시절)")
    legacy = tmp / "r_옛날_1"
    legacy.mkdir()
    (legacy / "status.json").write_text(json.dumps(
        {"run_id": "r_옛날_1", "domain": "흡연", "mode": "fixture", "status": "succeeded",
         "steps": [], "artifacts": {}, "started_at": old, "finished_at": old},
        ensure_ascii=False), encoding="utf-8")
    got = R.read_status("r_옛날_1")
    chk("read_status 가 user_id 를 null 로 채운다",
        "user_id" in got and got["user_id"] is None)
    chk("`loaded` 도 그대로 null 로 채운다", got.get("loaded", "X") is None)
    chk("run_record_errors 는 안 만든다 (없음 = 실패 기록 없음)",
        "run_record_errors" not in got)

    d2 = {"run_id": "r_옛날_1"}
    R.note_run_record_error(d2, "start", "사유")
    R.note_run_record_error(d2, "end", "사유2")
    chk("note_run_record_error 는 쌓는다(덮지 않는다)",
        len(d2["run_record_errors"]) == 2, str(len(d2["run_record_errors"])))
    chk("각 항목에 at·time·reason 이 있다",
        set(d2["run_record_errors"][0]) == {"at", "time", "reason"})
finally:
    R.RUNS_ROOT, R.run_records = _real_root, _real_rr
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════════════
print("\n--- 10) 정리 — 넣은 것을 지우고, 지워졌는지까지 본다")
with psycopg.connect(settings.DATABASE_URL,
                     connect_timeout=DB_CONNECT_TIMEOUT) as c, c.cursor() as cur:
    cur.execute("delete from run_records where run_id = any(%s)", (list(RUNS),))
    deleted = cur.rowcount
    c.commit()
chk("2행 삭제됨", deleted == 2, f"deleted={deleted}")
chk("정말 없다", q("select count(*) from run_records where run_id = any(%s)",
                   (list(RUNS),))[0] == 0)
after_total = q("select count(*) from run_records")[0]
chk("남의 행은 안 건드렸다", after_total == before_total,
    f"{before_total} → {after_total}")

print(f"\n결과: {ok} OK / {fail} 실패  (총 {ok + fail})")
raise SystemExit(1 if fail else 0)
