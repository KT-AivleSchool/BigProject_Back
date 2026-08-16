# -*- coding: utf-8 -*-
"""업로드 도메인 정리(`datasets/user_input/<도메인>`) 대조 — **무엇이 삭제를 막는가**.

    python app\\tools\\check_user_input_pruner.py

🔴 진짜 `runs/` 도 진짜 `datasets/user_input/` 도 **안 쓴다.** 둘 다 임시 폴더로
   갈아끼운다. 확인하려는 건 「어떤 run 이 어떤 폴더를 보호하는가」이지 이 컴퓨터에
   뭐가 쌓여 있는지가 아니다. (`check_upload_api.py` 가 뒷정리 루트를 `DOMAIN_ROOT`
   로 잘못 잡아 판정이 틀렸던 전례가 있다 — 여기선 루트를 아예 옮긴다.)

🔴 이 대조기가 없어서 못 잡은 결함이 있다(2026-08-16). `_run_facts` 가 run 을
   **도메인 이름만으로** 골라, `datasets/user_input/재활용` 을 읽지도 않는 프리셋
   `hitl` run 하나(`r_20260814_008`)가 그 폴더의 삭제를 막았다. 하필 그 run 이
   `awaiting_hitl` 이라 스레드가 없어 `reap_orphans` 도 못 닫는다 → **영구히** 409 이고
   자동 정리기는 영원히 `keep` 이다. 예외가 안 나고 **지워지지 않을 뿐**이다.

그래서 여기서 제일 중요한 항목은 삭제가 아니라 **「보호가 성립하는 범위」**다:
보호가 넓으면 폴더가 영원히 살고, 좁으면 돌고 있는 run 의 입력이 발밑에서 사라진다.
"""
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services import pipeline_runner as R  # noqa: E402
from app.services import user_input_pruner as U  # noqa: E402

ok = fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label} {extra}")
    else:
        fail += 1
        print(f"  [!!] {label} {extra}")


TMP = Path(tempfile.mkdtemp(prefix="omnisite_uinput_"))
RUNS = TMP / "runs"
UIN = TMP / "user_input"
_REAL_RUNS = R.RUNS_ROOT
_REAL_UIN = U.USER_INPUT_ROOT

DOM = "재활용"
NOW = datetime(2026, 8, 16, 12, 0, 0)


def put_run(rid: str, *, domain: str, mode: str | None, status: str,
            finished: datetime | None = None, broken: bool = False) -> None:
    d = RUNS / rid
    d.mkdir(parents=True, exist_ok=True)
    p = d / "status.json"
    if broken:
        p.write_text("{ 이건 JSON 이 아니다", encoding="utf-8")
        return
    doc = {"run_id": rid, "domain": domain, "status": status,
           "started_at": (finished or NOW).isoformat(timespec="seconds")}
    # 🔴 `mode=None` 은 **키를 안 넣는다**(옛 run 의 모양). `"mode": null` 과 다르다.
    if mode is not None:
        doc["mode"] = mode
    if finished is not None:
        doc["finished_at"] = finished.isoformat(timespec="seconds")
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def reset(*, upload_age_h: float = 0.0) -> None:
    """임시 runs/ 와 임시 user_input/ 을 처음부터 다시 깐다."""
    shutil.rmtree(RUNS, ignore_errors=True)
    shutil.rmtree(UIN, ignore_errors=True)
    (RUNS).mkdir(parents=True)
    f = UIN / DOM / "data"
    f.mkdir(parents=True)
    p = f / "01.csv"
    p.write_bytes(b"x" * 100)
    import os
    t = (NOW - timedelta(hours=upload_age_h)).timestamp()
    os.utime(p, (t, t))


def facts():
    return U._run_facts(DOM)


def plan_of(dom: str = DOM, ttl: int = 24):
    for it in U.plan(ttl, now=NOW):
        if it["domain"] == dom:
            return it
    return None


