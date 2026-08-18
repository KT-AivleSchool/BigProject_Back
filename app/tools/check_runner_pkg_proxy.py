# -*- coding: utf-8 -*-
"""`pipeline_runner` 패키지 프록시 대조 — 「갈아끼우기가 서브모듈까지 닿는가」.

    python app\\tools\\check_runner_pkg_proxy.py

🔴 이것만이 재는 것
   2026-08-18 에 `pipeline_runner.py`(2,580행)를 12개 서브모듈로 갈랐다. 한 파일이던
   시절 `R.RUNS_ROOT = tmp` 는 그 파일의 전역 하나를 고쳤고 **모든 함수가 즉시** 새
   값을 봤다. 패키지로 가르면 `from .state import RUNS_ROOT` 가 **값을 복사**하므로
   `R.RUNS_ROOT = tmp` 는 패키지 attr 하나만 고치고 서브모듈은 옛 값을 계속 본다.

   실측(분할 직후, 프록시 없이):
       패키지 attr : /tmp/fake
       run_dir()   : \\real\\runs\\r_1      ← 진짜 폴더

   **예외가 안 난다.** 대조기 9종이 초록불인 채로 **진짜 `runs/`** 를 건드린다 —
   `check_prune_runs` 는 진짜 .gpkg 를 지우고, `check_run_records_e2e` 의
   `reap_orphans()` 는 **남이 돌리는 run** 을 failed 로 닫는다.

   그래서 패키지 `__init__` 에 `_Proxy.__setattr__` 을 걸어 소유 서브모듈의 전역까지
   밀어 넣는다. **이 파일은 그 마법이 살아 있는지만 잰다** — 러너 동작은 안 본다
   (그건 `check_cancel_run`·`check_prune_runs` 등 9종의 몫이다).

🔴 대조기 9종은 **한 줄도 안 고쳤다**(사람 결정 2026-08-18). 분할하면서 회귀망을 같이
   고치면 「분할이 안전한가」를 재는 자와 재어지는 자가 같이 움직인다. 대신 그
   마법을 지키는 자를 따로 둔 것이 이 파일이다.

DB·LLM·uvicorn 안 쓴다. **진짜 `runs/` 도 안 쓴다** — 경로를 문자열로만 다룬다.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.services.pipeline_runner as R  # noqa: E402

ok = fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label} {extra}")
    else:
        fail += 1
        print(f"  [!!] {label} {extra}")


def _owners(name: str) -> list[str]:
    return [m.__name__.rsplit(".", 1)[-1] for m in R._SUBMODULES if name in m.__dict__]


# ══════════════════════════════════════════════════════════════════
print("--- 1) 마법이 걸려 있는가 · 장부가 실제 서브모듈 전부인가")
# ══════════════════════════════════════════════════════════════════
chk("패키지 클래스가 _Proxy", type(R).__name__ == "_Proxy", type(R).__name__)

# 🔴 `_SUBMODULES` 에서 빠진 서브모듈은 **전파 대상에서 조용히 빠진다.** 새 파일을
#    만들고 여기 안 적으면 그 모듈만 옛 값으로 돈다 — 예외가 안 난다.
_pkg_dir = Path(R.__file__).parent
_on_disk = {p.stem for p in _pkg_dir.glob("*.py") if p.stem != "__init__"}
_in_ledger = {m.__name__.rsplit(".", 1)[-1] for m in R._SUBMODULES}
chk("장부 == 폴더의 서브모듈", _on_disk == _in_ledger,
    f"폴더에만 {sorted(_on_disk - _in_ledger)} · 장부에만 {sorted(_in_ledger - _on_disk)}")
chk("서브모듈 11개", len(R._SUBMODULES) == 11, str(len(R._SUBMODULES)))


# ══════════════════════════════════════════════════════════════════
print("--- 2) 전파 — RUNS_ROOT (대조기 6종이 갈아끼우는 바로 그 이름)")
# ══════════════════════════════════════════════════════════════════
_orig_root = R.RUNS_ROOT
FAKE = Path("Z:/가짜runs_대조용")

chk("소유 서브모듈 3개", sorted(_owners("RUNS_ROOT")) == ["prepare", "state", "status"],
    str(sorted(_owners("RUNS_ROOT"))))

R.RUNS_ROOT = FAKE
chk("패키지 attr 이 바뀜", R.RUNS_ROOT == FAKE, str(R.RUNS_ROOT))
for _m in R._SUBMODULES:
    if "RUNS_ROOT" in _m.__dict__:
        chk(f"{_m.__name__.rsplit('.', 1)[-1]}.RUNS_ROOT 전파",
            _m.__dict__["RUNS_ROOT"] == FAKE, str(_m.__dict__["RUNS_ROOT"]))

# 🔴 여기가 핵심이다. attr 만 보면 위 세 줄로 통과하지만, 실제로 폴더를 고르는 것은
#    `state.run_dir()` 이다 — 프록시가 없던 순간의 증상이 정확히 이 어긋남이었다.
chk("run_dir() 이 갈아낀 경로를 돌려준다", R.run_dir("r_1") == FAKE / "r_1",
    str(R.run_dir("r_1")))
chk("_status_path() 도 같이 따라온다",
    R._status_path("r_1") == FAKE / "r_1" / "status.json", str(R._status_path("r_1")))

# ⚠ **분할 전부터 그랬다.** `_SEQ_PATH` 는 import 시점에 굳어서 `RUNS_ROOT` 를
#    갈아끼워도 안 따라온다. 여기 적는 이유는 「분할이 이걸 바꾸지 않았다」를 재기
#    위해서다 — 고칠 거면 별건이고, 고치면 이 항목이 빨간불로 알려준다.
chk("[알려진 성질] _SEQ_PATH 는 안 따라온다(분할 전과 같다)",
    R._SEQ_PATH.parent != FAKE, str(R._SEQ_PATH))

R.RUNS_ROOT = _orig_root
chk("되돌리기도 전파된다",
    all(m.__dict__["RUNS_ROOT"] == _orig_root
        for m in R._SUBMODULES if "RUNS_ROOT" in m.__dict__)
    and R.run_dir("r_1") == _orig_root / "r_1",
    str(R.run_dir("r_1")))


# ══════════════════════════════════════════════════════════════════
print("--- 3) 전파 — run_records (모듈 객체를 가짜로 갈아끼운다)")
# ══════════════════════════════════════════════════════════════════
# `check_run_records.py:252` 가 `R.run_records = 가짜모듈` 로 친다. 소유가 둘
# (`status`·`runner`)인데 **같은 객체**라 모호가 아니다 — 둘 다 고쳐야 한다.
_orig_rr = R.run_records
chk("소유 서브모듈 2개", sorted(_owners("run_records")) == ["runner", "status"],
    str(sorted(_owners("run_records"))))

_fake_rr = object()
R.run_records = _fake_rr
chk("status·runner 둘 다 가짜를 본다",
    all(m.__dict__["run_records"] is _fake_rr
        for m in R._SUBMODULES if "run_records" in m.__dict__))
R.run_records = _orig_rr
chk("되돌아옴", R.run_records is _orig_rr and R.status.run_records is _orig_rr)


# ══════════════════════════════════════════════════════════════════
print("--- 4) 모호하면 터진다 (추측해서 한쪽만 고치지 않는다)")
# ══════════════════════════════════════════════════════════════════
_NAME = "_모호_대조용"
_a, _b = object(), object()

# ⓐ 서로 다른 객체를 두 서브모듈이 같은 이름으로 들고 있다 → 어느 쪽인지 알 수 없다
R.state.__dict__[_NAME] = _a
R.steps.__dict__[_NAME] = _b
try:
    setattr(R, _NAME, 1)
    chk("서로 다른 것을 들고 있으면 raise", False, "조용히 통과했다")
except RuntimeError as e:
    chk("서로 다른 것을 들고 있으면 raise", "state" in str(e) and "steps" in str(e),
        type(e).__name__)
chk("터진 뒤 아무것도 안 고쳐졌다",
    R.state.__dict__[_NAME] is _a and R.steps.__dict__[_NAME] is _b)

# ⓑ 같은 객체를 여럿이 들고 있는 것은 모호가 **아니다** — 그게 정상이고 전부 고친다
R.steps.__dict__[_NAME] = _a
setattr(R, _NAME, 7)
chk("같은 것을 들고 있으면 안 터지고 전부 고친다",
    R.state.__dict__[_NAME] == 7 and R.steps.__dict__[_NAME] == 7)

# ⓒ 아무도 안 가진 이름은 패키지에만 앉는다(전파할 데가 없다)
del R.state.__dict__[_NAME], R.steps.__dict__[_NAME]
_NEW = "_없던이름_대조용"
setattr(R, _NEW, 3)
chk("아무도 안 가진 이름은 패키지에만", getattr(R, _NEW) == 3 and _owners(_NEW) == [])
delattr(R, _NEW)
delattr(R, _NAME)
chk("대조용 이름 정리됨",
    not hasattr(R, _NAME) and not hasattr(R, _NEW) and _owners(_NAME) == [])


# ══════════════════════════════════════════════════════════════════
print("--- 5) 표면 — 서브모듈이 정의한 것은 전부 패키지에서 **같은 객체**로 보인다")
# ══════════════════════════════════════════════════════════════════
# 🔴 재내보내기를 빠뜨리면 `R.<이름>` 이 `AttributeError` 다. 라우터·대조기가 그
#    이름으로 부르므로 「서브모듈에 함수를 더하고 __init__ 을 안 고쳤다」가 그대로
#    구멍이 된다. import 는 안 센다 — 재수출 대상은 **그 모듈이 정의한 것**이다.
_missing: list[str] = []
_aliased: list[str] = []
_total = 0
for _m in R._SUBMODULES:
    _tree = ast.parse(Path(_m.__file__).read_text(encoding="utf-8"))
    _defined: set[str] = set()
    for _n in _tree.body:
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _defined.add(_n.name)
        elif isinstance(_n, ast.Assign):
            _defined.update(t.id for t in _n.targets if isinstance(t, ast.Name))
        elif isinstance(_n, ast.AnnAssign) and isinstance(_n.target, ast.Name):
            _defined.add(_n.target.id)
    _short = _m.__name__.rsplit(".", 1)[-1]
    for _name in sorted(_defined):
        _total += 1
        if not hasattr(R, _name):
            _missing.append(f"{_short}.{_name}")
        elif getattr(R, _name) is not _m.__dict__[_name]:
            _aliased.append(f"{_short}.{_name}")

chk(f"재내보내기 누락 0건 (정의 {_total}개)", not _missing, str(_missing[:8]))
chk("패키지가 보는 것 == 서브모듈이 정의한 그 객체", not _aliased, str(_aliased[:8]))

# 밖에서 실제로 `R.<이름>` 으로 부르는 것 전수(2026-08-18 grep 실측 44개).
# 정의가 아니라 **import 로 들어온 것**(`USER_INPUT_ROOT`·`run_records`)도 섞여 있어
# 위 검사로는 안 걸린다 — `user_input_pruner.py:140` 이 실제로 읽는다.
_CALLED = (
    "GATE_IDS MODES MODE_FIXTURE MODE_FULL MODE_HITL RUNS_ROOT RunConflict "
    "RunRequestError USER_INPUT_ROOT _ACTIVE _CANCELLED _CANCEL_MSG _CHILDREN _LOCK "
    "_PLAN _Proc _StepFailed _TERM_GRACE_S _WORKERS _answer_path _apply_audit "
    "_domain_root _load_conditions _new_status _now_iso _prepare_dirs _proc_of "
    "_questions_audit _read_params _refresh_artifacts _resume_index _run_one "
    "_save_answer _spawn _stage_args _validate_weight _write_status cancel_run "
    "note_run_record_error read_status reap_orphans run_dir run_records start_run"
).split()
_gone = [n for n in _CALLED if not hasattr(R, n)]
chk(f"밖에서 부르는 이름 {len(_CALLED)}개 전부 있음", not _gone, str(_gone))

print(f"\n{ok + fail}항목 중 {ok} 통과 · {fail} 실패")
sys.exit(1 if fail else 0)
