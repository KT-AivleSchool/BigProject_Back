# -*- coding: utf-8 -*-
"""STEP0·1 대조 — 프로파일러 · 연산 카탈로그 · `full` 배선.

    set PYTHONIOENCODING=utf-8
    python app\\tools\\check_full_step01.py

🔴 **LLM 0회 · DB 0회 · 네트워크 0회 · 디스크 원본 0건.**
   감리 판정(LLM)을 재는 대조기가 아니다. 재는 것은 그 **앞뒤의 결정론 층**이다 —
   ① LLM 이 무엇을 고를 수 있는지(카탈로그 표면) ② 고른 것을 엔진이 어떤 순서로
   돌리는지(`_plan_ops`) ③ 돌다 실패했을 때 **데이터를 죽이는가 살리는가**(레이어 보호)
   ④ 프로파일러가 LLM 에게 무엇을 보여주는지(`_value_dist`) ⑤ `full` 모드가 그 셋을
   실제로 이어 부르는가(`gam2_run_pipeline.run`).

왜 이 셋을 **한 대조기**로 묶는가
   `gam2_audit_ops_catalog`(1,381) · `gam2_profile`(595) · `gam2_run_pipeline`(305) 은
   **같은 구멍 하나**다 — 셋 다 `_PLAN` 의 `0-1` 칸에서만 돌고, 그 칸은 `full` 에만
   있으며, `full` 을 도는 대조기가 하나도 없었다. 파일이 셋이라 구멍도 셋으로 보이지만
   실제로는 **모드 하나가 안 재어지고 있었다.**

🔴 여기서 재는 것은 「돌아가는가」가 아니라 **「틀렸을 때 시끄러운가」**다.
   이 층의 사고는 전부 **예외 없이 값만 틀리는** 모양이었다:
     · `filter_by_value(allowed=['성동구'])` 가 행정동 표를 18행 → **0행** 으로 만들었다
     · 성동구 표기 규칙이 달라 행정동 매칭이 **59%** 였는데 그대로 걸렀다
     · `_value_dist` 가 없던 시절 감리 AI 가 앞 2행만 보고 **폐지 어린이집 91곳**을 배제했다
   그래서 항목의 절반이 「데이터를 안 죽이고 high flag 로 남기는가」를 묻는다.
"""
import io
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402

import app.services.gam2_audit_ops_catalog as C  # noqa: E402
import app.services.gam2_profile as P  # noqa: E402
import app.services.gam2_run_pipeline as RP  # noqa: E402

ok = fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label} {extra}")
    else:
        fail += 1
        print(f"  [!!] {label} {extra}")


def _raises(exc, fn, *a, **kw):
    """`fn` 이 `exc` 로 터지면 (True, 메시지). 안 터지면 (False, 결과)."""
    try:
        return False, fn(*a, **kw)
    except exc as e:
        return True, str(e)


def _ctx(**over):
    c = C.OpContext(facility="흡연부스", region="성동구", domain="재활용").as_dict()
    c.update(over)
    return c


# ══════════════════════════════════════════════════════════════════
# 1) 카탈로그 표면 — LLM 이 고를 수 있는 것의 전부
# ══════════════════════════════════════════════════════════════════
# 🔴 이름을 바꾸면 **예외가 안 난다.** `_plan_ops` 가 미등록 op 를 건너뛰고
#    `unknown_op` flag 로만 남기기 때문이다(그건 LLM 환각을 막는 옳은 설계다).
#    그래서 리팩터링이 op_id 를 갈면 그 정제가 **조용히 안 돈다** — 여기서 시끄럽게 만든다.
print("--- 1) 카탈로그 표면 (op_id · stage · depends_on · applies_to · params)")

EXPECTED = {
    "trim_whitespace": (1, [], ["any"], ["cols"]),
    "crs_transform": (2, [], ["point", "polygon"], ["target_epsg"]),
    "run_geocode": (3, [], ["tabular"], ["address_cols", "out_cols", "type_order"]),
    "reverse_geocode": (4, [], ["point"], ["coord_cols", "out_col"]),
    "spatial_join_admin": (4, [], ["point"], ["code_col", "coord_cols", "name_col"]),
    "filter_by_value": (5, [], ["any"], ["allowed", "col"]),
    "filter_by_address_contains": (5, [], ["any"], ["addr_cols", "contains"]),
    "filter_by_code_prefix": (5, [], ["tabular"], ["col", "prefix"]),
    "filter_by_admin_name": (5, [], ["tabular"], ["col", "min_cover", "region"]),
    "filter_by_join_key": (5, [], ["tabular"], ["key_col", "normalize", "whitelist"]),
    "cast_numeric": (6, [], ["tabular"], ["cols"]),
    "drop_null": (7, ["run_geocode"], ["any"], ["action", "cols"]),
    "dedup": (8, [], ["any"], ["keys"]),
    "validate_geocode": (9, ["run_geocode"], ["point"], ["coord_cols", "target_admin_prefix"]),
    "emit_whitelist": (10, [], ["any"], ["key_col", "name", "normalize"]),
}

chk("등록 op 15개", len(C.REGISTRY) == 15, f"{len(C.REGISTRY)}개")
chk("op_id 집합 일치", set(C.REGISTRY) == set(EXPECTED),
    f"누락 {sorted(set(EXPECTED) - set(C.REGISTRY))} · 추가 {sorted(set(C.REGISTRY) - set(EXPECTED))}")

_bad = []
for oid, (stage, dep, applies, pkeys) in EXPECTED.items():
    o = C.REGISTRY.get(oid)
    if o is None:
        _bad.append(f"{oid}(없음)")
        continue
    got = (o.stage, sorted(o.depends_on), sorted(o.applies_to), sorted(o.params_schema))
    want = (stage, sorted(dep), sorted(applies), sorted(pkeys))
    if got != want:
        _bad.append(f"{oid}: {got} != {want}")
chk("15개 전부 stage·depends_on·applies_to·params 일치", not _bad, str(_bad[:3]))

