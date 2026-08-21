# -*- coding: utf-8 -*-
r"""`gam2_weight_model` 패키지 프록시 대조 — 「스텁이 서브모듈까지 닿는가」.

    python app\tools\check_weight_pkg_proxy.py

🔴 이것만이 재는 것
   2026-08-19 에 `gam2_weight_model.py`(1,752행)를 10개 서브모듈로 갈랐다.
   한 파일이던 시절 `W.build_matrix = 스텁` 은 그 파일의 전역 하나를 고쳤고 **모든
   함수가 즉시** 새 값을 봤다. 패키지로 가르면 `from .matrix import build_matrix` 가
   **값을 복사**하므로 그 대입은 패키지 attr 하나만 고치고 서브모듈은 옛 값을 계속 본다.

🔴 **여기엔 오늘 갈아끼우는 곳이 0곳이다.** 감리(`A`) 패키지는 `check_full_step01.py`
   §10 이 이름 11개를 스텁으로 바꾸는 **현재 소비자**가 있었지만, `W.<이름> = …` 는
   저장소 전수에서 **한 자리도 안 나온다**(2026-08-19 실측). 그래서 이 프록시는 오늘의
   버그를 고치는 게 아니라 **내일의 조용한 오동작을 막는다**:
   `A.RealLLM = 스텁` 이 되는 걸 본 사람은 `W.build_matrix = 스텁` 도 될 거라고 읽는다.
   셋(A·R·W) 중 하나만 규칙이 다르면 그 기대가 **예외 없이 배신당한다** — 값만 틀린다.

   그러니 이 파일은 「지금 쓰이는 기능이 도는가」가 아니라 **「그 규칙이 살아 있는가」**
   를 잰다. 소비자가 없는 동안 조용히 상하는 것을 막는 자가 이것 하나뿐이다.

🔴 패키지 **안쪽**은 이미 소비자다. `matrix` 는 `load_admin_crosswalk`·
   `admin_names_to_codes`·`_detect_admin_key_col` 을, `diagnostics` 는
   `critic_weights`·`synthesize`·`normalize_matrix` 를, `outputs` 는 `data_note` 를
   자기 전역으로 들고 있다 — 패키지 attr 만 고치면 이들은 옛 함수를 계속 부른다.
   §3 이 그걸 **진짜 호출로** 잰다.

🔴 대조기들은 **한 줄도 안 고쳤다**(사람 결정 2026-08-18). 분할하면서 회귀망을 같이
   고치면 「분할이 안전한가」를 재는 자와 재어지는 자가 같이 움직인다. 대신 그
   마법을 지키는 자를 따로 둔 것이 이 파일이다.

DB·LLM·네트워크 안 쓴다. 도메인 폴더도 안 읽는다 — 이름과 객체만 다룬다.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.services.gam2_weight_model as W  # noqa: E402

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
    return [m.__name__.rsplit(".", 1)[-1] for m in W._SUBMODULES if name in m.__dict__]


# ══════════════════════════════════════════════════════════════════
print("--- 1) 마법이 걸려 있는가 · 장부가 실제 서브모듈 전부인가")
# ══════════════════════════════════════════════════════════════════
chk("패키지 클래스가 _Proxy", type(W).__name__ == "_Proxy", type(W).__name__)

# 🔴 `_SUBMODULES` 에서 빠진 서브모듈은 **전파 대상에서 조용히 빠진다.** 새 파일을
#    만들고 여기 안 적으면 그 모듈만 옛 값으로 돈다 — 예외가 안 난다.
# ⚠ 이 패키지엔 `__main__` 이 **없다.** 원본에 `if __name__ == "__main__"` 이 없었고
#    실행 진입점은 `run_weight_model.py` 다 — 감리 패키지와 갈리는 자리라 적어 둔다.
_pkg_dir = Path(W.__file__).parent
_on_disk = {p.stem for p in _pkg_dir.glob("*.py") if p.stem != "__init__"}
_in_ledger = {m.__name__.rsplit(".", 1)[-1] for m in W._SUBMODULES}
chk("장부 == 폴더의 서브모듈", _on_disk == _in_ledger,
    f"폴더에만 {sorted(_on_disk - _in_ledger)} · 장부에만 {sorted(_in_ledger - _on_disk)}")
chk("서브모듈 10개", len(W._SUBMODULES) == 10, str(len(W._SUBMODULES)))
chk("__main__ 은 없다(CLI 가 아니다)", not (_pkg_dir / "__main__.py").exists())


# ══════════════════════════════════════════════════════════════════
print("--- 2) 전파 — 밖에서 부르는 이름 전수를 하나씩 갈아끼워 본다")
# ══════════════════════════════════════════════════════════════════
# 🔴 감리 패키지와 달리 「지금 스텁으로 바꾸는 목록」이 없다. 그래서 **밖에서 실제로
#    부르는 이름 전수**를 대상으로 삼는다 — 남이 갈아끼울 수 있는 자리가 그 집합이다.
#    (2026-08-19 grep 실측: `import … as W` 3곳 — gam4_site_select · run_weight_model ·
#     check_loader_health · 이름 import 2곳 — make_parcel_candidates · check_exclusion_state)
_CALLED = (
    "Timer WORK_CRS _detect_admin_key_col _pick_value_cols apply_weight_hitl "
    "as_geodataframe attach_layers build_matrix build_weight_proposal build_weight_set "
    "critic_bootstrap critic_weights data_note define_indicators detect_sparse "
    "diagnose_alpha diagnose_sample_bias find_region_file fingerprint human_weights "
    "normalize_matrix save_weight_proposal save_weight_set slider_from_indicators "
    "slider_pct suggest_radius synthesize"
).split()
_gone = [n for n in _CALLED if not hasattr(W, n)]
chk(f"밖에서 부르는 이름 {len(_CALLED)}개 전부 있음", not _gone, str(_gone))

_orphan = [n for n in _CALLED if not _owners(n)]
chk("전부 소유 서브모듈이 있다", not _orphan, str(_orphan))

_SENTINEL = object()
_broken: list[str] = []
for _n in _CALLED:
    _orig = getattr(W, _n)
    setattr(W, _n, _SENTINEL)
    if getattr(W, _n) is not _SENTINEL:
        _broken.append(f"{_n}(패키지)")
    for _m in W._SUBMODULES:
        if _n in _m.__dict__ and _m.__dict__[_n] is not _SENTINEL:
            _broken.append(f"{_m.__name__.rsplit('.', 1)[-1]}.{_n}")
    setattr(W, _n, _orig)
    for _m in W._SUBMODULES:
        if _n in _m.__dict__ and _m.__dict__[_n] is not _orig:
            _broken.append(f"{_m.__name__.rsplit('.', 1)[-1]}.{_n}(되돌리기)")
chk(f"{len(_CALLED)}개 전부 소유 서브모듈까지 전파되고 되돌아온다", not _broken, str(_broken[:8]))


# ══════════════════════════════════════════════════════════════════
print("--- 3) 패키지 **안쪽** 호출자도 갈아낀 것을 본다")
# ══════════════════════════════════════════════════════════════════
# 🔴 attr 만 보면 §2 로 통과하지만, 실제로 함수를 고르는 것은 **호출하는 서브모듈의
#    전역**이다. 프록시가 없던 순간의 증상이 정확히 이 어긋남이었다
#    (`pipeline_runner` 때의 `run_dir()` 자리).
chk("load_admin_crosswalk 소유 2곳",
    sorted(_owners("load_admin_crosswalk")) == ["admin", "matrix"],
    str(sorted(_owners("load_admin_crosswalk"))))
chk("synthesize 소유 2곳",
    sorted(_owners("synthesize")) == ["diagnostics", "weights"],
    str(sorted(_owners("synthesize"))))
chk("normalize_matrix 소유 2곳",
    sorted(_owners("normalize_matrix")) == ["diagnostics", "matrix"],
    str(sorted(_owners("normalize_matrix"))))
chk("data_note 소유 2곳",
    sorted(_owners("data_note")) == ["hitl", "outputs"],
    str(sorted(_owners("data_note"))))

# 진짜 호출로 재는 자리 — `diagnostics.diagnose_alpha` 는 alpha 마다 `synthesize()` 를
# 부른다. 스텁이 안 닿으면 여기서 **진짜 합성값**이 나온다: 예외가 아니라 값만 다르다.
# (순수 함수라 DB·파일·LLM 을 안 쓴다.)
_orig_syn = W.synthesize
_seen: list[float] = []


def _fake_synthesize(w_human, w_critic, alpha=0.3, sparse_ids=None):
    _seen.append(alpha)
    return {"가": 1.0 - alpha, "나": alpha}


W.synthesize = _fake_synthesize
_got = W.diagnose_alpha({"가": 0.5, "나": 0.5}, {"가": 0.5, "나": 0.5},
                        sparse_ids=set(), alphas=(0.0, 0.4), verbose=False)
chk("diagnostics.diagnose_alpha 가 갈아낀 synthesize 를 부른다", _seen == [0.0, 0.4], str(_seen))
chk("그 결과가 스텁의 값이다",
    _got["weights"] == {"0.0": {"가": 1.0, "나": 0.0}, "0.4": {"가": 0.6, "나": 0.4}},
    str(_got["weights"]))

W.synthesize = _orig_syn
chk("되돌아옴",
    W.synthesize is _orig_syn
    and W.diagnostics.__dict__["synthesize"] is _orig_syn
    and W.weights.__dict__["synthesize"] is _orig_syn)

# 소유가 3곳인 이름도 빠짐없이 도는가(`SPARSE_THRESHOLD` — 희소 판정 임계).
chk("SPARSE_THRESHOLD 소유 3곳",
    sorted(_owners("SPARSE_THRESHOLD")) == ["matrix", "outputs", "state"],
    str(sorted(_owners("SPARSE_THRESHOLD"))))
_orig_st = W.SPARSE_THRESHOLD
W.SPARSE_THRESHOLD = _SENTINEL
chk("SPARSE_THRESHOLD 3곳 전부 전파",
    all(m.__dict__["SPARSE_THRESHOLD"] is _SENTINEL
        for m in W._SUBMODULES if "SPARSE_THRESHOLD" in m.__dict__))
W.SPARSE_THRESHOLD = _orig_st


# ══════════════════════════════════════════════════════════════════
print("--- 4) 모호하면 터진다 (추측해서 한쪽만 고치지 않는다)")
# ══════════════════════════════════════════════════════════════════
_NAME = "_모호_대조용"
_a, _b = object(), object()

# ⓐ 서로 다른 객체를 두 서브모듈이 같은 이름으로 들고 있다 → 어느 쪽인지 알 수 없다
W.state.__dict__[_NAME] = _a
W.matrix.__dict__[_NAME] = _b
try:
    setattr(W, _NAME, 1)
    chk("서로 다른 것을 들고 있으면 raise", False, "조용히 통과했다")
except RuntimeError as e:
    chk("서로 다른 것을 들고 있으면 raise", "state" in str(e) and "matrix" in str(e),
        type(e).__name__)
chk("터진 뒤 아무것도 안 고쳐졌다",
    W.state.__dict__[_NAME] is _a and W.matrix.__dict__[_NAME] is _b)

# ⓑ 같은 객체를 여럿이 들고 있는 것은 모호가 **아니다** — 그게 정상이고 전부 고친다
#    (`SPARSE_THRESHOLD` 3곳 · `synthesize` 2곳이 바로 그 모양이다)
W.matrix.__dict__[_NAME] = _a
setattr(W, _NAME, 7)
chk("같은 것을 들고 있으면 안 터지고 전부 고친다",
    W.state.__dict__[_NAME] == 7 and W.matrix.__dict__[_NAME] == 7)

# ⓒ 아무도 안 가진 이름은 패키지에만 앉는다(전파할 데가 없다)
del W.state.__dict__[_NAME], W.matrix.__dict__[_NAME]
_NEW = "_없던이름_대조용"
setattr(W, _NEW, 3)
chk("아무도 안 가진 이름은 패키지에만", getattr(W, _NEW) == 3 and _owners(_NEW) == [])
delattr(W, _NEW)
delattr(W, _NAME)
chk("대조용 이름 정리됨",
    not hasattr(W, _NAME) and not hasattr(W, _NEW) and _owners(_NAME) == [])


# ══════════════════════════════════════════════════════════════════
print("--- 5) 표면 — 서브모듈이 정의한 것은 전부 패키지에서 **같은 객체**로 보인다")
# ══════════════════════════════════════════════════════════════════
# 🔴 재내보내기를 빠뜨리면 `W.<이름>` 이 `AttributeError` 다. 파이프라인·대조기가 그
#    이름으로 부르므로 「서브모듈에 함수를 더하고 __init__ 을 안 고쳤다」가 그대로
#    구멍이 된다. import 는 안 센다 — 재수출 대상은 **그 모듈이 정의한 것**이다.
# ⚠ `state` 의 config 이름들(`ADM_DONG_SHP` 등)은 **import** 라 여기서 안 세어진다.
#    그래도 재수출한다 — 한 파일이던 시절 `W.STEP2_OUTPUT_DIR` 이 보였기 때문이다.
_missing: list[str] = []
_aliased: list[str] = []
_total = 0
for _m in W._SUBMODULES:
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
        if not hasattr(W, _name):
            _missing.append(f"{_short}.{_name}")
        elif getattr(W, _name) is not _m.__dict__[_name]:
            _aliased.append(f"{_short}.{_name}")

chk(f"재내보내기 누락 0건 (정의 {_total}개)", not _missing, str(_missing[:8]))
chk("패키지가 보는 것 == 서브모듈이 정의한 그 객체", not _aliased, str(_aliased[:8]))

# 한 파일이던 시절 밖에서 보이던 config 이름들도 그대로 보이는가.
_CFG = ("ADM_DONG_SHP ADMIN_CROSSWALK_PATH OPENAI_API_KEY REGION_DATA_DIR "
        "SEARCH_LLM_MODEL SPATIAL_CRS STEP2_OUTPUT_DIR WEIGHT_OUTPUT_DIR").split()
_cfg_gone = [n for n in _CFG if not hasattr(W, n)]
chk(f"config 이름 {len(_CFG)}개도 그대로 보인다", not _cfg_gone, str(_cfg_gone))

print(f"\n{ok + fail}항목 중 {ok} 통과 · {fail} 실패")
sys.exit(1 if fail else 0)
