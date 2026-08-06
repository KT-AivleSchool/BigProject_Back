# -*- coding: utf-8 -*-
"""
회귀 픽스처 대조 (S12) — LLM 변동과 코드 변경을 분리한다 (LLM 호출 0회)
=====================================================================
  python app/tools/check_fixture.py 흡연
  python app/tools/check_fixture.py 흡연 --restore     # 고정본 reviewed.json 을 step1_output 에 되돌린다

문제
  같은 데이터·같은 코드인데 감리(LLM)가 다르게 답하면 결과가 바뀐다. 그러면
  *"코드를 바꿔서 값이 변한 것"* 과 *"LLM 이 다르게 답한 것"* 을 구분할 수 없다.

이 스크립트가 하는 일
  1. **입력 고정 확인** — `step1_output/<도메인>_audit_result_reviewed.json` 의 sha256 을
     `<도메인>_FIX/reviewed.json` 과 대조한다. 다르면 **기준값 전체가 무효**다.
     이때는 아래 2 를 판정으로 쓰면 안 된다 — 그래서 다르면 값 비교를 하지 않고 멈춘다.
  2. **산출물 대조** — STEP2 행수·flag / STEP3 가중치 / STEP4 생존·배제·topN 을
     `<도메인>_FIX/기준값.json` 과 비교한다.

🔴 고정되지 않는 것 (기준값이 맞아도 보증하지 않는다)
  · `[R] 반경 제안` — 예전엔 `--radius` 를 줘도 LLM 을 호출했다(약 5초). 2026-08-05 부터
    **비-admin 지표를 전부 덮는 실행에서는 건너뛴다**(`run_weight_model.py:254`).
    쓰지도 않을 제안의 rationale 이 산출물에 남으면 "이 근거로 정했다"고 거짓말한다(원칙 4).
    일부만 덮는 실행에서는 여전히 호출된다.
  · 배제 union 은 이 스크립트가 재계산하지 않는다 — `app/tools/check_exclusion_state.py` 의 몫이다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys

# 이 스크립트는 `app/tools/` 안에 있다 — 저장소 루트는 두 단계 위다.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from app.config import (
        STEP1_OUTPUT_DIR,
        STEP2_OUTPUT_DIR,
        STEP3_OUTPUT_DIR,
        STEP4_OUTPUT_DIR,
        DOMAIN_ROOT,
        domain_prefix,
    )
except Exception:  # 단독 실행 폴백
    DOMAIN_ROOT = "data_임시"
    STEP1_OUTPUT_DIR = os.path.join(DOMAIN_ROOT, "step1_output")
    STEP2_OUTPUT_DIR = os.path.join(DOMAIN_ROOT, "step2_output")
    STEP3_OUTPUT_DIR = os.path.join(DOMAIN_ROOT, "step3_output")
    STEP4_OUTPUT_DIR = os.path.join(DOMAIN_ROOT, "step4_output")

    def domain_prefix(d):
        return d


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _load(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _cmp(label: str, exp, got, out: list) -> None:
    """기대값과 실측값을 비교해 결과 줄을 쌓는다. 부동소수는 소수 4자리에서 본다."""
    if isinstance(exp, float) or isinstance(got, float):
        ok = exp is not None and got is not None and abs(float(exp) - float(got)) < 5e-5
    else:
        ok = exp == got
    out.append((ok, label, exp, got))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument(
        "--restore",
        action="store_true",
        help="고정본 reviewed.json 을 step1_output 에 덮어쓴다",
    )
    a = ap.parse_args()

    pre = domain_prefix(a.domain)
    fix_dir = os.path.join(str(DOMAIN_ROOT), f"{a.domain}_FIX")
    fix_rev = os.path.join(fix_dir, "reviewed.json")
    base_path = os.path.join(fix_dir, "기준값.json")
    live_rev = os.path.join(str(STEP1_OUTPUT_DIR), f"{pre}_audit_result_reviewed.json")

    for p in (fix_rev, base_path):
        if not os.path.exists(p):
            print(f"[중단] 픽스처가 없다: {p}")
            return 2

    base = _load(base_path)
    print("=" * 88)
    print(f"[회귀 픽스처 대조] {a.domain}  ·  고정일 {base['고정일']}")
    print(f"  픽스처 : {fix_dir}")
    print("=" * 88)

    # ── 1. 입력 고정 확인 ────────────────────────────────────────────
    if a.restore:
        shutil.copyfile(fix_rev, live_rev)
        print(f"  [복원] {fix_rev}\n      -> {live_rev}")

    if not os.path.exists(live_rev):
        print(f"  🔴 현재 reviewed.json 이 없다: {live_rev}")
        print(f"     → python app/tools/check_fixture.py {a.domain} --restore")
        return 2

    live_sha = _sha(live_rev)
    if live_sha != base["reviewed_sha256"]:
        print("  🔴 감리 입력이 고정본과 다르다 — **기준값 전체가 무효다.**")
        print(f"     고정본 {base['reviewed_sha256'][:16]}…")
        print(f"     현재   {live_sha[:16]}…")
        print("     STEP1 을 재실행했거나 HITL 로 값을 바꿨다. 둘 중 하나를 해라:")
        print(
            f"       · 코드 회귀를 보려면 : python app/tools/check_fixture.py {a.domain} --restore"
        )
        print(
            "       · 새 기준으로 삼으려면: 파이프라인 완주 후 픽스처를 **명시적으로** 갱신"
        )
        print("=" * 88)
        return 1  # 값 비교는 하지 않는다. 여기서 비교하면 원인이 섞인다.
    print(f"  ✅ 감리 입력 일치  sha256 {live_sha[:16]}…")

    # ── 2. 산출물 대조 ──────────────────────────────────────────────
    rows: list = []

    p2 = os.path.join(str(STEP2_OUTPUT_DIR), f"{pre}_clean_report.json")
    if os.path.exists(p2):
        cur = {r["dataset_id"]: r for r in _load(p2)["results"]}
        for did, exp in base["STEP2_정제"].items():
            got = cur.get(did, {})
            _cmp(f"STEP2 {did} rows", exp["rows_after"], got.get("rows_after"), rows)
            _cmp(f"STEP2 {did} flags", exp["n_flags"], got.get("n_flags"), rows)
    else:
        rows.append((False, "STEP2 clean_report", "있어야 함", "없음"))

    p3 = os.path.join(str(STEP3_OUTPUT_DIR), f"{pre}_weight_set.json")
    if os.path.exists(p3):
        ws = _load(p3)
        cur = {i["id"]: i for i in ws["indicators"]}
        for iid, exp in base["STEP3_가중치"].items():
            got = cur.get(iid, {})
            for k in ("w_human", "w_final", "radius_m"):
                _cmp(f"STEP3 {iid}.{k}", exp[k], got.get(k), rows)
    else:
        rows.append((False, "STEP3 weight_set", "있어야 함", "없음"))

    p4 = os.path.join(str(STEP4_OUTPUT_DIR), f"{pre}_report.json")
    if os.path.exists(p4):
        rp = _load(p4)
        e4 = base["STEP4"]
        _cmp(
            "STEP4 parcels",
            base["STEP3_후보"]["parcels"],
            rp["counts"]["parcels"],
            rows,
        )
        _cmp("STEP4 points", e4["points"], rp["counts"]["points"], rows)
        _cmp("STEP4 survive", e4["survive"], rp["counts"]["survive"], rows)
        _cmp("STEP4 gap 건수", e4["gap_n"], len(rp["data_gap"]), rows)
        _cmp(
            "STEP4 gap 종류",
            e4["gap_kinds"],
            sorted({g["kind"] for g in rp["data_gap"]}),
            rows,
        )
        cur_top = [r.get("PNU") for r in rp["topn"][:20]]
        _cmp("STEP4 topN20 PNU", e4["topN20_PNU"], cur_top, rows)

        # ── 공간 연산 대조 (S5 PostGIS 전환용) ──
        #   기존 6항목은 '결과가 같은가'를 본다. 아래는 '**공간 술어가 같게
        #   동작했는가**'를 본다. 결과가 같아도 술어가 다르게 동작했을 수 있고
        #   (우연히 Top-20 이 안 바뀔 수 있다), 그 상태로 PostGIS 로 넘어가면
        #   나중에 어디서 갈렸는지 못 찾는다.
        #   기준값에 없으면 조용히 건너뛰지 않고 **불일치로 세운다** — 안 그러면
        #   픽스처를 갱신 안 한 채로 "무회귀"가 뜬다.
        sp = rp.get("spatial") or {}
        cv = rp.get("coverage") or {}
        _cmp(
            "STEP4 배제 union km²",
            e4.get("배제_union_km2"),
            sp.get("exclusion_union_km2"),
            rows,
        )
        _cmp("STEP4 커버 쌍", e4.get("cover_pairs"), cv.get("cover_pairs"), rows)
        _cmp("STEP4 수요점", e4.get("n_demand"), cv.get("n_demand"), rows)
        e_w = e4.get("width_m") or {}
        g_w = sp.get("width_m") or {}
        for k in ("n", "min", "p05", "median", "p95", "max", "sum", "pass_min_width"):
            _cmp(f"STEP4 내접폭.{k}", e_w.get(k), g_w.get(k), rows)
    else:
        rows.append((False, "STEP4 report", "있어야 함", "없음"))

    bad = [r for r in rows if not r[0]]
    for ok, label, exp, got in rows:
        if not ok:
            print(f"  ❌ {label}\n       기준 {exp}\n       현재 {got}")
    print("-" * 88)
    print(
        f"  대조 {len(rows)}항목  ·  일치 {len(rows) - len(bad)}  ·  불일치 {len(bad)}"
    )
    if bad:
        print("  🔴 감리 입력은 같은데 값이 달라졌다 → **코드 변경의 결과다.**")
    else:
        print("  ✅ 무회귀")
    print("=" * 88)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