# 🔴 `describe_all()` 이 프롬프트로 나가는 **전부**다. `stage` 가 여기 없는 것이 설계다 —
#    순서는 엔진이 정하고 LLM 은 「무엇을」만 고른다. `stage` 를 흘리면 LLM 이 순서를
#    지어내기 시작하고, 그러면 `_plan_ops` 의 안정정렬이 무슨 일을 하는지 알 수 없어진다.
_d = C.describe_all()
chk("describe_all 15건", len(_d) == 15, str(len(_d)))
chk("describe_all 5필드", all(set(x) == {"op_id", "applies_to", "depends_on",
                                         "description", "params_schema"} for x in _d),
    str(sorted(_d[0])) if _d else "")
chk("describe_all 에 stage 없음 (순서는 엔진 몫)",
    all("stage" not in x for x in _d))
chk("전 op 에 description 있음", all((x["description"] or "").strip() for x in _d))

# 중복 등록은 조용히 덮어쓰면 **먼저 등록된 구현이 사라진다** — 그래서 raise 다.
_dup, _msg = _raises(ValueError, C.register_op, C.REGISTRY["dedup"])
chk("중복 op_id 는 raise", _dup and "중복 op_id" in _msg, _msg[:40])

# 🔴 **개수를 문서에 적으면 상한다.** 실제로 「원자 op 12개」로 적혀 있는 동안 15개가
#    됐고, 그 문서를 보고 다음 사람은 3개가 없어진 것으로 읽는다. 정본은 `REGISTRY`
#    하나다. 그래서 여기서는 「숫자가 맞는가」가 아니라 **「현재 개수를 주장하는 문구가
#    있는가」**를 묻는다 — 숫자를 맞춰 놓으면 다음 op 를 더할 때 또 틀린다.
#    지난 개수를 **과거로** 적은 것(「그 시점 13→12」)은 상하지 않으므로 봐준다.
_src = Path(C.__file__).read_text(encoding="utf-8")
_claims = []
for _i, _line in enumerate(_src.splitlines(), 1):
    if re.search(r"op\s*\d+\s*개", _line):
        _claims.append(f"{_i}: {_line.strip()[:60]}")
    elif re.search(r"\d+\s*→\s*\d+", _line) and "그 시점" not in _line:
        _claims.append(f"{_i}: {_line.strip()[:60]}")
chk("정본에 현재 op 개수를 주장하는 문구가 없다 (숫자는 `len(REGISTRY)` 로 센다)",
    not _claims, str(_claims[:2]))


# ══════════════════════════════════════════════════════════════════
# 2) `_plan_ops` — LLM 이 고른 것을 어떤 순서로 돌리는가
# ══════════════════════════════════════════════════════════════════
print("--- 2) 계획 수립 (같은 op 2회 · 안정정렬 · depends_on · 미등록)")

# 같은 op 를 두 번 부르는 것은 정상이다(컬럼별로 다른 params). dict 로 뭉개면 하나가 사라진다.
_plan, _unk = C._plan_ops([
    {"op_id": "dedup", "params": {"keys": ["a"]}},
    {"op_id": "dedup", "params": {"keys": ["b"]}},
])
chk("같은 op 2회가 살아남는다", len(_plan) == 2, str(len(_plan)))
chk("두 params 가 각각 보존", [o["params"]["keys"] for o in _plan] == [["a"], ["b"]],
    str([o["params"] for o in _plan]))

# stage 로 재배치하되 **같은 stage 안에서는 LLM 순서(ai_seq)를 지킨다**(안정정렬).
_plan, _ = C._plan_ops([
    {"op_id": "dedup", "params": {}},              # 8
    {"op_id": "trim_whitespace", "params": {}},    # 1
    {"op_id": "filter_by_value", "params": {"col": "z"}},   # 5
    {"op_id": "filter_by_code_prefix", "params": {"col": "y"}},  # 5
])
chk("stage 오름차순 재배치",
    [o["op_id"] for o in _plan] == ["trim_whitespace", "filter_by_value",
                                    "filter_by_code_prefix", "dedup"],
    str([o["op_id"] for o in _plan]))
chk("같은 stage(5) 안에서는 LLM 순서 보존",
    [o["ai_seq"] for o in _plan if C.REGISTRY[o["op_id"]].stage == 5] == [2, 3],
    str([o["ai_seq"] for o in _plan]))

# 미등록 op(LLM 환각)는 데이터셋을 죽이지 않고 **건너뛰고 보고**한다.
_plan, _unk = C._plan_ops([
    {"op_id": "지어낸_op", "params": {}},
    {"op_id": "dedup", "params": {"keys": ["a"]}},
])
chk("미등록 op 는 건너뛴다", [o["op_id"] for o in _plan] == ["dedup"], str(_plan))
chk("미등록 op 를 이름으로 보고", _unk == ["지어낸_op"], str(_unk))

# `depends_on` 은 **같은 계획에 그 의존이 있을 때만** 건다.
# (좌표가 내장된 데이터는 run_geocode 없이 validate_geocode 만 쓸 수 있다)
_no_raise, _ = _raises(ValueError, C._plan_ops, [
    {"op_id": "validate_geocode", "params": {}},
])
chk("의존이 계획에 없으면 raise 안 함 (조건부 면제)", not _no_raise)

# 🔴 **잠든 방어다.** 지금 stage 로는 위반이 만들어질 수 없다 —
#    run_geocode(3) < drop_null(7) < validate_geocode(9) 라 안정정렬이 언제나
#    의존을 앞에 놓는다. 즉 이 `raise` 는 **한 번도 안 켜진다.**
#    그렇다고 없는 방어는 아니다: 리팩터링이 stage 하나만 바꾸면 그날 켜진다.
#    그래서 stage 를 **일부러 뒤집어** 기전을 치고 되돌린다(BaseOp 는 가변 dataclass).
_stage_ok = all(C.REGISTRY[a].stage < C.REGISTRY[b].stage
                for a, b in [("run_geocode", "drop_null"),
                             ("run_geocode", "validate_geocode")])
chk("현재 stage 로는 위반이 발생 불가 (= 이 방어는 잠들어 있다)", _stage_ok,
    f"run_geocode={C.REGISTRY['run_geocode'].stage} "
    f"drop_null={C.REGISTRY['drop_null'].stage} "
    f"validate_geocode={C.REGISTRY['validate_geocode'].stage}")

