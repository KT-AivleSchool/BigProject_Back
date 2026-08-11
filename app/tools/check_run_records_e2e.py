# -*- coding: utf-8 -*-
"""
run 메타데이터 **관통** 대조 — 진짜 run 을 돌려 `run_records` 에 쌓이는지 본다
=====================================================================
  python app/tools/check_run_records_e2e.py [도메인]        # 기본 흡연

`check_run_records.py`(64항목)와 **다른 질문**을 잰다. 그쪽은 `run_records` 의
세 함수를 **직접** 불러 넣고 지운다 — 「함수가 옳게 쓰나」의 답이다. 러너 배선 절은
`run_records` 모듈을 **가짜로 갈아끼워** 호출 방식(1회 호출·일괄·`user_input` 동반)만
본다. 목은 SQL 을 안 치므로 **「DB 에 무엇이 남나」는 못 본다.**

그래서 배선이 통째로 빠져도 저쪽은 64/64 다. 여기서 그 위 칸을 잰다 —
**API·러너로 실제 run 을 돌렸을 때 행이 생기고, 끝나면서 사실과 맞게 갱신되는가.**

세 절이 각각 다른 것을 본다:
  【1】 fixture 관통 (TestClient · 약 85초 · LLM 0회)
        발급 시점에 `queued` 행이 **먼저** 생기는가 → 끝나면 `succeeded`+`loaded_*`
  【2】 hitl 게이트 대기 (약 95초 · 🔴 LLM 1회 — 게이트B 제안 패스)
        🔴 여기가 이 도구의 핵심이다. 설계는 `running`·`awaiting_hitl` 을 DB 에
        **일부러 안 적는다**(진행률을 DB 에 물으면 정본이 둘이 되고 언젠가 갈린다).
        그 주장은 fixture 로는 못 잰다 — 몇 초에 지나가는 상태라 「안 적었다」와
        「못 봤다」가 구분되지 않는다. hitl 은 게이트에서 **멈춰 있으므로** 붙잡고 잰다.
  【3】 고아 정리 → 진짜 DB (1초 미만)
        `failed` 로 끝난 run 도 남는가. 그리고 **발급 INSERT 가 없었던 run** 이
        UPSERT 로 행을 얻는가 — 「행이 없는 상태가 정상 경로」의 실증이다.

🔴 uvicorn 을 재시작하지 않는다. 【1】은 in-process TestClient, 【2】는 러너 직접,
   【3】은 `RUNS_ROOT` 를 임시 폴더로 바꿔 부른다 — **진짜 `runs/` 는 스캔조차 안 된다**
   (그냥 부르면 `reap_orphans` 가 남이 돌리는 run 을 `failed` 로 닫는다).

⚠ 남는 것 — run 2개(`runs/` 폴더 + `run_records` 2행 + 그 run_id 의 `audit_rules` 13 ·
  `booth_candidates` 20). **지우지 않는다**: 격리된 run 이라 남의 것을 안 밟고,
  프런트가 `/candidates?run_id=` 로 확인할 실물이 된다. 【3】의 가짜 행만 지운다.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# 이 스크립트는 `app/tools/` 안에 있다 — 저장소 루트는 두 단계 위다.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import psycopg  # noqa: E402

from app.config import DB_CONNECT_TIMEOUT, DOMAIN_ROOT, settings  # noqa: E402
from app.services import pipeline_runner as R  # noqa: E402

ROOT = Path(_ROOT)
DOMAIN = sys.argv[1] if len(sys.argv) > 1 else "흡연"
REAP_RID = "r_probe_reap_1"
OK = FAIL = 0


def chk(label: str, cond: bool, got: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK]   {label}" + (f"  ({got})" if got else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  → {got}")


def q(sql, params=(), one=True):
    with psycopg.connect(
        settings.DATABASE_URL, connect_timeout=DB_CONNECT_TIMEOUT
    ) as c, c.cursor() as cur:
        cur.execute(sql, params)
        # DELETE·UPDATE 는 결과집합이 없다 — fetchall 이 ProgrammingError 로 터진다.
        rows = cur.fetchall() if cur.description is not None else []
    return (rows[0] if rows else None) if one else rows


_COLS = (
    "last_known_status user_id domain mode user_input "
    "started_at finished_at loaded_audit_rules loaded_booth_candidates"
).split()


def row(run_id: str) -> dict | None:
    r = q(
        f"SELECT {', '.join(_COLS)} FROM run_records WHERE run_id = %s", (run_id,)
    )
    return dict(zip(_COLS, r)) if r else None


def disk_status(run_id: str) -> dict:
    return json.loads(
        (ROOT / "runs" / run_id / "status.json").read_text(encoding="utf-8")
    )


def wait_runner(run_id: str, want: tuple[str, ...], timeout: int = 900) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = R.read_status(run_id)
        if d["status"] in want:
            return d
        time.sleep(2)
    raise TimeoutError(f"{run_id}: {R.read_status(run_id)['status']}")


print(f"■ run 메타데이터 관통 대조 — 도메인 {DOMAIN}")
n_before = q("SELECT count(*) FROM run_records")[0]
print(f"  시작 시점 run_records: {n_before}행")

# ══════════════════════════════════════════════════════════════════════
print("\n--- 1) fixture 관통 — 발급 시점에 이미 행이 있는가 (TestClient)")
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

with TestClient(app) as client:
    res = client.post(
        "/api/v1/pipeline/runs", json={"domain": DOMAIN, "mode": "fixture"}
    )
    if res.status_code not in (200, 201, 202):
        sys.exit(f"🔴 POST /pipeline/runs → {res.status_code}: {res.text[:300]}")
    rid1 = res.json()["run_id"]
    print(f"  run_id = {rid1}")

    r0 = row(rid1)
    chk("발급 직후 행이 있다 (끝나기 전이다)", r0 is not None)
    if r0:
        chk("`last_known_status` 가 queued 다", r0["last_known_status"] == "queued",
            str(r0["last_known_status"]))
        chk("`finished_at` 은 아직 비어 있다", r0["finished_at"] is None)
        chk("`user_id` 는 NULL — 익명 실행이 정상 상태다", r0["user_id"] is None)
        chk("`domain`·`mode` 가 실렸다",
            r0["domain"] == DOMAIN and r0["mode"] == "fixture",
            f"{r0['domain']}/{r0['mode']}")
        chk("`loaded_*` 는 아직 비어 있다",
            r0["loaded_audit_rules"] is None
            and r0["loaded_booth_candidates"] is None)

    t0 = time.time()
    last = None
    doc = {}
    while time.time() - t0 < 900:
        doc = client.get(f"/api/v1/pipeline/runs/{rid1}").json()
        if doc.get("status") != last:
            last = doc.get("status")
            print(f"    {time.time() - t0:6.1f}s  {last}")
        if last in ("succeeded", "failed"):
            break
        time.sleep(3)

chk("fixture 가 완주했다", doc.get("status") == "succeeded", str(doc.get("status")))
d1, r1 = disk_status(rid1), row(rid1)
loaded = d1.get("loaded") or {}
chk("`last_known_status` == status.json 의 status",
    (r1 or {}).get("last_known_status") == d1.get("status"),
    f"{(r1 or {}).get('last_known_status')} ↔ {d1.get('status')}")
chk("`finished_at` 이 채워졌다", (r1 or {}).get("finished_at") is not None)
chk("`loaded_audit_rules` == status.json",
    (r1 or {}).get("loaded_audit_rules") == loaded.get("audit_rules"),
    f"{(r1 or {}).get('loaded_audit_rules')} ↔ {loaded.get('audit_rules')}")
chk("`loaded_booth_candidates` == status.json",
    (r1 or {}).get("loaded_booth_candidates") == loaded.get("booth_candidates"),
    f"{(r1 or {}).get('loaded_booth_candidates')} ↔ {loaded.get('booth_candidates')}")
chk("발급 시점 값이 종료 UPSERT 에 안 덮였다 (domain·mode)",
    (r1 or {}).get("domain") == DOMAIN and (r1 or {}).get("mode") == "fixture")
chk("성공이면 `run_record_errors` 키 자체가 없다",
    "run_record_errors" not in d1, str(d1.get("run_record_errors"))[:120])

# 🔴 시각 — status.json 은 naive 로컬, 컬럼은 TIMESTAMPTZ, 세션 TZ 는 Etc/UTC 다.
#    그냥 넣으면 9시간 밀리는데 **값이 그럴듯해서** 안 걸린다.
print("\n  · 시각 — naive 로컬이 TIMESTAMPTZ 에서 밀리지 않는가")
for key in ("started_at", "finished_at"):
    want = datetime.fromisoformat(d1[key]).astimezone()
    got = (r1 or {}).get(key)
    delta = abs((got - want).total_seconds()) if got else 1e9
    chk(f"`{key}` 이 status.json 과 같은 순간이다", delta < 2,
        f"{got} ↔ {want.isoformat()} (차 {delta:.0f}초)")

# 산출물 적재 — 기록한 수가 실제 행 수와 같은가(러너가 DB 를 다시 세지 않는다는 확인)
n_audit = q("SELECT count(*) FROM audit_rules WHERE run_id = %s", (rid1,))[0]
n_booth = q("SELECT count(*) FROM booth_candidates WHERE run_id = %s", (rid1,))[0]
chk("`audit_rules` 실제 행 수 == 기록",
    n_audit == (r1 or {}).get("loaded_audit_rules"),
    f"DB {n_audit} ↔ 기록 {(r1 or {}).get('loaded_audit_rules')}")
chk("`booth_candidates` 실제 행 수 == 기록",
    n_booth == (r1 or {}).get("loaded_booth_candidates"),
    f"DB {n_booth} ↔ 기록 {(r1 or {}).get('loaded_booth_candidates')}")

# ══════════════════════════════════════════════════════════════════════
print("\n--- 2) hitl 게이트 대기 — 멈춰 있는 동안 DB 는 뭐라고 하는가")
rid2 = R.start_run(DOMAIN, R.MODE_HITL)
print(f"  run_id = {rid2}")

d = wait_runner(rid2, ("awaiting_hitl", "failed", "succeeded"))
print(f"  게이트A — status.json = {d['status']} / {(d.get('gate') or {}).get('id')}")
chk("게이트A 에서 멈췄다",
    d["status"] == "awaiting_hitl" and d["gate"]["id"] == "audit", str(d["status"]))
r = row(rid2)
chk("행은 있다", r is not None)
chk("🔴 DB 는 여전히 `queued` — `awaiting_hitl` 을 안 적는다",
    (r or {}).get("last_known_status") == "queued",
    str((r or {}).get("last_known_status")))
chk("대기 중엔 `finished_at` 이 비어 있다", (r or {}).get("finished_at") is None)
chk("대기 중엔 `loaded_*` 도 비어 있다",
    (r or {}).get("loaded_audit_rules") is None
    and (r or {}).get("loaded_booth_candidates") is None)
started_a = (r or {}).get("started_at")

# hitl 은 배제 확정을 전부 제안값으로 되돌린다(사람이 전부 본다) → 답이 필요하다.
ed = [qq for qq in d["gate"]["questions"] if qq["editable"]]
other = [(qq["kind"], qq["dataset_id"]) for qq in ed if qq["kind"] != "exclusion"]
if other:
    sys.exit(f"🔴 배제 말고 편집 가능한 질문이 있다 — 답을 지어낼 수 없다: {other}")
R.submit_gate(rid2, "audit", {
    "run_id": rid2,
    "exclusions": [
        {
            "dataset_id": qq["dataset_id"],
            "role_index": qq["role_index"],
            "radius_m": qq["proposed_m"] if qq["proposed_m"] is not None
            else qq["radius_m"],
        }
        for qq in ed
    ],
})

d = wait_runner(rid2, ("awaiting_hitl", "failed", "succeeded"))
print(f"  게이트B — status.json = {d['status']} / {(d.get('gate') or {}).get('id')}")
chk("게이트B 에서 멈췄다",
    d["status"] == "awaiting_hitl" and d["gate"]["id"] == "weight", str(d["status"]))
r = row(rid2)
chk("게이트를 하나 지나도 DB 는 `queued` 그대로",
    (r or {}).get("last_known_status") == "queued",
    str((r or {}).get("last_known_status")))
chk("`started_at` 이 발급 시점 값 그대로다 (게이트가 안 덮는다)",
    (r or {}).get("started_at") == started_a, str((r or {}).get("started_at")))

qw = d["gate"]["questions"]
fix = json.loads(
    (Path(DOMAIN_ROOT) / f"{DOMAIN}_FIX" / "기준값.json").read_text(encoding="utf-8")
)
R.submit_gate(rid2, "weight", {
    "run_id": rid2,
    "radius": {
        k: v["radius_m"]
        for k, v in fix["STEP3_가중치"].items()
        if v.get("radius_m") is not None
    },
    "slider": {qq["indicator_id"]: qq["slider_proposed"] for qq in qw},
})

d = wait_runner(rid2, ("succeeded", "failed"))
print(f"  완주 — status.json = {d['status']}")
d2, r = disk_status(rid2), row(rid2)
loaded2 = d2.get("loaded") or {}
chk("hitl 이 완주했다", d2.get("status") == "succeeded", str(d2.get("status")))
chk("`last_known_status` == status.json",
    (r or {}).get("last_known_status") == d2.get("status"),
    f"{(r or {}).get('last_known_status')} ↔ {d2.get('status')}")
chk("`finished_at` 이 채워졌다", (r or {}).get("finished_at") is not None)
chk("`loaded_*` 둘 다 status.json 과 같다",
    (r or {}).get("loaded_audit_rules") == loaded2.get("audit_rules")
    and (r or {}).get("loaded_booth_candidates") == loaded2.get("booth_candidates"),
    f"{(r or {}).get('loaded_audit_rules')}/{(r or {}).get('loaded_booth_candidates')}"
    f" ↔ {loaded2.get('audit_rules')}/{loaded2.get('booth_candidates')}")
chk("`started_at` 은 끝나면서도 안 덮였다",
    (r or {}).get("started_at") == started_a)
chk("`run_record_errors` 키가 없다", "run_record_errors" not in d2,
    str(d2.get("run_record_errors"))[:120])

# ══════════════════════════════════════════════════════════════════════
print("\n--- 3) 고아 정리 → 진짜 DB (§8 은 목이라 여기를 못 본다)")
# 🔴 부르기 전에 활성 run 을 직접 센다. 임시 RUNS_ROOT 로 바꿀 것이지만,
#    바꾸기 전 상태를 남겨 두면 사고가 났을 때 무엇이 밟혔는지 알 수 있다.
active = [
    p.parent.name
    for p in (ROOT / "runs").glob("*/status.json")
    if json.loads(p.read_text(encoding="utf-8")).get("status")
    in ("running", "queued", "awaiting_hitl")
]
print(f"  진짜 runs/ 의 활성 run: {len(active)}개 {active}")

n_mid = q("SELECT count(*) FROM run_records")[0]
tmp = Path(tempfile.mkdtemp(prefix="chk_run_records_e2e_"))
real_root = R.RUNS_ROOT
try:
    R.RUNS_ROOT = tmp
    old = datetime.now().replace(microsecond=0, year=2000).isoformat(
        timespec="seconds"
    )
    dd = tmp / REAP_RID
    dd.mkdir(parents=True)
    (dd / "status.json").write_text(json.dumps({
        "run_id": REAP_RID, "domain": DOMAIN, "mode": "full", "status": "running",
        "steps": [{"id": "2", "status": "running"}], "artifacts": {},
        "started_at": old, "finished_at": None,
    }, ensure_ascii=False), encoding="utf-8")
    (dd / "params.json").write_text(
        json.dumps({"user_input": "고아 실측 안건", "topn": 20}, ensure_ascii=False),
        encoding="utf-8")

    R.reap_orphans()  # 목 없이 — 진짜 run_records 로 간다

    saved = json.loads((dd / "status.json").read_text(encoding="utf-8"))
    chk("status.json 이 failed 로 닫혔다", saved["status"] == "failed", saved["status"])
    chk("DB 기록이 성공해 `run_record_errors` 키가 없다",
        "run_record_errors" not in saved, str(saved.get("run_record_errors"))[:120])

    rr = q(
        "SELECT last_known_status, user_input, started_at, finished_at, mode, domain"
        " FROM run_records WHERE run_id = %s", (REAP_RID,))
    chk("🔴 발급 INSERT 가 없었는데도 행이 생겼다 (UPSERT)", rr is not None)
    if rr:
        st, ui, sa, fa, mode, dom = rr
        chk("`last_known_status` = failed — 실패도 이력에 남는다", st == "failed", str(st))
        chk("`user_input` 을 params.json 에서 가져왔다", ui == "고아 실측 안건", str(ui))
        chk("`started_at` 은 그 run 의 시작 시각이다 (지금이 아니다)",
            sa.year == 2000, sa.isoformat())
        chk("`finished_at` 이 채워졌다", fa is not None, str(fa))
        chk("`mode`·`domain` 도 실렸다", mode == "full" and dom == DOMAIN,
            f"{mode}/{dom}")
    chk("이 run 말고 늘어난 행은 없다",
        q("SELECT count(*) FROM run_records")[0] == n_mid + 1)
finally:
    R.RUNS_ROOT = real_root
    shutil.rmtree(tmp, ignore_errors=True)

# ── 정리 — 가짜 고아 행만 지운다 (【1】【2】의 진짜 run 은 남긴다) ──────
with psycopg.connect(
    settings.DATABASE_URL, connect_timeout=DB_CONNECT_TIMEOUT
) as c, c.cursor() as cur:
    cur.execute("DELETE FROM run_records WHERE run_id = %s", (REAP_RID,))
    c.commit()
chk("가짜 고아 행을 지웠다",
    q("SELECT count(*) FROM run_records WHERE run_id = %s", (REAP_RID,))[0] == 0)
chk("남은 증가분은 이 대조가 만든 진짜 run 2개뿐이다",
    q("SELECT count(*) FROM run_records")[0] == n_before + 2,
    f"{n_before} → {q('SELECT count(*) FROM run_records')[0]}")
chk("RUNS_ROOT 를 되돌렸다", R.RUNS_ROOT == real_root, str(R.RUNS_ROOT))

print(f"\n■ {OK} OK · {FAIL} FAIL")
print(f"■ 남긴 것: run_records 2행({rid1} · {rid2}) + 그 run 의 audit_rules·"
      f"booth_candidates. 프런트가 `/candidates?run_id=` 로 확인할 실물이다")
sys.exit(1 if FAIL else 0)
