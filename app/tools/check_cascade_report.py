# -*- coding: utf-8 -*-
"""`[CASCADED]` 배선 대조 — 적재기 → 러너 → `status.json.loaded.cascaded`.

    python app\\tools\\check_cascade_report.py

🔴 DB 도 uvicorn 도 안 쓴다. 적재기 대신 같은 줄을 찍는 가짜 자식을 돌린다 —
   확인하려는 건 「러너가 그 줄을 읽어 status 에 남기는가」 하나다.
"""
import io
import shutil
import sys
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services import pipeline_runner as R  # noqa: E402
from scripts.load_topn_candidates import cascade_loss  # noqa: E402

ok = fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label} {extra}")
    else:
        fail += 1
        print(f"  [!!] {label} {extra}")


def _keyerror_on(counts) -> bool:
    """`cascade_loss` 가 빠진 키를 **KeyError 로 드러내는가**."""
    try:
        cascade_loss(counts)
    except KeyError:
        return True
    return False


RUN = "r_가짜_cascade"
LINES = [
    "[CASCADED] table=booth_candidates run_id={r} conflict_simulations=8 "
    "debate_logs=112 verified_precedents_unlinked=0 hearing_results_b=2",
    "[LOADED] table=booth_candidates run_id={r} rows=20",
]

# `count_cascade()` 가 내는 키 전부. 여기에 없는 키로 `cascade_loss` 를 부르면
# KeyError 다 — 일부러 그렇다. 키가 빠졌는데 `.get(k, 0)` 으로 넘기면 **안 센 것이
# 0건으로 읽혀** 남의 토론이 --force 없이 지워진다(원칙 1·4).
ZERO = {
    "conflict_simulations": 0,
    "debate_logs": 0,
    "verified_precedents_unlinked": 0,
    "hearing_results_b": 0,
}


def drive(run_id_in_lines: str) -> R._Proc:
    src = "; ".join(f"print({ln.format(r=run_id_in_lines)!r})" for ln in LINES)
    proc = R._Proc(step_ids=(), argv=[sys.executable, "-c", src])
    doc = {"run_id": RUN, "domain": "흡연", "steps": [], "artifacts": {}}
    R._run_one(RUN, doc, proc, io.StringIO())
    return proc


# `_write_status` 가 실제 폴더에 쓴다. 날짜 패턴(`r_YYYYMMDD_NNN`)이 아니라
# `_new_run_id` 의 번호 매김에 안 끼어든다. 끝나면 지운다.
R.run_dir(RUN).mkdir(parents=True, exist_ok=True)

print("--- 1) 같은 run_id 면 읽는다")
p = drive(RUN)
chk("loaded 읽음", p.loaded == {"booth_candidates": 20}, str(p.loaded))
chk("cascaded 4키", p.cascaded == {"conflict_simulations": 8, "debate_logs": 112,
                                   "verified_precedents_unlinked": 0,
                                   "hearing_results_b": 2}, str(p.cascaded))

print("--- 2) 다른 run_id 면 안 읽는다 (남의 run 성과를 이 run 에 적지 않는다)")
try:
    p2 = drive("r_남의run_001")
    chk("run_id 어긋나면 실패", False, "예외가 안 났다")
except R._StepFailed as e:
    chk("run_id 어긋나면 실패", "다른 run_id" in str(e), type(e).__name__)
    p2 = None

print("--- 3) status 합류")
doc = {"loaded": None}
if p.loaded or p.cascaded:
    doc["loaded"] = {"run_id": RUN, **(doc.get("loaded") or {}), **p.loaded}
    if p.cascaded:
        doc["loaded"]["cascaded"] = {**(doc["loaded"].get("cascaded") or {}), **p.cascaded}
chk("run_id 있음", doc["loaded"]["run_id"] == RUN)
chk("행수 있음", doc["loaded"]["booth_candidates"] == 20)
chk("cascaded 중첩", doc["loaded"]["cascaded"]["debate_logs"] == 112)

print("--- 4) 0 도 기록된다 (줄 없음 ≠ 0건)")
chk("0 이 사라지지 않음", doc["loaded"]["cascaded"]["verified_precedents_unlinked"] == 0)

print("--- 5) --force 판정 (되살릴 수 있는 것은 손실이 아니다)")
chk("전부 0 이면 통과", not cascade_loss(ZERO))
chk("A 공청회 있으면 정지", cascade_loss(
    {**ZERO, "conflict_simulations": 8, "debate_logs": 112}))
chk("판례 연결만 끊겨도 정지", cascade_loss({**ZERO, "verified_precedents_unlinked": 3}))
# 🔴 B 만 있는 run 이 있다. A 만 세던 시절엔 여기서 --force 없이 지워졌다.
chk("B 다인토론만 있어도 정지", cascade_loss({**ZERO, "hearing_results_b": 1}))
chk("키가 빠지면 조용히 넘어가지 않는다",
    _keyerror_on({k: v for k, v in ZERO.items() if k != "hearing_results_b"}))

shutil.rmtree(R.run_dir(RUN), ignore_errors=True)
chk("가짜 run 폴더 정리됨", not R.run_dir(RUN).exists())

print(f"\n{ok + fail}항목 중 {ok} 통과 · {fail} 실패")
sys.exit(1 if fail else 0)