_orig_stage = C.REGISTRY["run_geocode"].stage
try:
    C.REGISTRY["run_geocode"].stage = 99  # 의존을 맨 뒤로 민다
    _hit, _msg = _raises(ValueError, C._plan_ops, [
        {"op_id": "run_geocode", "params": {}},
        {"op_id": "validate_geocode", "params": {}},
    ])
    chk("stage 를 뒤집으면 순서 위반이 raise", _hit and "순서 위반" in _msg, _msg[:40])
    chk("에러가 어느 op 가 어느 뒤에 와야 하는지 말한다",
        _hit and "validate_geocode" in _msg and "run_geocode" in _msg)
finally:
    C.REGISTRY["run_geocode"].stage = _orig_stage
chk("stage 원복됨", C.REGISTRY["run_geocode"].stage == _orig_stage,
    str(C.REGISTRY["run_geocode"].stage))

# 🔴 시그니처가 본문과 어긋나면 **호출자가 조용히 틀린다.** 여기 `-> list[dict]` 라고
#    적혀 있던 시절, 그 주석만 읽고 `for o in _plan_ops(...)` 를 쓰면 2튜플을 도는 것이라
#    첫 회전에 `list` 가 나오고 두 번째에 `list[str]` 이 나온다 — 예외는 한참 뒤에 난다.
#    미등록 op 보고(`unknown`)는 **두 번째 원소로만 존재**하므로 주석이 그걸 감추면
#    LLM 환각이 보고되는 자리가 통째로 안 보인다.
import inspect  # noqa: E402

_ann = str(inspect.signature(C._plan_ops).return_annotation)
chk("`_plan_ops` 반환 주석이 2튜플이라고 말한다", "tuple" in _ann.lower(), _ann)
chk("그리고 실제로 2튜플이다", isinstance(C._plan_ops([]), tuple) and len(C._plan_ops([])) == 2,
    str(type(C._plan_ops([]))))


# ══════════════════════════════════════════════════════════════════
# 3) `execute` 계약
# ══════════════════════════════════════════════════════════════════
print("--- 3) execute 계약 (ctx 선검증 · unknown flag · OpLog)")

_df = pd.DataFrame({"a": [1, 2, 2], "b": ["x", "y", "y"]})

# 🔴 ctx 검증은 **op 를 하나라도 돌리기 전에** 한다. 뒤에 하면 절반 정제된 df 가 남는다.
_hit, _msg = _raises(ValueError, C.execute, _df, {"cleaning_ops": []},
                     {"facility": "흡연부스", "region": "", "domain": "d",
                      "adm_shp_path": "/x"})
chk("빈 ctx 값은 실행 전에 raise", _hit and "region" in _msg, _msg[:50])
chk("에러가 필수 키를 다 알려준다", _hit and all(k in _msg for k in C.CTX_REQUIRED_KEYS),
    str(C.CTX_REQUIRED_KEYS))

_hit, _msg = _raises(ValueError, C.execute, _df, {"cleaning_ops": []},
                     {"facility": "f", "domain": "d", "adm_shp_path": "/x"})
chk("키 자체가 없어도 raise", _hit and "region" in _msg, _msg[:40])

# OpContext 를 그대로 넘겨도 된다(호출부가 dict 로 풀지 않게).
_out, _flags, _logs = C.execute(_df.copy(), {"cleaning_ops": [
    {"op_id": "dedup", "params": {"keys": ["a"]}},
]}, C.OpContext(facility="흡연부스", region="성동구", domain="재활용"))
chk("OpContext 도 받는다", len(_out) == 2 and not _flags, f"{len(_out)}행")
chk("OpLog 1건", len(_logs) == 1)
chk("OpLog 행수 기록 (3→2)", (_logs[0].rows_before, _logs[0].rows_after) == (3, 2),
    f"{_logs[0].rows_before}→{_logs[0].rows_after}")
chk("OpLog params 원본 보존", _logs[0].params == {"keys": ["a"]}, str(_logs[0].params))
chk("elapsed_sec 소수 2자리", _logs[0].elapsed_sec == round(_logs[0].elapsed_sec, 2))

_out, _flags, _logs = C.execute(_df.copy(), {"cleaning_ops": [
    {"op_id": "없는op", "params": {}},
    {"op_id": "dedup", "params": {"keys": ["a"]}},
]}, _ctx())
chk("unknown_op 이 flag 로 나온다", _flags and _flags[0].type == "unknown_op",
    str([f.type for f in _flags]))
chk("unknown_op 은 high · row_id=-1",
    _flags[0].severity == "high" and _flags[0].row_id == -1,
    f"{_flags[0].severity}/{_flags[0].row_id}")
chk("unknown_op 이 op 이름을 담는다", _flags[0].raw_text == "없는op", str(_flags[0].raw_text))
chk("나머지 op 는 그대로 돈다", len(_out) == 2 and len(_logs) == 1, f"{len(_out)}행")

# `cleaning_ops` 가 없어도(감리가 정제 불필요로 판정) 죽지 않는다.
_out, _flags, _logs = C.execute(_df.copy(), {}, _ctx())
chk("cleaning_ops 없으면 무변경 통과", len(_out) == 3 and not _flags and not _logs)


# ══════════════════════════════════════════════════════════════════
# 4) 순수 op 결정론
# ══════════════════════════════════════════════════════════════════
print("--- 4) 순수 pandas op (같은 입력 → 같은 출력)")

