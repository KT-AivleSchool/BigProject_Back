# -*- coding: utf-8 -*-
"""실행 취소 `DELETE /pipeline/runs/{run_id}` 대조 (계약 3-4) — LLM 0회 · DB 0회

    python app/tools/check_cancel_run.py

🔴 **묻는 것은 「status.json 이 failed 로 바뀌었나」가 아니다.**
   그건 원래 PR 도 했다. 여기서 확인하는 건 **자식 프로세스가 실제로 죽었는가**다 —
   안 죽이면 화면은 「멈췄다」고 말하는데 파이프라인은 계속 돌고, 실행 스레드가
   다음 단계 전이에서 `_write_status` 로 그 「멈췄다」를 **덮어쓴다**(원칙 4).
   그래서 §3 은 종료 코드가 아니라 **`Popen` 핸들의 생사**를 직접 본다.

🔴 진짜 파이프라인을 돌리지 않는다(정본 산출물 디렉터리가 덮인다 · 수십 초).
   `_proc_of` 를 목으로 갈아끼워 **오래 자는 진짜 자식**을 띄운다 — 죽이는 대상이
   진짜 OS 프로세스여야 이 대조가 의미가 있다. 목 프로세스로 바꾸면 `terminate()`
   가 정말 먹는지를 못 본다.

🔴 `RUNS_ROOT` 를 임시 폴더로 갈아끼운다. 안 바꾸면 **남이 돌리는 run 폴더**에
   쓰게 되고, `_ACTIVE` 는 프로세스 전역이라 진짜 도메인 이름을 쓰면 살아 있는
   서버와 같은 자물쇠를 건드린다 → 도메인도 `_대조용` 으로 짓는다.
   (이 프로세스는 uvicorn 이 아니므로 `_ACTIVE` 는 실제로는 따로지만, 습관을 유지한다.)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.services import pipeline_runner as R  # noqa: E402

DOMAIN = "취소_대조용"

# 오래 자는 자식. `-u` 로 버퍼링을 끄고 한 줄 찍어 **정말 떴는지**를 로그로 남긴다.
_CHILD_SRC = "import time,sys; print('CHILD-UP', flush=True); time.sleep(300)"

_ok = _fail = 0


def chk(label: str, cond: bool, got=None) -> bool:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  ✅ {label}")
    else:
        _fail += 1
        print(f"  ❌ {label}")
        if got is not None:
            print(f"       실측 {got!r}")
    return bool(cond)


def err(label: str, fn, exc, needle: str | None = None) -> None:
    """`fn()` 이 `exc` 로 터지는지. 안 터지면 실패(조용한 성공이 제일 나쁘다)."""
    try:
        got = fn()
    except exc as e:
        if needle and needle not in str(e):
            chk(label, False, f"사유가 다르다: {e}")
        else:
            chk(label, True)
        return
    except Exception as e:  # noqa: BLE001
        chk(label, False, f"다른 예외: {type(e).__name__}: {e}")
        return
    chk(label, False, f"안 터졌다 → {got!r}")


# ── 목 ────────────────────────────────────────────────────────────────
def _install_mocks(tmp: Path) -> None:
    R.RUNS_ROOT = tmp
    R._PLAN = {**R._PLAN, R.MODE_FIXTURE: ("2",)}
    R._load_conditions = lambda *a, **k: {}
    R._read_params = lambda rid: {}
    R._stage_args = lambda *a, **k: (None, None)
    R._proc_of = lambda stage, domain, base, *a, **k: R._Proc(
        ("2",), [sys.executable, "-u", "-c", _CHILD_SRC])
    # DB 사본(계층 ③)은 이 대조의 대상이 아니다. 실 DB 에 행을 남기지 않는다.
    R.run_records.record_run_start = lambda doc, ui=None: None
    R.run_records.record_run_end = lambda doc, ui=None: None


def _make_run(run_id: str, status: str = "queued") -> dict:
    d = R.run_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    doc = R._new_status(run_id, DOMAIN, R.MODE_FIXTURE)
    doc["status"] = status
    R._write_status(run_id, doc)
    return doc


def _wait(pred, timeout=15.0, step=0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return False


# ── §1 없는 run · 이미 끝난 run ────────────────────────────────────────
def sec1() -> None:
    print("\n[1] 취소할 수 없는 것을 취소했다고 하지 않는다")
    err("없는 run_id → KeyError(404)", lambda: R.cancel_run("r_없음_999"), KeyError)

    _make_run("r_done", "succeeded")
    err("완주한 run → RunConflict(409)", lambda: R.cancel_run("r_done"),
        R.RunConflict, "이미 끝난 run")
    chk("완주 run 의 status 는 안 바뀐다",
        R.read_status("r_done")["status"] == "succeeded",
        R.read_status("r_done")["status"])
    chk("완주 run 에 취소 사유가 안 적힌다",
        R.read_status("r_done").get("error") is None,
        R.read_status("r_done").get("error"))

    _make_run("r_failed", "failed")
    err("이미 실패한 run → RunConflict(409)", lambda: R.cancel_run("r_failed"),
        R.RunConflict, "이미 끝난 run")


# ── §2 진짜 자식을 죽인다 ──────────────────────────────────────────────
def sec2() -> None:
    print("\n[2] 자식 프로세스를 **실제로** 죽인다")
    run_id = "r_kill"
    _make_run(run_id)
    with R._LOCK:
        R._ACTIVE[DOMAIN] = run_id
    R._spawn(run_id, DOMAIN, R.MODE_FIXTURE, 0)

    got_child = _wait(lambda: R._CHILDREN.get(run_id) is not None)
    chk("자식이 장부(_CHILDREN)에 등록된다", got_child, list(R._CHILDREN))
    child = R._CHILDREN.get(run_id)
    if child is None:
        chk("자식 없이 진행 불가 — 이하 항목 생략", False)
        return
    pid = child.pid
    # 자식이 정말 살아서 돌고 있는지부터 본다. 죽어 있는 것을 죽였다고 말하면
    # 이 대조 전체가 가짜 초록불이 된다.
    chk("취소 전 자식이 살아 있다", child.poll() is None, child.poll())
    up = _wait(lambda: "CHILD-UP" in (R.run_dir(run_id) / "run.log").read_text(
        encoding="utf-8", errors="replace"))
    chk("자식이 실제로 실행됐다 (run.log 에 자기 출력)", up)

    t0 = time.monotonic()
    doc = R.cancel_run(run_id)
    took = time.monotonic() - t0

    chk("자식이 죽었다 (poll() 이 종료 코드를 준다)", child.poll() is not None,
        child.poll())
    chk(f"취소가 빨리 끝난다 (실측 {took:.2f}s < 유예 {R._TERM_GRACE_S}s)",
        took < R._TERM_GRACE_S, round(took, 2))
    chk("PID 가 OS 에도 없다", not _pid_alive(pid), pid)

    chk("status=failed", doc["status"] == "failed", doc["status"])
    chk("error 가 취소 사유", doc.get("error") == R._CANCEL_MSG, doc.get("error"))
    chk("finished_at 이 찍힌다", bool(doc.get("finished_at")), doc.get("finished_at"))
    chk("gate 키 없음(계약 7-3)", "gate" not in doc, list(doc))
    st2 = next((s for s in doc["steps"] if s["id"] == "2"), None)
    # 🔴 done 이면 산출물이 다 나온 것처럼 읽힌다. 끊긴 칸은 failed 다.
    chk("끊긴 칸이 failed (done 아님)", st2 and st2["status"] == "failed", st2)
    chk("디스크에도 같은 값이 적혔다",
        R.read_status(run_id)["status"] == "failed"
        and R.read_status(run_id)["error"] == R._CANCEL_MSG,
        R.read_status(run_id).get("error"))

    chk("_ACTIVE 반납 (같은 도메인 재실행 가능)", DOMAIN not in R._ACTIVE,
        dict(R._ACTIVE))
    chk("_CHILDREN 정리", run_id not in R._CHILDREN, list(R._CHILDREN))
    chk("_WORKERS 정리", run_id not in R._WORKERS, list(R._WORKERS))
    chk("_CANCELLED 정리", run_id not in R._CANCELLED, list(R._CANCELLED))

    # 🔴 스레드가 죽고 나서 status 를 **다시 덮어쓰지 않는가.** 원래 PR 이 실패한
    #    자리가 정확히 여기다 — 밖에서 쓴 상태가 다음 전이에 조용히 덮였다.
    time.sleep(0.5)
    chk("잠시 뒤에도 failed 그대로 (스레드가 덮어쓰지 않는다)",
        R.read_status(run_id)["status"] == "failed",
        R.read_status(run_id)["status"])

    err("이미 취소된 run 을 또 취소 → 409", lambda: R.cancel_run(run_id),
        R.RunConflict, "이미 끝난 run")


def _pid_alive(pid: int) -> bool:
    """OS 에 그 PID 가 남아 있는가. 자식을 이미 거둬갔으므로 좀비도 안 남는다."""
    if os.name == "nt":
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True, errors="replace")
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ── §3 게이트 대기 중 취소 (스레드가 없다) ─────────────────────────────
def sec3() -> None:
    print("\n[3] 게이트 대기(awaiting_hitl) 취소 — 스레드가 없어도 닫힌다")
    run_id = "r_gate"
    doc = _make_run(run_id, "awaiting_hitl")
    doc["gate"] = {"id": "audit", "questions": []}
    doc["steps"][0]["status"] = "running"
    R._write_status(run_id, doc)
    with R._LOCK:
        R._ACTIVE[DOMAIN] = run_id

    # 🔴 이게 없으면 그 도메인은 **서버 재시작 전까지 409** 다 — 게이트에서 멈춘
    #    run 은 스레드가 이미 끝나서 아무도 뒷정리를 안 한다.
    got = R.cancel_run(run_id)
    chk("status=failed", got["status"] == "failed", got["status"])
    chk("error 가 취소 사유", got.get("error") == R._CANCEL_MSG, got.get("error"))
    chk("gate 키가 사라진다(계약 7-3)", "gate" not in got, list(got))
    chk("running 이던 칸이 failed",
        got["steps"][0]["status"] == "failed", got["steps"][0])
    chk("_ACTIVE 반납", DOMAIN not in R._ACTIVE, dict(R._ACTIVE))


# ── §4 완주와 겹친 취소는 위조하지 않는다 ──────────────────────────────
def sec4() -> None:
    print("\n[4] 완주와 겹친 취소 — 끝난 run 을 취소로 덮어쓰지 않는다")
    run_id = "r_race"
    _make_run(run_id, "running")

    # `cancel_run` 이 상태를 본 **뒤**, `_close_cancelled` 가 다시 읽기 **전**에
    # 스레드가 완주로 닫는 상황. read_status 를 한 번만 가로채 재현한다.
    real_read = R.read_status
    calls = {"n": 0}

    def racy(rid: str):
        d = real_read(rid)
        calls["n"] += 1
        if rid == run_id and calls["n"] == 1:
            # 첫 조회(cancel_run) 뒤에 완주가 확정된다
            d2 = real_read(rid)
            d2["status"] = "succeeded"
            d2["finished_at"] = R._now_iso()
            R._write_status(rid, d2)
        return d

    R.read_status = racy
    try:
        err("완주가 끼어들면 409", lambda: R.cancel_run(run_id),
            R.RunConflict, "사이에 run 이 끝났")
    finally:
        R.read_status = real_read

    doc = R.read_status(run_id)
    chk("succeeded 가 그대로 남는다", doc["status"] == "succeeded", doc["status"])
    chk("취소 사유가 안 적힌다", doc.get("error") is None, doc.get("error"))
    chk("_CANCELLED 정리 (다음 요청이 유령 취소를 안 본다)",
        run_id not in R._CANCELLED, list(R._CANCELLED))


# ── §5 HTTP 표면 ──────────────────────────────────────────────────────
def sec5() -> None:
    print("\n[5] HTTP — DELETE /api/v1/pipeline/runs/{run_id}")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1 import pipeline as ep

    app = FastAPI()
    app.include_router(ep.router, prefix="/api/v1/pipeline")
    c = TestClient(app)

    r = c.delete("/api/v1/pipeline/runs/r_없음_999")
    chk("없는 run → 404", r.status_code == 404, r.status_code)
    chk("404 에 사유가 있다", "없는 run_id" in r.text, r.text[:120])

    _make_run("r_http_done", "succeeded")
    r = c.delete("/api/v1/pipeline/runs/r_http_done")
    chk("완주한 run → 409", r.status_code == 409, r.status_code)

    run_id = "r_http"
    _make_run(run_id)
    with R._LOCK:
        R._ACTIVE[DOMAIN] = run_id
    R._spawn(run_id, DOMAIN, R.MODE_FIXTURE, 0)
    _wait(lambda: R._CHILDREN.get(run_id) is not None)
    child = R._CHILDREN.get(run_id)

    r = c.delete(f"/api/v1/pipeline/runs/{run_id}")
    chk("살아 있는 run → 204", r.status_code == 204, r.status_code)
    chk("204 는 본문이 없다", r.content == b"", r.content[:80])
    chk("자식이 죽었다", child is not None and child.poll() is not None,
        child.poll() if child else None)
    chk("status=failed", R.read_status(run_id)["status"] == "failed",
        R.read_status(run_id)["status"])

    # 경로조작은 여전히 막힌다 — run_id 가 경로로 쓰이는 자리다.
    for bad in ("..", "..%2F..%2Fetc", "%2e%2e%2fCLAUDE.md"):
        r = c.delete(f"/api/v1/pipeline/runs/{bad}")
        chk(f"경로조작 {bad!r} 은 취소되지 않는다", r.status_code in (404, 400, 405),
            r.status_code)


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="cancel_chk_") as td:
        tmp = Path(td) / "runs"
        tmp.mkdir(parents=True)
        _install_mocks(tmp)
        print("=" * 78)
        print(f"[취소 대조] RUNS_ROOT={tmp}")
        print("=" * 78)
        sec1()
        sec2()
        sec3()
        sec4()
        sec5()

        # 남은 자식이 있으면 임시 폴더 삭제가 막히고, 무엇보다 **대조기가 죽인다고
        # 말한 것을 안 죽인 채로 끝났다**는 뜻이다.
        leftover = [(rid, ch.pid) for rid, ch in list(R._CHILDREN.items())
                    if ch.poll() is None]
        print()
        chk("끝났을 때 살아 있는 자식이 없다", not leftover, leftover)
        for t in list(R._WORKERS.values()):
            t.join(2.0)

    print("-" * 78)
    print(f"  대조 {_ok + _fail}항목  ·  통과 {_ok}  ·  실패 {_fail}")
    print("=" * 78)
    if _fail:
        return 1
    print("  🔴 in-process 다. 「살아 있는 uvicorn 이 이 코드다」는 증명하지 않는다 —")
    print("     재시작 뒤 실제 포트로 한 번 더 칠 것(CLAUDE.md 「27/27 은 배포됐다가 아니다」).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
