"""STEP 0.5 `_judge_relevance` — 「판정 못 했다」의 원인 분해 대조. LLM·DB 0회.

🔴 묻는 것은 「관련 판정이 맞나」가 **아니다**. 그건 LLM 몫이라 여기서 못 잰다.
   묻는 것은 **판정하지 못한 파일마다 왜 못 했는지가 산출물에 남는가**다 —
   `resolve_facility` 가 원본 `file_labels` 를 pop 하므로, 여기서 안 남기면
   나중에 되짚을 방법이 아예 없다(원칙 4).

🔴 §3·§4·§5 가 핵심이다. 개수만 보면 셋 다 「unjudged 1건」으로 같은데
   성격이 다르다 — 모델이 이상한 이름을 쓴 것(`unknown_rel`) · 응답에서 그 파일이
   빠진 것(`missing`) · 응답이 통째로 없는 것(`no_labels`). 특히 §4 는 **환각 번호가
   남의 missing 을 만드는** 간접 인과라, `dropped` 가 없으면 원인이 사라진다.
"""

import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
# app/tools/ → 두 단계 위가 저장소 루트다
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.gam2_audit_judgment_test import (  # noqa: E402
    _judge_relevance,
    resolve_facility_mock,
)

N = ["a.csv", "b.csv", "c.csv"]
UI = "용산구 흡연부스 부지 선정 (시설 유형: 흡연부스, 분석 지역: 용산구)"
ok = ng = 0


def chk(name, cond, got=""):
    global ok, ng
    if cond:
        ok += 1
        print(f"  OK   {name}")
    else:
        ng += 1
        print(f"  🔴NG {name}  got={got!r}")


def causes(rel):
    return [(u["filename"], u["cause"]) for u in rel["unjudged"]]


print("§1 전건 정상 — unjudged 도 dropped 도 비어야")
L = [{"i": i, "about": "x", "rel": "regulates"} for i in range(3)]
m, r, rel = _judge_relevance(L, UI, N, "흡연부스")
chk("mismatch False", m is False, m)
chk("unjudged []", rel["unjudged"] == [], rel["unjudged"])
chk("dropped []", rel["dropped"] == [], rel["dropped"])
chk("count 대칭", rel["unjudged_count"] == len(rel["unjudged"]) == 0)

print("§2 한 파일 누락 → missing")
m, r, rel = _judge_relevance(L[:2], UI, N, "흡연부스")
chk("cause missing", causes(rel) == [("c.csv", "missing")], causes(rel))
chk("배지 안 켜짐", m is False, m)
chk("summary 비지 않음", bool(rel["summary"]))

print("§3 모르는 rel → unknown_rel (missing 과 갈려야)")
m, r, rel = _judge_relevance(L[:2] + [{"i": 2, "about": "x", "rel": "매우관련있음"}], UI, N, "흡연부스")
chk("cause unknown_rel", causes(rel) == [("c.csv", "unknown_rel")], causes(rel))

print("§4 환각 번호 → 그 파일 missing + dropped bad_index")
m, r, rel = _judge_relevance(L[:2] + [{"i": 11, "about": "x", "rel": "self"}], UI, N, "흡연부스")
chk("c 는 missing", causes(rel) == [("c.csv", "missing")], causes(rel))
chk("dropped 에 인과", rel["dropped"] == [{"i": 11, "cause": "bad_index"}], rel["dropped"])

print("§5 labels 자체가 없음 → 전건 no_labels (missing 아님)")
m, r, rel = _judge_relevance(None, UI, N, "흡연부스")
chk("전건 no_labels", {c for _, c in causes(rel)} == {"no_labels"}, causes(rel))
chk("3건", rel["unjudged_count"] == 3, rel["unjudged_count"])
chk("파싱실패에 배지 안 켬", m is False, m)

print("§6 malformed — dict 아님 / i 가 bool")
m, r, rel = _judge_relevance(["문자열", {"i": True, "rel": "self"}], UI, N, "흡연부스")
chk("bool 이 1번으로 안 앉음", causes(rel) == [(f, "missing") for f in N], causes(rel))
chk("dropped 2건 malformed", rel["dropped"] == [{"i": None, "cause": "malformed"}] * 2, rel["dropped"])

print("§7 mismatch 는 전수 판정됐을 때만")
L0 = [{"i": i, "about": "x", "rel": "unrelated"} for i in range(3)]
m, r, rel = _judge_relevance(L0, UI, N, "흡연부스")
chk("mismatch True", m is True, m)
chk("reason 비지 않음", bool(r.strip()), r)
chk("unjudged 없음", rel["unjudged"] == [], rel["unjudged"])
m2, r2, _ = _judge_relevance(L0[:2], UI, N, "흡연부스")
chk("하나만 몰라도 안 켬", m2 is False and r2 == "", (m2, r2))

print("§8 mock ↔ real 키 집합")
fx = {str(i): {"filename": f} for i, f in enumerate(N)}
mk = resolve_facility_mock(UI, fx)
_, _, rel = _judge_relevance(L, UI, N, "흡연부스")
chk("relevance 키 동일", set(mk["relevance"]) == set(rel), set(mk["relevance"]) ^ set(rel))
chk("mock unjudged 전건", len(mk["relevance"]["unjudged"]) == 3)
chk("mock cause not_judged", {u["cause"] for u in mk["relevance"]["unjudged"]} == {"not_judged"})
chk("mock 도 count 대칭", mk["relevance"]["unjudged_count"] == len(mk["relevance"]["unjudged"]))

print("§9 불변식 — 모든 갈래에서 summary 비지 않음 · True 면 reason 있음")
for nm, lb, ui in [
    ("정상", L, UI),
    ("누락", L[:2], UI),
    ("전무", L0, UI),
    ("없음", None, UI),
    ("빈입력", L, ""),
]:
    m, r, rel = _judge_relevance(lb, ui, N, "흡연부스")
    chk(f"{nm}: summary", bool(rel["summary"].strip()))
    chk(f"{nm}: True⇒reason", (not m) or bool(r.strip()))
    chk(
        f"{nm}: 개수 정합",
        rel["dataset_count"] == 3
        and rel["related_count"] == len(rel["related"])
        and rel["unjudged_count"] == len(rel["unjudged"]),
    )

print(f"\n{'🔴 ' if ng else ''}{ok}/{ok + ng}")
sys.exit(1 if ng else 0)