# trim_whitespace — 🔴 눈에 안 보이는 것들이 조인 키를 깬다.
_t = pd.DataFrame({"c": ["  마장동   ", "성수\u00a01가", "\ufeff왕십리", "a  b", None, 7]})
_out, _f = C.REGISTRY["trim_whitespace"].run(_t.copy(), {"cols": ["c"]}, _ctx())
chk("trim: 양끝 공백", _out["c"][0] == "마장동", repr(_out["c"][0]))
chk("trim: NBSP(\\u00a0) → 보통 공백", _out["c"][1] == "성수 1가", repr(_out["c"][1]))
chk("trim: BOM(\\ufeff) 제거", _out["c"][2] == "왕십리", repr(_out["c"][2]))
chk("trim: 중복 공백 1칸", _out["c"][3] == "a b", repr(_out["c"][3]))
# 🔴 `astype(str)` 이 결측을 **문자열 'nan' 으로 되살린다** — 그 상태로 조인 키에 쓰면
#    「결측」이 아니라 「nan 이라는 값」이 되어 `dropna` 도 `isna()` 도 못 잡는다.
#    그래서 op 가 다시 결측으로 되돌린다. ⚠ 되돌린 자리는 `None` 이 아니라 NaN 으로
#    보인다(pandas 가 object 열의 None 을 그렇게 렌더한다) — **표현이 아니라 결측성**을 묻는다.
chk("trim: 문자열 'nan' 이 결측으로 되돌아온다", pd.isna(_out["c"][4]), repr(_out["c"][4]))
chk("trim: 원본 df 불변", _t["c"][0] == "  마장동   ")

# cols 를 안 주면 텍스트 컬럼 전부. 숫자 컬럼은 안 건드린다.
_t2 = pd.DataFrame({"s": [" a "], "n": [1]})
_out, _ = C.REGISTRY["trim_whitespace"].run(_t2.copy(), {}, _ctx())
chk("trim: cols 미지정이면 텍스트 컬럼만", _out["s"][0] == "a" and _out["n"][0] == 1,
    f"{_out['s'][0]!r}/{_out['n'][0]!r}")

# cast_numeric — 🔴 콤마 붙은 숫자·텍스트 서식. 못 읽는 값은 0 이 아니라 NaN 이다.
_out, _ = C.REGISTRY["cast_numeric"].run(
    pd.DataFrame({"v": ["1,234", "112.00", "미상", None]}), {"cols": ["v"]}, _ctx())
chk("cast: 콤마 제거", _out["v"][0] == 1234, str(_out["v"][0]))
chk("cast: 텍스트 서식 실수", _out["v"][1] == 112.0, str(_out["v"][1]))
chk("cast: 못 읽는 값은 NaN (0 이 아니다)", pd.isna(_out["v"][2]), str(_out["v"][2]))

# drop_null — 기본은 **경고만**. 자동 삭제는 명시할 때만.
_n = pd.DataFrame({"x": [1, None, 3]})
_out, _f = C.REGISTRY["drop_null"].run(_n.copy(), {"cols": ["x"]}, _ctx())
chk("drop_null 기본은 삭제 안 함", len(_out) == 3, f"{len(_out)}행")
chk("drop_null 기본은 mid flag", [f.type for f in _f] == ["null_required"]
    and _f[0].severity == "mid", str([(f.type, f.severity) for f in _f]))
chk("drop_null flag 가 행 번호를 짚는다", _f[0].row_id == 1, str(_f[0].row_id))
_out, _f = C.REGISTRY["drop_null"].run(_n.copy(), {"cols": ["x"], "action": "drop"}, _ctx())
chk("action=drop 이면 실제로 지운다", len(_out) == 2 and not _f, f"{len(_out)}행")

# dedup
_out, _ = C.REGISTRY["dedup"].run(_df.copy(), {"keys": ["a"]}, _ctx())
chk("dedup 키 기준", len(_out) == 2, f"{len(_out)}행")

# filter_by_value
_v = pd.DataFrame({"g": ["성동구", " 성동구 ", "용산구"]})
_out, _f = C.REGISTRY["filter_by_value"].run(_v.copy(), {"col": "g", "allowed": ["성동구"]}, _ctx())
chk("filter_by_value: 값 비교 전에 strip", len(_out) == 2 and not _f, f"{len(_out)}행")

# filter_by_address_contains — 여러 주소 컬럼의 **OR** 이고 정규식이 아니다.
_a = pd.DataFrame({"도로명": ["성동구 왕십리로", "용산구 이태원로"],
                   "지번": ["용산구 후암동", "성동구 마장동"]})
_out, _ = C.REGISTRY["filter_by_address_contains"].run(
    _a.copy(), {"addr_cols": ["도로명", "지번"], "contains": "성동구"}, _ctx())
chk("filter_by_address_contains: 컬럼 간 OR", len(_out) == 2, f"{len(_out)}행")
_out, _ = C.REGISTRY["filter_by_address_contains"].run(
    pd.DataFrame({"z": ["a.c", "abc"]}), {"addr_cols": ["z"], "contains": "a.c"}, _ctx())
chk("filter_by_address_contains: 정규식 아님 (regex=False)", len(_out) == 1, f"{len(_out)}행")

# filter_by_code_prefix
_out, _ = C.REGISTRY["filter_by_code_prefix"].run(
    pd.DataFrame({"c": ["11200510", "11170510"]}), {"col": "c", "prefix": "11200"}, _ctx())
chk("filter_by_code_prefix", len(_out) == 1, f"{len(_out)}행")


# ══════════════════════════════════════════════════════════════════
# 5) 생산자·소비자 (whitelist)
# ══════════════════════════════════════════════════════════════════
print("--- 5) emit_whitelist(생산) ↔ filter_by_join_key(소비)")

# 🔴 params 키 이름이 **서로 다르다**: 생산은 `name`, 소비는 `whitelist`.
#    같은 문자열인데 키가 달라서, 한쪽 이름으로 짜면 `missing_whitelist` 가 뜬다
#    (= 조용히 필터가 안 걸린 채 통과하는 가짜 초록불).
chk("생산자 params 는 name", "name" in C.REGISTRY["emit_whitelist"].params_schema)
chk("소비자 params 는 whitelist", "whitelist" in C.REGISTRY["filter_by_join_key"].params_schema)
chk("stage: 생산(10) 이 소비(5) 보다 늦다 — 같은 실행 안에서는 못 잇는다",
    C.REGISTRY["emit_whitelist"].stage > C.REGISTRY["filter_by_join_key"].stage,
    f"{C.REGISTRY['emit_whitelist'].stage} > {C.REGISTRY['filter_by_join_key'].stage}")

_c = _ctx()
_src = pd.DataFrame({"k": ["00001", "00002", "", "00002"]})
_out, _f = C.REGISTRY["emit_whitelist"].run(
    _src.copy(), {"name": "동코드", "key_col": "k", "normalize": "none"}, _c)
