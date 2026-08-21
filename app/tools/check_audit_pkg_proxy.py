# -*- coding: utf-8 -*-
r"""`gam2_audit_judgment_test` 패키지 프록시 대조 — 「스텁이 서브모듈까지 닿는가」.

    python app\tools\check_audit_pkg_proxy.py

🔴 이것만이 재는 것
   2026-08-19 에 `gam2_audit_judgment_test.py`(2,179행)를 11개 서브모듈로 갈랐다.
   한 파일이던 시절 `A.RealLLM = 스텁` 은 그 파일의 전역 하나를 고쳤고 **모든 함수가
   즉시** 새 값을 봤다. 패키지로 가르면 `from .llm import RealLLM` 이 **값을 복사**
   하므로 그 대입은 패키지 attr 하나만 고치고 서브모듈은 옛 값을 계속 본다.

   **예외가 안 난다.** 실제로 갈아끼우는 곳이 있다 — `check_full_step01.py` §10 이
   이름 11개를 스텁으로 바꿔 STEP0·1 을 LLM 없이 돌린다(`RealLLM` 은 **부르면 터지는**
   `_BoomLLM` 이다). 프록시가 없으면 스텁을 꽂아도 진짜 `RealLLM` 이 불려
   **대조기가 돈이 드는 진짜 호출을 한다. 그리고 초록불이 뜬다.**

   그래서 패키지 `__init__` 에 `_Proxy.__setattr__` 을 걸어 소유 서브모듈의 전역까지
   밀어 넣는다. **이 파일은 그 마법이 살아 있는지만 잰다** — 감리 동작은 안 본다
   (그건 `check_full_step01`·`check_hitl_gate`·`check_fixture` 의 몫이다).

🔴 패키지 **안쪽**도 같다. `harness` 는 `enrich_hitl_flags` 를, `admin_code` 는
   `build_fixtures` 를, `hitl` 은 `apply_radius_answer` 를 자기 전역으로 들고 있다 —
   패키지 attr 만 고치면 이 셋은 옛 함수를 계속 부른다. §3 이 그걸 잰다.

🔴 대조기들은 **한 줄도 안 고쳤다**(사람 결정 2026-08-18). 분할하면서 회귀망을 같이
   고치면 「분할이 안전한가」를 재는 자와 재어지는 자가 같이 움직인다. 대신 그
   마법을 지키는 자를 따로 둔 것이 이 파일이다.

DB·LLM 안 쓴다. 도메인 폴더도 안 읽는다 — 이름과 객체만 다룬다.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.services.gam2_audit_judgment_test as A  # noqa: E402

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
    return [m.__name__.rsplit(".", 1)[-1] for m in A._SUBMODULES if name in m.__dict__]


# ══════════════════════════════════════════════════════════════════
print("--- 1) 마법이 걸려 있는가 · 장부가 실제 서브모듈 전부인가")
# ══════════════════════════════════════════════════════════════════
chk("패키지 클래스가 _Proxy", type(A).__name__ == "_Proxy", type(A).__name__)

# 🔴 `_SUBMODULES` 에서 빠진 서브모듈은 **전파 대상에서 조용히 빠진다.** 새 파일을
#    만들고 여기 안 적으면 그 모듈만 옛 값으로 돈다 — 예외가 안 난다.
# ⚠ `__main__` 은 장부에 **없는 게 맞다.** CLI 진입점이라 아무도 그 전역을 갈아
#    끼우지 않고, 넣으면 `python -m` 이 아닐 때 import 되지도 않는다.
_pkg_dir = Path(A.__file__).parent
_on_disk = {p.stem for p in _pkg_dir.glob("*.py") if p.stem not in ("__init__", "__main__")}
_in_ledger = {m.__name__.rsplit(".", 1)[-1] for m in A._SUBMODULES}
chk("장부 == 폴더의 서브모듈(__main__ 제외)", _on_disk == _in_ledger,
    f"폴더에만 {sorted(_on_disk - _in_ledger)} · 장부에만 {sorted(_in_ledger - _on_disk)}")
chk("서브모듈 10개", len(A._SUBMODULES) == 10, str(len(A._SUBMODULES)))
chk("__main__ 은 장부에 없다", "__main__" not in _in_ledger)


# ══════════════════════════════════════════════════════════════════
print("--- 2) 전파 — check_full_step01.py §10 이 실제로 갈아끼우는 이름 11개")
# ══════════════════════════════════════════════════════════════════
# 🔴 목록은 그 파일 §10 에서 뽑은 **실측**이다(2026-08-19 · 대입 15자리 · 고유 11개).
#    여기서 하나라도 소유자가 0이면 그 스텁은 **아무 데도 안 꽂힌다** — 그런데 대입
#    자체는 조용히 성공하므로 대조기는 초록불이고 진짜 LLM 이 불린다.
_STUBBED = [
    "set_domain", "build_fixtures", "resolve_facility", "resolve_facility_mock",
    "save_facility_inference", "MockLLM", "RealLLM", "run_harness",
    "report", "save_results", "enrich_with_search",
]

_orphan = [n for n in _STUBBED if not _owners(n)]
chk(f"스텁 대상 {len(_STUBBED)}개 전부 소유 서브모듈이 있다", not _orphan, str(_orphan))

_SENTINEL = object()
_broken: list[str] = []
for _n in _STUBBED:
    _orig = getattr(A, _n)
    setattr(A, _n, _SENTINEL)
    if getattr(A, _n) is not _SENTINEL:
        _broken.append(f"{_n}(패키지)")
    for _m in A._SUBMODULES:
        if _n in _m.__dict__ and _m.__dict__[_n] is not _SENTINEL:
            _broken.append(f"{_m.__name__.rsplit('.', 1)[-1]}.{_n}")
    setattr(A, _n, _orig)
    for _m in A._SUBMODULES:
        if _n in _m.__dict__ and _m.__dict__[_n] is not _orig:
            _broken.append(f"{_m.__name__.rsplit('.', 1)[-1]}.{_n}(되돌리기)")
chk("11개 전부 소유 서브모듈까지 전파되고 되돌아온다", not _broken, str(_broken[:8]))


# ══════════════════════════════════════════════════════════════════
print("--- 3) 패키지 **안쪽** 호출자도 갈아낀 것을 본다")
# ══════════════════════════════════════════════════════════════════
# 🔴 attr 만 보면 §2 로 통과하지만, 실제로 함수를 고르는 것은 **호출하는 서브모듈의
#    전역**이다. 프록시가 없던 순간의 증상이 정확히 이 어긋남이었다
#    (`pipeline_runner` 때의 `run_dir()` 자리).
chk("build_fixtures 소유 2곳", sorted(_owners("build_fixtures")) == ["admin_code", "fixtures"],
    str(sorted(_owners("build_fixtures"))))
chk("enrich_hitl_flags 소유 2곳",
    sorted(_owners("enrich_hitl_flags")) == ["exclusions", "harness"],
    str(sorted(_owners("enrich_hitl_flags"))))
chk("apply_radius_answer 소유 2곳",
    sorted(_owners("apply_radius_answer")) == ["exclusions", "hitl"],
    str(sorted(_owners("apply_radius_answer"))))

# 진짜 호출로 재는 자리 — `admin_code._code_samples` 는 캐시가 비면 `build_fixtures()`
# 를 부른다. 스텁이 안 닿으면 여기서 **진짜 프로파일을 읽으려 든다**(도메인 미설정이라
# 예외 → 조용히 `{}` → 표본 0개). 즉 값만 틀린다.
_orig_bf = A.build_fixtures
_orig_cache = A._FIXTURE_CACHE
_called: list[int] = []


def _fake_build_fixtures(*a, **kw):
    _called.append(1)
    return {"Z9": {"sample_rows": [{"코드": "11170510"}]}}


A.build_fixtures = _fake_build_fixtures
A._FIXTURE_CACHE = None
_got = A._code_samples("Z9", "코드")
chk("admin_code._code_samples 가 갈아낀 build_fixtures 를 부른다", _called == [1], str(_called))
chk("그 결과가 스텁의 값이다", _got == ["11170510"], str(_got))

A.build_fixtures = _orig_bf
A._FIXTURE_CACHE = _orig_cache
chk("되돌아옴",
    A.build_fixtures is _orig_bf
    and A.admin_code.__dict__["build_fixtures"] is _orig_bf
    and A._FIXTURE_CACHE is _orig_cache)

# 소유가 4곳인 이름도 빠짐없이 도는가(`_out_path` — 산출물 경로를 정한다).
chk("_out_path 소유 4곳",
    sorted(_owners("_out_path")) == ["exclusions", "hitl", "outputs", "state"],
    str(sorted(_owners("_out_path"))))
_orig_op = A._out_path
A._out_path = _SENTINEL
chk("_out_path 4곳 전부 전파",
    all(m.__dict__["_out_path"] is _SENTINEL
        for m in A._SUBMODULES if "_out_path" in m.__dict__))
A._out_path = _orig_op


# ══════════════════════════════════════════════════════════════════
print("--- 4) 모호하면 터진다 (추측해서 한쪽만 고치지 않는다)")
# ══════════════════════════════════════════════════════════════════
_NAME = "_모호_대조용"
_a, _b = object(), object()

# ⓐ 서로 다른 객체를 두 서브모듈이 같은 이름으로 들고 있다 → 어느 쪽인지 알 수 없다
A.state.__dict__[_NAME] = _a
A.harness.__dict__[_NAME] = _b
try:
    setattr(A, _NAME, 1)
    chk("서로 다른 것을 들고 있으면 raise", False, "조용히 통과했다")
except RuntimeError as e:
    chk("서로 다른 것을 들고 있으면 raise", "state" in str(e) and "harness" in str(e),
        type(e).__name__)
chk("터진 뒤 아무것도 안 고쳐졌다",
    A.state.__dict__[_NAME] is _a and A.harness.__dict__[_NAME] is _b)

# ⓑ 같은 객체를 여럿이 들고 있는 것은 모호가 **아니다** — 그게 정상이고 전부 고친다
#    (`_out_path` 4곳 · `build_fixtures` 2곳이 바로 그 모양이다)
A.harness.__dict__[_NAME] = _a
setattr(A, _NAME, 7)
chk("같은 것을 들고 있으면 안 터지고 전부 고친다",
    A.state.__dict__[_NAME] == 7 and A.harness.__dict__[_NAME] == 7)

# ⓒ 아무도 안 가진 이름은 패키지에만 앉는다(전파할 데가 없다)
del A.state.__dict__[_NAME], A.harness.__dict__[_NAME]
_NEW = "_없던이름_대조용"
setattr(A, _NEW, 3)
chk("아무도 안 가진 이름은 패키지에만", getattr(A, _NEW) == 3 and _owners(_NEW) == [])
delattr(A, _NEW)
delattr(A, _NAME)
chk("대조용 이름 정리됨",
    not hasattr(A, _NAME) and not hasattr(A, _NEW) and _owners(_NAME) == [])


# ══════════════════════════════════════════════════════════════════
print("--- 5) 표면 — 서브모듈이 정의한 것은 전부 패키지에서 **같은 객체**로 보인다")
# ══════════════════════════════════════════════════════════════════
# 🔴 재내보내기를 빠뜨리면 `A.<이름>` 이 `AttributeError` 다. 파이프라인·대조기가 그
#    이름으로 부르므로 「서브모듈에 함수를 더하고 __init__ 을 안 고쳤다」가 그대로
#    구멍이 된다. import 는 안 센다 — 재수출 대상은 **그 모듈이 정의한 것**이다.
_missing: list[str] = []
_aliased: list[str] = []
_total = 0
for _m in A._SUBMODULES:
    _tree = ast.parse(Path(_m.__file__).read_text(encoding="utf-8"))
    _defined: set[str] = set()
    for _n2 in _tree.body:
        if isinstance(_n2, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _defined.add(_n2.name)
        elif isinstance(_n2, ast.Assign):
            _defined.update(t.id for t in _n2.targets if isinstance(t, ast.Name))
        elif isinstance(_n2, ast.AnnAssign) and isinstance(_n2.target, ast.Name):
            _defined.add(_n2.target.id)
    _short = _m.__name__.rsplit(".", 1)[-1]
    for _name in sorted(_defined):
        _total += 1
        if not hasattr(A, _name):
            _missing.append(f"{_short}.{_name}")
        elif getattr(A, _name) is not _m.__dict__[_name]:
            _aliased.append(f"{_short}.{_name}")

chk(f"재내보내기 누락 0건 (정의 {_total}개)", not _missing, str(_missing[:8]))
chk("패키지가 보는 것 == 서브모듈이 정의한 그 객체", not _aliased, str(_aliased[:8]))

# 밖에서 실제로 부르는 것 전수(2026-08-19 grep 실측 19개).
#   `A.<이름>` 18개 — gam2_clean_data · gam2_run_pipeline · pipeline_runner.answers ·
#                     pipeline_runner.prepare · check_hitl_gate · check_full_step01
#   이름 import 2개 — check_ordinance_select.py:32 (`load_ordinance` · `set_domain`)
# ⚠ `A.DOMAIN`(gam2_run_pipeline.py:209)은 **주석 안**이다 — 지운 용산구 폴백의 기록이라
#    재수출 대상이 아니다. 부분문자열로 세면 여기 끼어든다.
_CALLED = (
    "MockLLM RealLLM _DOMAIN _out_path apply_intent_answer apply_radius_answer "
    "assert_exclusions_confirmed build_fixtures enrich_with_search load_ordinance "
    "report require_region reset_exclusion_confirmations resolve_facility "
    "resolve_facility_mock run_harness save_facility_inference save_results set_domain"
).split()
_gone = [n for n in _CALLED if not hasattr(A, n)]
chk(f"밖에서 부르는 이름 {len(_CALLED)}개 전부 있음", not _gone, str(_gone))

print(f"\n{ok + fail}항목 중 {ok} 통과 · {fail} 실패")
sys.exit(1 if fail else 0)
