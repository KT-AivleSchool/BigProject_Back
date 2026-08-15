# -*- coding: utf-8 -*-
"""`runs/` 정리 대조 — 무엇을 지우고 **무엇을 안 지키면 멈추는가**.

    python app\\tools\\check_prune_runs.py

🔴 DB 도 uvicorn 도, **진짜 `runs/` 도 안 쓴다.** 임시 폴더를 `RUNS_ROOT` 로 갈아끼운다 —
   확인하려는 건 「어떤 조건에서 지우고 어떤 조건에서 안 지우는가」이지 이 컴퓨터에
   뭐가 쌓여 있는지가 아니다.

🔴 2026-08-11 — 조건 ③(`booth_candidates` 참조 보호)이 **없어졌다.** 그래서 여기서
   「참조 중이면 남긴다」를 확인하던 항목을 지우는 대신 **뒤집어서** 확인한다:
   `referenced_run_ids` 가 사라졌는가 · `prune_now` 가 DB 를 안 보는가.
   없어진 동작을 대조기가 계속 요구하면 대조기가 영원히 빨간불이고, 조용히 지우면
   「원래 그런 조건은 없었다」가 된다 — 없앤 사실이 어딘가엔 남아야 한다.

여기서 확인하는 것 중 **제일 중요한 건 삭제가 아니라 비삭제**다. 잘못 지운 건
되돌릴 수 없고, 안 지운 건 디스크만 쓴다.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services import pipeline_runner as R  # noqa: E402
from app.services import run_pruner as P  # noqa: E402

ok = fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label} {extra}")
    else:
        fail += 1
        print(f"  [!!] {label} {extra}")


DOMAIN = "흡연"
PRE = "흡연"
TMP = Path(tempfile.mkdtemp(prefix="omnisite_prune_"))
_REAL_ROOT = R.RUNS_ROOT

# 8개. 사전순 = 시간순이므로 최근 5개는 004~008 이다.
RUNS = [f"r_20260101_{i:03d}" for i in range(1, 9)]

# 확장자별 1개씩. 지울 것 둘 · 남길 것 넷 — 「폴더째 지우지 않는다」가 여기서 드러난다.
FILES = {
    "step3/{pre}_후보_지적도필지.gpkg": 1000,   # 지운다 (artifacts.candidates)
    "step2/{pre}_clean_05.parquet": 500,        # 지운다
    "step2/{pre}_clean_report.json": 30,        # 남긴다 (artifacts.clean_report)
    "step4/{pre}_exclusion.geojson": 40,
    "run.log": 50,
    "step4/{pre}_topN_min.csv": 10,
}
KILL = (".gpkg", ".parquet")


def build(*, live: dict | None = None, no_status: tuple = ()) -> None:
    """임시 runs/ 를 처음부터 다시 만든다. 파괴적 시험마다 새로 깐다."""
    shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True)
    for rid in RUNS:
        for rel, size in FILES.items():
            p = R.run_dir(rid) / rel.format(pre=PRE)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * size)
        if rid in no_status:
            continue
        doc = R._new_status(rid, DOMAIN)
        doc["status"] = (live or {}).get(rid, "succeeded")
        R._refresh_artifacts(doc)
        R._write_status(rid, doc)


def files_of(rid: str) -> set[str]:
    d = R.run_dir(rid)
    return {f.relative_to(d).as_posix() for f in d.rglob("*") if f.is_file()}


def run(coro):
    return asyncio.run(coro)


R.RUNS_ROOT = TMP
try:
    # ══════════════════════════════════════════════════════════
    print("--- 1) 계획 — 왜 남기는지까지 말하는가")
    # 003 은 사람을 기다리는 중. 002 는 예전 조건 ③ 이면 보호됐을 자리다.
    build(live={"r_20260101_003": "awaiting_hitl"})
    items = P.plan(5)
    by = {i["run_id"]: i for i in items}

    chk("run 8개 전부 계획에 나온다", len(items) == 8, f"({len(items)})")
    chk("001 은 지운다", by["r_20260101_001"]["action"] == "prune")
    chk("002 도 지운다 — DB 참조는 더 이상 보호가 아니다",
        by["r_20260101_002"]["action"] == "prune",
        f"({by['r_20260101_002']['action']}/{by['r_20260101_002']['reason']})")
    chk("003 은 사람을 기다리는 중이라 남긴다",
        by["r_20260101_003"]["action"] == "keep"
        and "awaiting_hitl" in by["r_20260101_003"]["reason"],
        f"({by['r_20260101_003']['reason']})")
    chk("004~008 은 최근 5개라 남긴다",
        all(by[r]["action"] == "keep" and "최근 5개" in by[r]["reason"]
            for r in RUNS[3:]))
    chk("남기는 항목은 전부 이유가 있다",
        all(i["reason"] for i in items if i["action"] == "keep"))
    chk("계획은 아무것도 안 지운다",
        all(files_of(r) == {k.format(pre=PRE) for k in FILES} | {"status.json"}
            for r in RUNS))

    # ══════════════════════════════════════════════════════════
    print("--- 2) 실행 — 폴더째 지우지 않는다")
    res = P.apply(items, keep=5)
    left = files_of("r_20260101_001")
    chk("지운 run 2개", res["runs"] == 2, f"({res['runs']})")
    chk("지운 파일 4개", res["files"] == 4, f"({res['files']})")
    chk("회수 바이트가 실제 크기와 같다", res["bytes"] == 3000, f"({res['bytes']})")
    chk("gpkg·parquet 는 사라졌다",
        not any(f.endswith(KILL) for f in left))
    chk("json·geojson·csv·log 는 남았다",
        {"status.json", "run.log", f"step2/{PRE}_clean_report.json",
         f"step4/{PRE}_exclusion.geojson",
         f"step4/{PRE}_topN_min.csv"} <= left, f"({sorted(left)})")
    chk("남긴 run 은 손대지 않았다",
        all(any(f.endswith(KILL) for f in files_of(r)) for r in RUNS[2:]))

    # ══════════════════════════════════════════════════════════
    print("--- 3) 기록 — 없어진 이유가 산출물에 남는가")
    doc = json.loads((R.run_dir("r_20260101_001") / "status.json").read_text("utf-8"))
    pr = doc.get("pruned") or {}
    chk("status.json 에 pruned 가 있다", bool(pr))
    # 🔴 여기서 5 를 요구하는 건 우연이 아니다. 환경 기본값은 100 이고 계획은 5 로
    #    세웠다 — 기록이 `keep_recent:100` 이면 apply() 가 실제 쓴 값이 아니라
    #    keep_count() 를 다시 읽고 있다는 뜻이다(무엇을 기준으로 지웠는지가 거짓).
    chk("언제·어떤 정책으로 지웠는지 있다 (계획에 쓴 keep 그대로)",
        bool(pr.get("at")) and pr.get("policy") == "keep_recent:5",
        f"({pr.get('policy')} · 환경 기본값 {P.keep_count()})")
    chk("지운 파일 목록이 상대경로+크기로 남는다",
        len(pr.get("removed") or []) == 2
        and all(set(x) == {"path", "bytes"} for x in pr["removed"]),
        f"({[x['path'] for x in pr.get('removed', [])]})")
    # 🔴 status 가 URL 을 주는데 엔드포인트가 404 면 status 가 거짓말을 한다(원칙 4).
    chk("지운 산출물의 URL 은 null 로 돌아갔다",
        doc["artifacts"]["candidates"] is None)
    chk("남은 산출물의 URL 은 그대로다",
        isinstance(doc["artifacts"]["clean_report"], str))

    # ══════════════════════════════════════════════════════════
    print("--- 4) 없어진 조건 ③ · 부팅 훅")
    # ⓐ 조건 ③ 이 정말 없어졌는가. 함수가 남아 있으면 누군가 다시 배선한다.
    chk("referenced_run_ids 가 없다", not hasattr(P, "referenced_run_ids"))
    chk("prune_now 결과에 protected 키가 없다",
        "protected" not in run(P.prune_now(dry_run=True)))
    chk("run_pruner 소스에 booth_candidates 조회가 없다",
        "select(Parcel.run_id)" not in Path(P.__file__).read_text("utf-8"))

    # ⓑ 부팅 훅은 **어떤 실패도 기동을 막지 않는다.** 안 지우면 디스크만 쓰지만
    #    기동이 막히면 프런트가 통째로 멈춘다.
    build()
    orig_plan = P.plan

    def _boom(keep):
        raise RuntimeError("계획 실패(가짜)")

    P.plan = _boom
    hook_raised = False
    try:
        run(P.prune_on_boot_hook())
    except Exception:
        hook_raised = True
    chk("부팅 훅은 실패해도 기동을 안 막는다", not hook_raised)
    chk("그때 아무 파일도 안 지워졌다",
        all(any(f.endswith(KILL) for f in files_of(r)) for r in RUNS))
    P.plan = orig_plan

    # ══════════════════════════════════════════════════════════
    print("--- 5) 설정값 — 조용히 기본값으로 넘어가지 않는가")
    old = dict(os.environ)
    try:
        os.environ.pop("OMNISITE_RUNS_KEEP", None)
        # 🔴 5 → 100 (2026-08-11). 조건 ③ 을 뺀 대신 상한을 여기 하나로 받는다.
        chk("기본 보관 개수는 100", P.keep_count() == 100, f"({P.keep_count()})")
        os.environ["OMNISITE_RUNS_KEEP"] = "10"
        chk("env 로 바꿀 수 있다", P.keep_count() == 10)
        for bad, why in (("다섯", "정수가 아니면"), ("0", "0 이면"), ("-1", "음수면")):
            os.environ["OMNISITE_RUNS_KEEP"] = bad
            try:
                P.keep_count()
                got = False
            except RuntimeError:
                got = True
            chk(f"{why} raise 한다", got, f"({bad!r})")
        os.environ.pop("OMNISITE_RUNS_KEEP", None)

        os.environ.pop("OMNISITE_RUNS_PRUNE_ON_BOOT", None)
        chk("부팅 자동 정리는 기본 켜짐", P.prune_on_boot() is True)
        for v in ("0", "false", "OFF", "no"):
            os.environ["OMNISITE_RUNS_PRUNE_ON_BOOT"] = v
            chk(f"{v!r} 로 끌 수 있다", P.prune_on_boot() is False)
    finally:
        os.environ.clear()
        os.environ.update(old)

    # ══════════════════════════════════════════════════════════
    print("--- 6) 가장자리")
    # status.json 이 없어도 파일은 지운다. 기록할 데가 없다고 안 지우면
    # 그 run 은 영원히 안 줄어든다 — 대신 로그로 남긴다(모듈 apply() 참조).
    build(no_status=("r_20260101_001",))
    P.apply(P.plan(5), keep=5)
    chk("status 없는 run 도 파일은 지운다",
        not any(f.endswith(KILL) for f in files_of("r_20260101_001")))
    chk("status.json 을 새로 만들지는 않는다",
        "status.json" not in files_of("r_20260101_001"))

    # 두 번째 정리는 지울 게 없다. 「지울 파일 없음」으로 남아야 한다.
    items2 = P.plan(5)
    it1 = next(i for i in items2 if i["run_id"] == "r_20260101_002")
    chk("이미 정리된 run 은 지울 파일 없음으로 남긴다",
        it1["action"] == "keep" and it1["reason"] == "지울 파일 없음",
        f"({it1['action']}/{it1['reason']})")

    # 기록은 덮어쓰지 않고 쌓인다 — 2차 정리에서 1차 기록이 사라지면
    # 「왜 없어졌나」의 절반이 없어진다.
    build()
    P.apply(P.plan(5), keep=5)
    (R.run_dir("r_20260101_001") / "step3" / f"{PRE}_후보_지적도필지.gpkg").write_bytes(b"y" * 7)
    P.apply(P.plan(5), keep=5)
    doc = json.loads((R.run_dir("r_20260101_001") / "status.json").read_text("utf-8"))
    chk("2차 정리 기록은 1차에 덧붙는다",
        len(doc["pruned"]["removed"]) == 3, f"({len(doc['pruned']['removed'])})")

    # 빈 runs/ 에서도 안 터진다.
    shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True)
    chk("빈 runs/ 에서 계획은 빈 목록", P.plan(5) == [])
    shutil.rmtree(TMP, ignore_errors=True)
    chk("runs/ 자체가 없어도 안 터진다", P.plan(5) == [])

finally:
    R.RUNS_ROOT = _REAL_ROOT
    shutil.rmtree(TMP, ignore_errors=True)

chk("진짜 runs/ 는 손대지 않았다", R.RUNS_ROOT == _REAL_ROOT and not TMP.exists())

print(f"\n{ok + fail}항목 중 {ok} 통과 · {fail} 실패")
sys.exit(1 if fail else 0)