chk("emit: df 를 변형하지 않는다 (부작용만)", len(_out) == 4 and not _f, f"{len(_out)}행")
chk("emit: ctx 에 적힌다", _c["whitelists"]["동코드"] == ["00001", "00002"],
    str(_c["whitelists"]))
chk("emit: 빈 키는 안 싣는다", "" not in _c["whitelists"]["동코드"])

_hit = pd.DataFrame({"k": [1.0, 2.0, 9.0]})
_out, _f = C.REGISTRY["filter_by_join_key"].run(
    _hit.copy(), {"key_col": "k", "whitelist": "동코드", "normalize": "zfill5"}, _c)
chk("join_key: zfill5 가 '1.0' → '00001'", len(_out) == 2, f"{len(_out)}행")
chk("join_key: 미매칭은 low flag (막지 않는다)",
    not _f or _f[0].severity == "low", str([(f.type, f.severity) for f in _f]))

_c2 = _ctx()
_c2["whitelists"] = {"이름": ["가나(구)", "다라"]}
_out, _ = C.REGISTRY["filter_by_join_key"].run(
    pd.DataFrame({"k": ["가나", "다라", "마바"]}),
    {"key_col": "k", "whitelist": "이름", "normalize": "strip_paren"}, _c2)
chk("join_key: strip_paren", len(_out) == 2, f"{len(_out)}행")


# ══════════════════════════════════════════════════════════════════
# 6) 레이어 보호 — 실패했을 때 데이터를 죽이는가 살리는가
# ══════════════════════════════════════════════════════════════════
# 🔴 이 대조기의 **핵심**이다. 여기 넷은 전부 실제로 데이터를 0행으로 만들었거나
#    만들 뻔한 자리다. 옳은 답은 「원본 df 그대로 + high flag」이지 「0행」이 아니다 —
#    0행은 「그 지역에 시설이 없다」는 **거짓 진술**이 되어 하류로 흐른다(원칙 4).
print("--- 6) 레이어 보호 4종 (원본 df 보존 + high flag)")

_g = pd.DataFrame({"g": ["종로구", "중구"]})
_out, _f = C.REGISTRY["filter_by_value"].run(_g.copy(), {"col": "g", "allowed": ["성동구"]}, _ctx())
chk("① 허용값 0건 매칭 → df 안 죽인다", len(_out) == 2, f"{len(_out)}행")
chk("① filter_no_match · high · row_id=-1",
    [f.type for f in _f] == ["filter_no_match"] and _f[0].severity == "high"
    and _f[0].row_id == -1, str([(f.type, f.severity, f.row_id) for f in _f]))
chk("① 실제 값 예시를 알려준다 (사람이 고칠 수 있게)",
    "종로구" in (_f[0].raw_text or ""), (_f[0].raw_text or "")[:60])

# 🔴 admin 계열 3종은 `_admin_names_of` 가 **크로스워크 CSV 를 디스크에서 읽는다.**
#    스텁하지 않으면 판정이 「그 파일이 있느냐」에 딸려간다 — 파일이 없는 기계에서
#    가짜 빨간불, 있는 기계에서 다른 결과. 대조기는 환경을 재면 안 된다.
_orig_names = C._admin_names_of
try:
    C._admin_names_of = lambda region: []
    _out, _f = C.REGISTRY["filter_by_admin_name"].run(
        pd.DataFrame({"d": ["마장동"]}), {"col": "d"}, _ctx())
    chk("② 코드표를 못 찾으면 → df 안 죽인다", len(_out) == 1, f"{len(_out)}행")
    chk("② admin_name_map_missing · high",
        [f.type for f in _f] == ["admin_name_map_missing"] and _f[0].severity == "high",
        str([(f.type, f.severity) for f in _f]))

    C._admin_names_of = lambda region: ["마장동", "사근동", "행당제1동"]
    # 표기 규칙이 어긋나 6행 중 1행만 맞는 상황(성동구 실측 59% 사고의 축소판).
    _low = pd.DataFrame({"d": ["마장동", "A동", "B동", "C동", "D동", "E동"]})
    _out, _f = C.REGISTRY["filter_by_admin_name"].run(_low.copy(), {"col": "d"}, _ctx())
    chk("③ 일치율이 min_cover 미만이면 → df 안 죽인다", len(_out) == 6, f"{len(_out)}행")
    chk("③ admin_name_low_match · high",
        [f.type for f in _f] == ["admin_name_low_match"] and _f[0].severity == "high",
        str([(f.type, f.severity) for f in _f]))
    chk("③ 일치율과 미매칭 예를 같이 알려준다",
        "17%" in (_f[0].raw_text or "") and "A동" in (_f[0].raw_text or ""),
        (_f[0].raw_text or "")[:70])
    # min_cover 를 낮추면 통과하고, 이번엔 제외분이 low flag 로만 남는다.
    _out, _f = C.REGISTRY["filter_by_admin_name"].run(
        _low.copy(), {"col": "d", "min_cover": 0.1}, _ctx())
    chk("③ min_cover 를 낮추면 실제로 거른다", len(_out) == 1, f"{len(_out)}행")
    chk("③ 제외분은 low flag (막지 않는다)",
        [f.type for f in _f] == ["admin_name_dropped"] and _f[0].severity == "low",
        str([(f.type, f.severity) for f in _f]))

finally:
    C._admin_names_of = _orig_names
chk("`_admin_names_of` 원복됨", C._admin_names_of is _orig_names)

# ══════════════════════════════════════════════════════════════════
# 7) `_norm_dong` — 표기 규칙이 두 벌인데 한쪽만 아는 사고
# ══════════════════════════════════════════════════════════════════
# 🔴 스텁 밖이다. 이 함수는 디스크도 크로스워크도 안 읽는 **순수 문자열 규칙**이라
#    위 `try/finally` 안에 두면 「스텁이 있어야 도는 것」으로 읽힌다.
print("--- 7) `_norm_dong` — 법정 표기 ↔ 약식 표기")
_pairs = [("행당제1동", "행당1동"), ("성수1가제1동", "성수1가1동"),
          ("왕십리제2동", "왕십리2동"), ("금호2·3가동", "금호2.3가동"),
          ("금호2ㆍ3가동", "금호2.3가동"), ("마장동   ", "마장동"),
          ("  마 장 동", "마장동")]
