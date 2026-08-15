# -*- coding: utf-8 -*-
"""
HITL 완주 대조 (A2) — fixture 와 hitl 이 **같은 값**에 도달하는지
=====================================================================
  python app/tools/check_hitl_e2e.py 흡연              # 두 모드를 실제로 돌린다
  python app/tools/check_hitl_e2e.py 흡연 --compare r_a r_b   # 이미 끝난 run 둘만 대조

무엇을 보는가
  같은 답을 넣었을 때 `mode: "hitl"` 이 `mode: "fixture"` 와 **같은 산출물**을 내는지.
  게이트를 넣느라 파이프라인 값이 흔들렸다면 여기서 걸린다.

  · 게이트A 답 = `{}` — 픽스처 감리는 전부 확정 상태라 고칠 게 없다(읽기 전용)
  · 게이트B 답 = **픽스처 반경** + 게이트가 제안한 슬라이더 그대로
    🔴 게이트B 가 LLM 으로 제안한 반경을 그대로 쓰면 값이 달라지는 게 **정상**이다.
       픽스처와 같은 답을 넣었을 때 같은 값이 나오는지를 보는 것이다.

🔴 서버를 쓰지 않는다. 러너를 in-process 로 부른다 — 포트도 프로세스도 안 건드린다.
   (살아 있는 uvicorn 을 재시작하면 `_reap_orphans` 가 남의 run 을 닫는다)

🔴 LLM 을 부른다 — 제안 패스 1회(약 10초). 완전 무호출은 `check_hitl_gate.py` 쪽이다.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# 이 스크립트는 `app/tools/` 안에 있다 — 저장소 루트는 두 단계 위다.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.config import DOMAIN_ROOT  # noqa: E402
from app.services import pipeline_runner as R  # noqa: E402

args = [a for a in sys.argv[1:]]
COMPARE: list[str] = []
if "--compare" in args:
    i = args.index("--compare")
    COMPARE = args[i + 1 : i + 3]
    args = args[:i]
DOMAIN = args[0] if args else "흡연"

ok_all = True


# ── 산출물 요약 — 비교 대상은 **값이 있는 키만** 쓴다 ────────────────
#    없는 키를 넣으면 None == None 으로 통과해 **아무것도 안 본 채 초록불**이 된다.
def summary(run_id: str) -> dict:
    p = R.run_dir(run_id)
    ws = json.loads(
        (p / "step3" / f"{DOMAIN}_weight_set.json").read_text(encoding="utf-8")
    )
    rp = json.loads((p / "step4" / f"{DOMAIN}_report.json").read_text(encoding="utf-8"))
    inds = ws.get("indicators") or []
    out = {
        "w_human": {i["id"]: i.get("w_human") for i in inds},
        "w_critic": {i["id"]: i.get("w_critic") for i in inds},
        "w_final": {i["id"]: i.get("w_final") for i in inds},
        "radius_m": {i["id"]: i.get("radius_m") for i in inds},
        "counts": rp["counts"],
        "spatial": rp["spatial"],
        "coverage": rp["coverage"],
        "gap_kinds": sorted(g["kind"] for g in rp["data_gap"]),
        "topn_PNU": [r["PNU"] for r in rp["topn"]],
        "topn_점수": [r["점수"] for r in rp["topn"]],
    }
    empty = [k for k, v in out.items() if v in (None, {}, [])]
    if empty:  # 비어 있으면 "일치" 가 아무 뜻도 없다
        raise SystemExit(
            f"🔴 {run_id}: 비교 항목이 비었습니다 → {empty}\n"
            f"   산출물 키 이름이 바뀐 것이다. 이 스크립트를 고쳐야 한다."
        )
    return out


def diff(a: dict, b: dict, label: str) -> bool:
    j = lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False)  # noqa: E731
    bad = [k for k in a if j(a[k]) != j(b.get(k))]
    if bad:
        print(f"  🔴 {label} 불일치: {bad}")
        for k in bad:
            print(f"     기준 {k} = {j(a[k])[:300]}")
            print(f"     실측 {k} = {j(b.get(k))[:300]}")
        return False
    print(f"  ✅ {label} 전 항목 일치 ({len(a)}개)")
    return True


def wait(run_id: str, until: tuple[str, ...], timeout: int = 1800) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = R.read_status(run_id)
        if d["status"] in until:
            return d
        time.sleep(2)
    raise TimeoutError(f"{run_id}: {R.read_status(run_id)['status']} (제한 {timeout}s)")


# ── 대조만 하는 모드 ────────────────────────────────────────────────
if COMPARE:
    a, b = COMPARE
    print(f"[대조] {a} ↔ {b}")
    sys.exit(0 if diff(summary(a), summary(b), f"{a}↔{b}") else 1)

# ── [1] fixture 모드 ───────────────────────────────────────────────
print("[1] fixture 모드")
rid1 = R.start_run(DOMAIN, R.MODE_FIXTURE)
print("   run_id =", rid1)
d = wait(rid1, ("succeeded", "failed"))
print("   status =", d["status"], d.get("error") or "")
ok_all &= d["status"] == "succeeded"
if d["status"] != "succeeded":
    sys.exit("🔴 fixture 가 실패했다 — hitl 을 볼 의미가 없다")
base = summary(rid1)

# ── [2] hitl 모드 ─────────────────────────────────────────────────
print("\n[2] hitl 모드 — 게이트 두 개를 지나 같은 값에 도달해야 한다")
rid2 = R.start_run(DOMAIN, R.MODE_HITL)
print("   run_id =", rid2)

d = wait(rid2, ("awaiting_hitl", "failed", "succeeded"))
print("   status =", d["status"], "· gate =", (d.get("gate") or {}).get("id"))
assert d["status"] == "awaiting_hitl" and d["gate"]["id"] == "audit", d
qs = d["gate"]["questions"]
ed = [q for q in qs if q["editable"]]
print(f"   게이트A 질문 {len(qs)}건 · 편집가능 {len(ed)}건")
# 🔴 2026-08-10 — hitl 모드는 배제 확정을 **전부 제안값으로 되돌린다**(사람이 전부 본다).
#    그래서 예전처럼 빈 답을 낼 수 없다. 답은 **제안값 = 픽스처가 확정했던 값**이고,
#    같은 답을 넣으면 fixture 와 같은 값이 나와야 한다 — 그게 이 대조의 뜻이다.
#    배제 말고 편집 가능한 게 있으면 픽스처가 아니다(답을 지어낼 수 없다).
other = [(q["kind"], q["dataset_id"]) for q in ed if q["kind"] != "exclusion"]
if other:
    sys.exit(f"🔴 배제 말고 편집 가능한 질문이 있다 — 답을 만들 수 없다: {other}")
exclusions = [
    {
        "dataset_id": q["dataset_id"],
        "role_index": q["role_index"],
        # 제안값이 없으면 role 에 실린 값(= 픽스처 확정값)을 그대로 승인한다.
        "radius_m": q["proposed_m"] if q["proposed_m"] is not None else q["radius_m"],
    }
    for q in ed
]
print("   답변 배제반경 =", [(e["dataset_id"], e["radius_m"]) for e in exclusions])
R.submit_gate(rid2, "audit", {"run_id": rid2, "exclusions": exclusions})

d = wait(rid2, ("awaiting_hitl", "failed", "succeeded"))
print("   status =", d["status"], "· gate =", (d.get("gate") or {}).get("id"))
assert d["status"] == "awaiting_hitl" and d["gate"]["id"] == "weight", d
qw = d["gate"]["questions"]
print(
    f"   게이트B 지표 {len(qw)}건 · 반경필요 {sum(q['radius_required'] for q in qw)}건"
    f" · 충돌 {sum(bool(q['conflict']) for q in qw)}건"
)

fix = json.loads(
    (Path(DOMAIN_ROOT) / f"{DOMAIN}_FIX" / "기준값.json").read_text(encoding="utf-8")
)
radius = {
    k: v["radius_m"]
    for k, v in fix["STEP3_가중치"].items()
    if v.get("radius_m") is not None
}
slider = {q["indicator_id"]: q["slider_proposed"] for q in qw}
print("   답변 radius =", radius)
print("   제안 radius =", {q["indicator_id"]: q["radius_proposed"] for q in qw})
R.submit_gate(rid2, "weight", {"run_id": rid2, "radius": radius, "slider": slider})

d = wait(rid2, ("succeeded", "failed"))
print("   status =", d["status"], d.get("error") or "")
ok_all &= d["status"] == "succeeded"
if d["status"] == "succeeded":
    ok_all &= diff(base, summary(rid2), "fixture ↔ hitl")

# ── [3] 답변 기록 ─────────────────────────────────────────────────
print("\n[3] 게이트 답변 기록")
for g in R.GATE_IDS:
    p = R._answer_path(rid2, g)
    print(f"   {g}: {'있음' if p.is_file() else '🔴 없음'}  {p}")
    ok_all &= p.is_file()

print("\n==", "통과" if ok_all else "🔴 실패", "==")
print("   fixture", rid1, "· hitl", rid2)
sys.exit(0 if ok_all else 1)