try:
    R.RUNS_ROOT = RUNS
    U.USER_INPUT_ROOT = UIN
    print(f"임시 루트: {TMP}")

    # ── §1 프리셋 run 은 업로드 도메인을 보호하지 않는다 (이번에 고친 자리) ────────
    print("\n§1 프리셋 run(fixture·hitl)은 같은 이름 업로드 도메인을 못 막는다")
    for mode in ("hitl", "fixture"):
        reset(upload_age_h=99)
        put_run("r_20260814_008", domain=DOM, mode=mode, status="awaiting_hitl")
        live, last = facts()
        chk(f"{mode} awaiting_hitl → live=False", live is False)
        chk(f"{mode} 종료시각도 안 센다 (clock 이 안 밀린다)", last is None)
        it = plan_of()
        chk(f"{mode} 이 서 있어도 계획은 prune", it["action"] == "prune", f"— {it['reason']}")

    # 🔴 이게 핵심이다. 이름만 보면 위 셋이 전부 keep 이었고, `awaiting_hitl` 은
    #    사람이 답하거나 취소할 때까지 안 끝나므로 **영원히** keep 이었다.

    # ── §2 업로드 run 은 보호한다 (좁히다가 이걸 같이 없애면 안 된다) ─────────────
    print("\n§2 full run 은 보호한다 — 돌고 있는 입력을 발밑에서 지우지 않는다")
    for st in ("queued", "running", "awaiting_hitl"):
        reset(upload_age_h=99)
        put_run("r_20260816_001", domain=DOM, mode="full", status=st)
        live, _ = facts()
        chk(f"full {st} → live=True", live is True)
        chk(f"full {st} → keep", plan_of()["action"] == "keep")

    reset(upload_age_h=99)
    put_run("r_20260816_001", domain=DOM, mode="full", status="succeeded",
            finished=NOW - timedelta(hours=99))
    live, _ = facts()
    chk("full succeeded 는 안 막는다", live is False)
    chk("full succeeded → prune", plan_of()["action"] == "prune")

    # ── §3 모르는 것은 보호한다 (원칙 1) ────────────────────────────────────────
    print("\n§3 「모른다」를 「안 읽는다」로 바꾸지 않는다")
    reset(upload_age_h=99)
    put_run("r_20260101_001", domain=DOM, mode=None, status="running")
    live, _ = facts()
    chk("mode 키가 없는 live run 은 보호한다", live is True)

    reset(upload_age_h=99)
    put_run("r_20260101_001", domain=DOM, mode="full", status="running", broken=True)
    live, _ = facts()
    chk("status.json 을 못 읽으면 보호한다", live is True)

    reset(upload_age_h=99)
    put_run("r_20260101_001", domain=DOM, mode="내일_생길_모드", status="running")
    live, _ = facts()
    chk("모르는 mode 는 프리셋으로 안 친다", live is True,
        "— _domain_root 가 full 이 아닌 것을 전부 프리셋으로 돌리므로 "
        "이 항목이 빨간불이면 판별자 쪽을 볼 것")

    # ── §4 다른 도메인은 애초에 무관하다 ────────────────────────────────────────
    print("\n§4 이름이 다르면 안 본다")
    reset(upload_age_h=99)
    put_run("r_20260816_001", domain="흡연", mode="full", status="running")
    live, _ = facts()
    chk("다른 도메인의 full run 은 무관", live is False)
    chk("→ prune", plan_of()["action"] == "prune")

    # ── §5 시계 — max(마지막 업로드, 마지막 full run 종료) ──────────────────────
    print("\n§5 시계는 둘 중 늦은 쪽 · 프리셋 run 은 시계에 안 들어간다")
    reset(upload_age_h=99)
    put_run("r_20260816_001", domain=DOM, mode="full", status="succeeded",
            finished=NOW - timedelta(hours=1))
    it = plan_of()
    chk("어제 올리고 방금 끝난 run 이 있으면 keep", it["action"] == "keep",
        f"— {it['reason']}")
    chk("시계 출처가 run 종료", it["age_hours"] == 1.0, f"age={it['age_hours']}")

    reset(upload_age_h=99)
    put_run("r_20260816_001", domain=DOM, mode="hitl", status="succeeded",
            finished=NOW - timedelta(hours=1))
    it = plan_of()
    chk("프리셋 run 이 방금 끝나도 시계를 안 민다", it["action"] == "prune",
        f"age={it['age_hours']}")

    reset(upload_age_h=1)
    put_run("r_20260816_001", domain=DOM, mode="full", status="succeeded",
            finished=NOW - timedelta(hours=99))
    it = plan_of()
    chk("반대로 방금 올렸으면 옛 run 종료를 안 쓴다", it["action"] == "keep",
        f"age={it['age_hours']}")

    # ── §6 TTL 경계 ─────────────────────────────────────────────────────────────
    print("\n§6 TTL 경계")
    reset(upload_age_h=23.9)
    chk("23.9시간 → keep", plan_of()["action"] == "keep")
    reset(upload_age_h=24.1)
    chk("24.1시간 → prune", plan_of()["action"] == "prune")
    chk("keep 도 사유를 채운다", bool(plan_of(ttl=999)["reason"]))

    # ── §7 판별자를 사본으로 두지 않았다 ────────────────────────────────────────
    print("\n§7 판별자는 러너 정본을 쓴다")
    chk("_reads_user_input 이 _domain_root 를 부른다",
        "_domain_root" in U._reads_user_input.__code__.co_names or
        "_domain_root" in (U._reads_user_input.__doc__ or ""))
    chk("full 만 업로드 루트", U._reads_user_input({"mode": R.MODE_FULL}) is True)
    chk("hitl 은 아니다", U._reads_user_input({"mode": R.MODE_HITL}) is False)
    chk("fixture 도 아니다", U._reads_user_input({"mode": R.MODE_FIXTURE}) is False)

    # ── §8 정리기는 프리셋 폴더를 아예 못 본다 (자리로 보장) ────────────────────
    print("\n§8 프리셋은 경로상 닿지 않는다")
    from app.config import DOMAIN_ROOT
    chk("USER_INPUT_ROOT ≠ DOMAIN_ROOT",
        Path(str(_REAL_UIN)).resolve() != Path(str(DOMAIN_ROOT)).resolve())
    reset()
    chk("계획에 뜨는 건 임시 업로드 루트의 것뿐",
        [i["domain"] for i in U.plan(24, now=NOW)] == [DOM])

finally:
    R.RUNS_ROOT = _REAL_RUNS
    U.USER_INPUT_ROOT = _REAL_UIN
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'=' * 60}\n결과: {ok} OK · {fail} NG  ({ok + fail} 항목)")
sys.exit(1 if fail else 0)