_nb = [(a, C._norm_dong(a), b) for a, b in _pairs if C._norm_dong(a) != C._norm_dong(b)]
chk("법정 ↔ 약식 표기가 같은 값으로 접힌다", not _nb, str(_nb[:3]))
# 🔴 규칙(`제` 뒤 숫자를 지운다)은 **지명의 '제'까지 깎는다** — `홍제1동` → `홍1동`.
#    그래도 예외 목록을 안 만드는 이유는 정규화를 **양쪽에 같이** 걸기 때문이다:
#    표도 크로스워크도 `홍제1동` 이면 둘 다 `홍1동` 이 되어 짝이 유지된다.
#    (예외 목록은 다음 지역에서 또 틀린다 — 지명은 우리가 셀 수 있는 집합이 아니다)
chk("지명의 '제'까지 깎인다 — 그래도 같은 문자열은 같은 값이 된다",
    C._norm_dong("홍제1동") == "홍1동" == C._norm_dong(" 홍제1동 "),
    C._norm_dong("홍제1동"))
# 🔴 **그래서 못 붙는 짝이 있다.** 이건 통과 항목이 아니라 **남은 한계의 실측**이다 —
#    '제'로 시작하는 지명의 법정(`홍제제1동`) ↔ 약식(`홍제1동`) 변종은
#    `홍1동` ↔ `홍제1동` 으로 갈려 안 붙는다. 성동구엔 그런 동이 없어 안 걸렸다.
#    적어두는 이유: 다음 지역에서 매칭률이 떨어지면 **여기부터 본다**(원칙 4·5).
chk("남은 한계 — '제'로 시작하는 지명의 법정↔약식은 안 붙는다 (실측으로 남긴다)",
    C._norm_dong("홍제제1동") != C._norm_dong("홍제1동"),
    f"{C._norm_dong('홍제제1동')} != {C._norm_dong('홍제1동')}")

print("--- 6-2) 생산자 없는 소비 (missing_whitelist)")
_c3 = _ctx()
_out, _f = C.REGISTRY["filter_by_join_key"].run(
    pd.DataFrame({"k": ["a", "b"]}), {"key_col": "k", "whitelist": "없는목록"}, _c3)
chk("④ 생산자 없으면 → df 안 죽인다", len(_out) == 2, f"{len(_out)}행")
chk("④ missing_whitelist · high",
    [f.type for f in _f] == ["missing_whitelist"] and _f[0].severity == "high",
    str([(f.type, f.severity) for f in _f]))
chk("④ 보유 목록을 알려준다 (오타인지 미생산인지 가르게)",
    "보유" in (_f[0].raw_text or ""), (_f[0].raw_text or "")[:60])


# ══════════════════════════════════════════════════════════════════
# 8) 명시적 실패 (원칙 1) — 추측해서 진행하지 않는다
# ══════════════════════════════════════════════════════════════════
print("--- 8) 없는 컬럼·빠진 params 는 조용히 넘어가지 않는다")

_hit, _msg = _raises(KeyError, C.REGISTRY["filter_by_value"].run,
                     pd.DataFrame({"other": [1]}), {"col": "없는컬럼", "allowed": ["x"]}, _ctx())
chk("없는 컬럼 → KeyError", _hit, str(_msg)[:40])
chk("에러가 실제 컬럼 목록을 보여준다", _hit and "other" in str(_msg), str(_msg)[:70])

_hit, _msg = _raises(ValueError, C.REGISTRY["filter_by_value"].run,
                     pd.DataFrame({"g": [1]}), {"col": "g"}, _ctx())
chk("필수 params 누락 → ValueError", _hit and "allowed" in str(_msg), str(_msg)[:50])
chk("에러가 기대 스키마를 동봉한다", _hit and "스키마" in str(_msg))

_hit, _msg = _raises(ValueError, C.REGISTRY["filter_by_value"].run,
                     pd.DataFrame({"g": [1]}), {"col": "g", "allowed": []}, _ctx())
chk("빈 리스트도 누락으로 본다", _hit, str(_msg)[:40])


# ══════════════════════════════════════════════════════════════════
# 9) 프로파일러 — 감리 AI 가 무엇을 보는가
# ══════════════════════════════════════════════════════════════════
# 🔴 여기가 틀리면 **LLM 이 못 본 것을 안 본다.** 실제 사고: `sample_rows` 는 앞 2행뿐인데
#    어린이집 `운영현황` 의 '재개' 는 30번째 행에 처음 나온다 → 감리 AI 가 필터를 못 만들어
#    **폐지 어린이집 91곳까지 배제**했다. `_value_dist` 는 그 사고의 처치다.
print("--- 9) 프로파일러 (_value_dist · 탐지기 · 번호 부여)")

_v = pd.DataFrame({"운영현황": ["정상"] * 3835 + ["폐지"] * 5504 + ["재개"] * 75 + ["휴지"] * 66})
_d = P._value_dist(_v)
chk("운영현황 4값 전부 보인다 (앞 2행만 보면 '정상' 뿐)",
    set(_d["운영현황"]["값"]) == {"정상", "폐지", "재개", "휴지"}, str(sorted(_d["운영현황"]["값"])))
chk("건수까지 준다 — 재개 75", _d["운영현황"]["값"]["재개"] == 75, str(_d["운영현황"]["값"]["재개"]))
chk("고유수", _d["운영현황"]["고유수"] == 4, str(_d["운영현황"]["고유수"]))

chk("고유값이 너무 많으면 안 싣는다 (프롬프트 비대화)",
    "id" not in P._value_dist(pd.DataFrame({"id": [f"v{i}" for i in range(P.CATEGORY_MAX_UNIQUE + 1)]})),
    f"CATEGORY_MAX_UNIQUE={P.CATEGORY_MAX_UNIQUE}")
# 🔴 자르지 않고 **컬럼째 뺀다.** 자르면 잘린 값이 진짜 값인 척 프롬프트에 들어간다(원칙 4).
_long = pd.DataFrame({"비고": ["짧다", "가" * (P.CATEGORY_VAL_MAXLEN + 1)]})
chk("긴 값이 하나라도 있으면 컬럼째 제외 (자르지 않는다)",
    "비고" not in P._value_dist(_long), str(sorted(P._value_dist(_long))))
chk("숫자스러운 컬럼은 범주가 아니다",
    "n" not in P._value_dist(pd.DataFrame({"n": ["1", "2", "3,000"]})))

chk("_is_numericish: 콤마 포함 숫자", P._is_numericish(["1,234", "5"]))
chk("_is_numericish: 8할 미만이면 아님", not P._is_numericish(["1", "가", "나", "다", "라"]))
chk("_is_numericish: 빈 목록은 True", P._is_numericish([]))

# 🔴 `url`·`코드` 가 든 컬럼명이 주소로 잡히면 지오코딩이 엉뚱한 컬럼을 친다.
chk("주소 컬럼 탐지", P._detect_addr_cols(["소재지주소", "설치위치"]) == ["소재지주소", "설치위치"],
    str(P._detect_addr_cols(["소재지주소", "설치위치"])))
chk("오탐 제외 — 홈페이지주소·주소코드",
    P._detect_addr_cols(["홈페이지주소", "주소코드", "지번주소"]) == ["지번주소"],
    str(P._detect_addr_cols(["홈페이지주소", "주소코드", "지번주소"])))

# dataset_id — 🔴 NFC 정규화. macOS 가 만든 NFD 파일명이면 같은 이름이 둘로 갈린다.
chk("_assign_id: 언더스코어 앞", P._assign_id("07_버스정류소.csv") == "07",
    P._assign_id("07_버스정류소.csv"))
import unicodedata  # noqa: E402

chk("_assign_id: NFD 파일명도 NFC 로 접힌다",
    P._assign_id(unicodedata.normalize("NFD", "가나.csv"))
    == P._assign_id(unicodedata.normalize("NFC", "가나.csv")))

with tempfile.TemporaryDirectory() as _td:
    for _n in ["03_다.csv", "01_가.csv", "02_나.xlsx", "_임시.csv", ".숨김.csv", "메모.txt"]:
        Path(_td, _n).write_text("a,b\n1,2\n", encoding="utf-8")
    _files = [os.path.basename(f) for f in P.list_dataset_files(_td)]
    chk("list_dataset_files: 가나다순", _files == ["01_가.csv", "02_나.xlsx", "03_다.csv"], str(_files))
    chk("list_dataset_files: `_`·`.` 로 시작하면 제외", "_임시.csv" not in _files)
    chk("list_dataset_files: 데이터 확장자만", "메모.txt" not in _files)

    _prof = P.profile_folder(_td)
    # 🔴 번호는 **파일명 가나다순 일련번호**다. 파일명 앞자리가 아니다 —
    #    앞 번호로 끼어들면 뒤가 전부 밀린다(업로드 API 의 `renumbered` 가 그 사고다).
    #
    # 🔴 여기 `02_나.xlsx` 는 **내용이 CSV 인 가짜 xlsx** 다 — pandas 가 못 읽는다.
    #    그때 `did` 는 `enumerate(data_paths, 1)` 에서 오고 실패는 `continue` 라,
    #    **번호는 이미 쓰였고 결과에서만 빠진다.** 즉 `03` 이 `02` 로 당겨지지 않고
    #    결번이 남는다. 이게 옳다 — 당겨지면 이미 돌린 감리·`reviewed.json` 이
    #    가리키는 번호가 딴 파일이 된다(프로파일러 자신도 그 경고를 찍는다:
    #    「파일을 추가/삭제하면 뒤 번호가 밀립니다」). 못 읽은 것은 조용히 안 넘어간다 —
    #    `[profile] 건너뜀 …` 이 stdout 에 남는다.
    chk("profile_folder: 못 읽는 파일은 번호를 쓰고 결과에서만 빠진다 (뒤 번호가 안 밀린다)",
        sorted(_prof) == ["01", "03"], str(sorted(_prof)))
    chk("profile_file 14키", len(_prof["01"]) == 14, str(len(_prof["01"])))
    chk("row_count 는 헤더 제외", _prof["01"]["row_count"] == 1, str(_prof["01"]["row_count"]))
    chk("sample_rows 최대 2행", len(_prof["01"]["sample_rows"]) <= 2)

    _sp = P.save_profiles(_prof, os.path.join(_td, "out", "profiles.json"))
    chk("save_profiles: 폴더까지 만든다", os.path.isfile(_sp))
    chk("save_profiles: 한글 그대로 (ensure_ascii=False)",
        "가" in Path(_sp).read_text(encoding="utf-8"))

_hit, _msg = _raises(FileNotFoundError, P.profile_folder, os.path.join(tempfile.gettempdir(), "없는폴더_zzz"))
chk("없는 폴더는 조용히 {} 가 아니라 FileNotFoundError", _hit, str(_msg)[:40])


# ══════════════════════════════════════════════════════════════════
# 10) `full` 배선 — STEP0·0.5·1·2 를 실제로 이어 부르는가
# ══════════════════════════════════════════════════════════════════
# 🔴 LLM 을 부르지 않는다. `gam2_run_pipeline` 은 `A.<이름>` 을 **호출 시점에** 찾으므로
#    모듈 attr 를 갈아끼우면 배선만 잴 수 있다. 갈아끼우기 전에 **13개 정본 이름이
#    실재하는지 먼저 본다** — 없는 이름을 스텁하면 오타가 통과하는 가짜 초록불이 된다.
print("--- 10) full 배선 (LLM 0회 · A 모듈 attr 스텁)")

_NAMES = ["set_domain", "build_fixtures", "resolve_facility", "resolve_facility_mock",
          "save_facility_inference", "require_region", "MockLLM", "RealLLM",
          "run_harness", "report", "save_results", "enrich_with_search", "_DOMAIN"]
_missing = [n for n in _NAMES if not hasattr(RP.A, n)]
chk("정본 이름 13개 실재", not _missing, str(_missing))

_calls: list = []
_orig = {n: getattr(RP.A, n) for n in _NAMES if n != "_DOMAIN"}


def _rec(name, ret=None):
    def _f(*a, **kw):
        _calls.append((name, a, kw))
        return ret
    return _f


class _BoomLLM:
    def __init__(self):
        raise AssertionError("RealLLM 이 만들어졌다 — mock 인데 실 LLM 을 붙였다")


def _drive(**kw):
    _calls.clear()
    RP.A.set_domain = _rec("set_domain")
    RP.A.build_fixtures = _rec("build_fixtures", [{"dataset_id": "01"}])
    RP.A.resolve_facility = _rec("resolve_facility", {"facility": "X", "region": "성동구"})
    RP.A.resolve_facility_mock = _rec("resolve_facility_mock",
                                      {"facility": "재활용정거장", "region": "성동구"})
    RP.A.save_facility_inference = _rec("save_facility_inference", "/tmp/fac.json")
    RP.A.MockLLM = _rec("MockLLM", object())
    RP.A.RealLLM = _BoomLLM
    RP.A.run_harness = _rec("run_harness", ({}, kw.pop("_raw", {})))
    RP.A.report = _rec("report")
    RP.A.save_results = _rec("save_results", "/tmp/audit.json")
    RP.A.enrich_with_search = _rec("enrich_with_search", "/tmp/enriched.json")
    buf = io.StringIO()
    with redirect_stdout(buf):
        paths = RP.run("datasets/재활용", "성동구 재활용정거장 부지 선정", mock=True, **kw)
    return paths, [c[0] for c in _calls], buf.getvalue()


try:
    _p, _seq, _log = _drive()
    chk("mock 이면 resolve_facility_mock 을 쓴다",
        "resolve_facility_mock" in _seq and "resolve_facility" not in _seq, str(_seq))
    chk("mock 이면 RealLLM 을 안 만든다 (만들면 AssertionError)", "MockLLM" in _seq)
    # 🔴 감리는 수백 초다. 그 뒤에 저장하면 감리가 죽었을 때 시설 확정 결과까지 사라진다.
    chk("시설 확정 저장이 감리(run_harness) **앞**",
        _seq.index("save_facility_inference") < _seq.index("run_harness"), str(_seq))
    chk("STEP0 → 0.5 → 1 순서",
        _seq.index("build_fixtures") < _seq.index("resolve_facility_mock")
        < _seq.index("run_harness"), str(_seq))
    chk("반환 paths 2키", set(_p) == {"facility", "audit"}, str(sorted(_p)))

    # 🔴 2026-08-12 재활용 사고 — `profiles.json` 은 `data/` 의 **사본**이라
    #    「없으면 만든다」가 곧 「있으면 안 본다」였다. full 은 업로드로 원본이 바뀐
    #    실행이므로 **무조건 다시 만든다**. 이 인자가 안 넘어가면 감리 AI 가
    #    지운 데이터셋을 보고 새로 올린 것을 못 본다 — 예외 없이 근거만 틀린다.
    _drive(reprofile=True)
    _bf = [c for c in _calls if c[0] == "build_fixtures"][0]
    chk("reprofile=True → build_fixtures(force_profile=True)",
        _bf[2].get("force_profile") is True, str(_bf[2]))
    _drive(reprofile=False)
    _bf = [c for c in _calls if c[0] == "build_fixtures"][0]
    chk("reprofile=False → force_profile=False", _bf[2].get("force_profile") is False, str(_bf[2]))

    # STEP2 네 갈래 — 「안 돌았다」와 「돌 대상이 없었다」를 구분해서 기록한다.
    _p, _seq, _log = _drive(_raw={"01": {"hitl_flags": [{"type": "exclusion_radius_missing"}]}})
    chk("mock 이면 배제반경 검색을 건너뛴다", "enrich_with_search" not in _seq, str(_seq))
    # 🔴 **건너뛴 것과 돌 대상이 없던 것은 다르다**(원칙 4). 둘 다 「검색 0회」인데
    #    사유가 다르고, 사유가 안 남으면 배제반경이 미확정인 채 STEP2 로 간 run 을
    #    「검색했는데 없었다」로 읽는다. 소요시간표의 `status` 칸이 그 자리다.
    chk("건너뛴 사유가 소요시간표에 남는다 ('mock 생략')", "mock 생략" in _log,
        [ln for ln in _log.splitlines() if "생략" in ln][:1])

    # 지역을 못 뽑으면 **추측하지 않고 멈춘다**(require_region 은 스텁하지 않았다).
    RP.A.resolve_facility_mock = _rec("resolve_facility_mock", {"facility": "재활용정거장"})
    _hit, _msg = False, ""
    try:
        with redirect_stdout(io.StringIO()):
            RP.run("datasets/재활용", "재활용정거장 부지 선정", mock=True)
    except SystemExit as e:
        _hit, _msg = True, str(e)
    chk("지역을 못 뽑으면 SystemExit (용산구로 폴백하지 않는다)", _hit, str(_msg)[:50])

    # 실패한 단계도 소요시간표에 **'실패'** 로 남고 예외는 다시 올라간다(삼키지 않는다).
    RP.A.resolve_facility_mock = _rec("resolve_facility_mock",
                                      {"facility": "재활용정거장", "region": "성동구"})
    RP.A.run_harness = _rec("run_harness")

    def _boom(*a, **kw):
        raise RuntimeError("감리 실패")

    RP.A.run_harness = _boom
    _buf = io.StringIO()
    _hit, _msg = False, ""
    try:
        with redirect_stdout(_buf):
            RP.run("datasets/재활용", "성동구 재활용정거장 부지 선정", mock=True)
    except RuntimeError as e:
        _hit, _msg = True, str(e)
    chk("단계 실패는 삼키지 않고 다시 올린다", _hit and _msg == "감리 실패", _msg)
finally:
    for _n, _v2 in _orig.items():
        setattr(RP.A, _n, _v2)
_restored = all(getattr(RP.A, n) is _orig[n] for n in _orig)
chk("A 모듈 attr 전부 원복됨", _restored)

# CLI 인자 분해 — `--flag` 가 위치 인자에 섞이면 도메인 폴더로 읽힌다.
chk("USAGE 문자열 있음", bool(getattr(RP, "USAGE", "")))


print(f"\n{ok + fail}항목 중 {ok} 통과 · {fail} 실패")
sys.exit(1 if fail else 0)
