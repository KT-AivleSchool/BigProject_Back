# -*- coding: utf-8 -*-
"""
회귀 픽스처 갱신 (S12) — 현재 산출물을 새 기준으로 고정한다 (LLM 호출 0회)
==========================================================================
  python app/tools/make_fixture.py 흡연                             # dry-run
  python app/tools/make_fixture.py 흡연 --write                     # 실제로 고정

왜 check_fixture.py 와 분리했나
  **확인용 도구가 자기 기준을 갈아치울 수 있으면 안 된다.**
  `check_fixture.py --capture` 로 만들면 실수로 한 번 치는 순간 회귀가 기준으로
  승격되고, 그 뒤로는 그 회귀를 잡을 방법이 사라진다. 대조기는 읽기 전용으로 둔다.

무엇을 고정하나 (check_fixture.py 가 읽는 것과 정확히 같은 46항목)
  reviewed.json          감리 입력 원본 + sha256
  STEP2_정제             데이터셋별 rows_after · n_flags
  STEP3_후보             parcels
  STEP3_가중치           지표별 w_human · w_final · radius_m
  STEP4                  points · survive · gap_n · gap_kinds · topN20_PNU

  + 산출물/              당시 실제 산출물 사본 4종.
    🔴 **check_fixture.py 는 이 폴더를 읽지 않는다.** 그래서 갱신을 빠뜨려도
       대조는 46/46 으로 통과한다 — 그러고는 사람이 이 폴더를 열어 **다른 실행의
       값을 기준으로 착각한다.** 실제로 2026-08-03 그 상태가 됐다.
       기준값과 산출물은 **반드시 함께** 교체한다.

🔴 고정 전 반드시 확인
  · 파이프라인을 **완주**한 직후여야 한다. 중간 단계 산출물이 섞이면 기준이 어긋난다.
  · 산출물끼리 서로 맞는지 본다 — weight_set.inputs 의 sha 가 현재
    reviewed.json · clean_report.json 과 일치하는지 자동 검사한다(불일치면 중단).
  · 기존 픽스처는 `기준값.json.bak` · `reviewed.json.bak` 으로 남긴다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import date

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
    DOMAIN_ROOT = "datasets"
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


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


# 산출물 사본 대상 — (산출물 폴더 상수, 파일명 템플릿).
#   기준값(46항목)은 이 파일들에서 **뽑은 요약**이다. 원본을 함께 두지 않으면
#   "왜 이 값인가" 를 되짚을 수 없고, 따로 놀면 기준이 두 개가 된다.
_ARTIFACTS = (
    (STEP2_OUTPUT_DIR, "{pre}_clean_report.json"),
    (STEP3_OUTPUT_DIR, "{pre}_weight_set.json"),
    (STEP4_OUTPUT_DIR, "{pre}_report.json"),
    (STEP4_OUTPUT_DIR, "{pre}_topN_min.csv"),
)


def _artifact_pairs(pre: str, fix_dir: str) -> list:
    """[(현행 산출물 경로, 픽스처 사본 경로, 파일명)]"""
    art = os.path.join(fix_dir, "산출물")
    out = []
    for base, tmpl in _ARTIFACTS:
        name = tmpl.format(pre=pre)
        out.append((os.path.join(str(base), name), os.path.join(art, name), name))
    return out


def _stale_artifacts(pairs: list) -> list:
    """픽스처 사본이 현행과 다른 것. [(파일명, 사유)]"""
    out = []
    for live, fixed, name in pairs:
        if not os.path.exists(live):
            out.append((name, "현행 산출물 없음"))
        elif not os.path.exists(fixed):
            out.append((name, "사본 없음"))
        elif _sha(live) != _sha(fixed):
            out.append((name, "사본이 현행과 다름"))
    return out


def _backup(path: str) -> None:
    """교체가 확정된 파일만 .bak 로 남긴다 — 호출 전에 '정말 바뀌는가' 를 판정할 것.

    무조건 덮으면 **재실행 한 번에 직전 기준선이 날아간다.** 값이 그대로인 채
    write 를 다시 치면 .bak 이 현재값으로 바뀌어, 비교 대상이 사라진다.
    """
    if os.path.exists(path):
        shutil.copyfile(path, path + ".bak")


def build(
    pre: str,
    union: float | None,
    union_nsl: float | None,
    spacing: int | None,
    cli: str | None,
    prev: dict | None,
) -> dict:
    """현재 산출물 -> 기준값 dict. 없는 파일이 있으면 예외.

    union 은 이제 `report.json > spatial.exclusion_union_km2` 에 있다(2026-08-04).
    그전에는 STEP4 **로그에만** 찍혀 사람이 눈으로 읽어 `--union` 으로 옮겨적었다.
    옮겨적는 값은 오타 한 번에 기준선이 조용히 바뀌므로 산출물에서 읽는다.
    `--union` 을 그래도 주면 **산출물과 대조하고, 다르면 멈춘다** — 둘 중
    어느 쪽이 맞는지 코드가 고를 문제가 아니다.

    union_nsl(`--no-shape-lift` 재실행값)은 여전히 수기다. 같은 실행에서 나오는
    값이 아니라 **다른 실행**의 값이라 이번 산출물에 있을 수가 없다.
    """
    rev = os.path.join(str(STEP1_OUTPUT_DIR), f"{pre}_audit_result_reviewed.json")
    p2 = os.path.join(str(STEP2_OUTPUT_DIR), f"{pre}_clean_report.json")
    p3 = os.path.join(str(STEP3_OUTPUT_DIR), f"{pre}_weight_set.json")
    p4 = os.path.join(str(STEP4_OUTPUT_DIR), f"{pre}_report.json")
    for p in (rev, p2, p3, p4):
        if not os.path.exists(p):
            raise FileNotFoundError(
                f"산출물 없음: {p}\n  파이프라인을 완주한 뒤 실행하세요."
            )

    ws = _load(p3)
    rp = _load(p4)

    # ── 정합성: weight_set 이 기록한 입력 sha 와 현재 파일이 같은가 ──────
    #   STEP3 를 돌린 뒤 STEP1/2 를 다시 돌렸다면 여기서 걸린다.
    #   이걸 안 보면 서로 다른 실행의 산출물을 한 기준으로 굳히게 된다.
    mismatch = []
    for key, path in (("reviewed", rev), ("clean_report", p2)):
        rec = (ws.get("inputs") or {}).get(key) or {}
        want, got = rec.get("sha256"), _sha(path)
        if want and want != got:
            mismatch.append((key, want, got))
    if mismatch:
        msg = ["weight_set.inputs 와 현재 산출물이 다르다 — 서로 다른 실행이 섞였다."]
        for k, w, g in mismatch:
            msg.append(f"  {k:12} weight_set {w[:16]}…  현재 {g[:16]}…")
        msg.append("  파이프라인을 STEP1 부터 다시 완주한 뒤 고정하세요.")
        raise RuntimeError("\n".join(msg))

    # ── 수기 입력값: 이월 금지 ────────────────────────────────────────
    #   이전 픽스처에 있던 항목을 인자 없이 넘기면 **낡은 수치가 기준으로 남는다.**
    #   조용히 이월하느니 멈추는 편이 낫다.
    prev4 = (prev or {}).get("STEP4") or {}
    prevc = (prev or {}).get("조건") or {}
    need = []
    if union_nsl is None and "배제_union_km2_no_shape_lift" in prev4:
        need.append("--union-nsl <--no-shape-lift 로 재실행했을 때의 union 값>")
    # 🔴 `spacing` 은 **이월 금지가 아니라 필수**다. 이월 검사만 있던 시절, 처음
    #    만드는 픽스처는 `--spacing` 없이도 통과했고 `_drop_none` 이 키를 지웠다.
    #    그 픽스처로 `mode:"fixture"` 를 돌리면 `_proc_of` 의 `cond["spacing"]` 이
    #    **KeyError → 500** 이다(재활용 실측 2026-08-12). 사유가 응답에 안 실려
    #    「픽스처가 잘못됐다」가 아니라 「API 가 깨졌다」로 읽힌다.
    #    산출물에 없는 값이라 추측할 수 없다 — 그래서 여기서 멈춘다(원칙 1).
    if spacing is None:
        need.append("--spacing <gam4_site_select.py 에 준 --spacing 값>")
    if cli is None and "cli" in prevc:
        need.append('--cli "<이번 실행에 쓴 명령>"')
    if need:
        raise RuntimeError(
            "이전 픽스처에 있던 수기 측정값이 인자로 안 들어왔다.\n  "
            + "\n  ".join(need)
            + "\n  (산출물에 없는 값이라 이월하면 낡은 수치가 기준에 남는다)"
        )

    step2 = {
        r["dataset_id"]: {
            "rows_after": r["rows_after"],
            "n_flags": r["n_flags"],
            "status": r.get("status"),
        }
        for r in _load(p2)["results"]
    }
    step3 = {
        i["id"]: {
            "w_human": i["w_human"],
            "w_critic": i.get("w_critic"),
            "w_final": i["w_final"],
            "radius_m": i["radius_m"],
        }
        for i in ws["indicators"]
    }

    sp = rp.get("spatial") or {}
    cv = rp.get("coverage") or {}
    if "exclusion_union_km2" not in sp:
        raise RuntimeError(
            "report.json 에 spatial.exclusion_union_km2 가 없다 — 계측 이전 산출물이다.\n"
            "  gam4_site_select.py 를 다시 돌린 뒤 고정하세요."
        )
    got_union = sp["exclusion_union_km2"]
    if union is not None and abs(union - got_union) > 1e-9:
        raise RuntimeError(
            f"--union {union} 과 산출물 {got_union} 이 다르다.\n"
            "  어느 쪽이 맞는지는 코드가 정할 일이 아니다. 확인 후 --union 을 빼거나 맞추세요."
        )

    step4 = {
        "points": rp["counts"]["points"],
        "survive": rp["counts"]["survive"],
        "gap_n": len(rp["data_gap"]),
        "gap_kinds": sorted({g["kind"] for g in rp["data_gap"]}),
        "topN20_PNU": [r.get("PNU") for r in rp["topn"][:20]],
        # ── 공간 술어 대조값 (S5) ──
        "배제_union_km2": got_union,
        "cover_pairs": cv.get("cover_pairs"),
        "n_demand": cv.get("n_demand"),
        "width_m": {
            k: (sp.get("width_m") or {}).get(k)
            for k in (
                "n",
                "min",
                "p05",
                "median",
                "p95",
                "max",
                "sum",
                "pass_min_width",
            )
        },
    }
    if union_nsl is not None:
        step4["배제_union_km2_no_shape_lift"] = union_nsl

    return {
        "_설명": (
            (prev or {}).get("_설명")
            or "S12 회귀 픽스처 기준값. reviewed.json 을 고정한 상태에서 "
            "STEP2~4 를 돌렸을 때 나와야 하는 값. "
            "감리 입력이 같은데 값이 다르면 코드 변경의 결과다."
        ),
        "고정일": date.today().isoformat(),
        "reviewed_sha256": _sha(rev),
        # 조건 — alpha·decay·scale·candidates 는 weight_set 에서 그대로 온다.
        #   spacing·cli 는 어느 산출물에도 없다. 추측해서 넣으면 재현 조건이
        #   조용히 틀어지므로 인자로만 받는다(위 need 검사 참조).
        "조건": _drop_none(
            {
                "alpha": ws.get("alpha"),
                "decay": ws.get("decay"),
                "scale": ws.get("scale"),
                "candidates": ws.get("candidate_source", {}).get("file"),
                "spacing": spacing,
                "cli": cli,
            }
        ),
        "STEP2_정제": step2,
        "STEP3_후보": {"parcels": rp["counts"]["parcels"]},
        "STEP3_가중치": step3,
        "STEP4": step4,
    }


def _diff(old: dict, new: dict) -> list:
    """고정본 대비 무엇이 바뀌는지. [(경로, 이전, 이후)]"""
    out = []

    def walk(a, b, path=""):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                walk(a.get(k), b.get(k), f"{path}.{k}" if path else str(k))
        elif a != b:
            out.append((path, a, b))

    walk(old, new)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="회귀 픽스처 갱신 (기본은 dry-run)")
    ap.add_argument("domain")
    ap.add_argument("--write", action="store_true", help="실제로 고정한다")
    ap.add_argument(
        "--union",
        type=float,
        default=None,
        help="배제 union km2. 이제 산출물에서 자동으로 읽으므로 "
        "**줄 필요가 없다**. 주면 산출물과 대조하고 다르면 멈춘다",
    )
    ap.add_argument(
        "--union-nsl",
        type=float,
        default=None,
        help="--no-shape-lift 로 재실행했을 때의 union km2",
    )
    ap.add_argument(
        "--spacing",
        type=int,
        default=None,
        help="gam4_site_select.py 에 준 --spacing (산출물에 없어 수기 입력)",
    )
    ap.add_argument("--cli", default=None, help="이번 실행에 쓴 명령. 재현용 기록")
    a = ap.parse_args()

    pre = domain_prefix(a.domain)
    fix_dir = os.path.join(str(DOMAIN_ROOT), f"{a.domain}_FIX")
    fix_rev = os.path.join(fix_dir, "reviewed.json")
    base_path = os.path.join(fix_dir, "기준값.json")
    live_rev = os.path.join(str(STEP1_OUTPUT_DIR), f"{pre}_audit_result_reviewed.json")

    print("=" * 88)
    print(
        f"[픽스처 갱신] {a.domain}   {'실행' if a.write else 'dry-run (--write 로 실제 고정)'}"
    )
    print(f"  대상 : {fix_dir}")
    print("=" * 88)

    prev = _load(base_path) if os.path.exists(base_path) else None
    try:
        new = build(pre, a.union, a.union_nsl, a.spacing, a.cli, prev)
    except Exception as e:
        print(f"  🔴 {e}")
        return 2

    pairs = _artifact_pairs(pre, fix_dir)
    stale = _stale_artifacts(pairs)

    # ── 무엇이 바뀌는가 ────────────────────────────────────────────
    changes = []
    if prev is not None:
        changes = [c for c in _diff(prev, new) if c[0] not in ("고정일",)]
        if not changes and not stale:
            print("  ✅ 기존 고정본과 동일 — 갱신할 것이 없다")
            return 0
        if not changes:
            # 기준값은 같은데 사본만 낡은 경우. check_fixture 는 46/46 을 주므로
            # 이 경로가 없으면 영영 안 고쳐진다(2026-08-03 실제로 그랬다).
            print("  기준값 46항목은 동일 — 산출물 사본만 갱신한다")
        else:
            print(f"  변경 {len(changes)}항목")
        for path, a_, b_ in changes:
            sa, sb = str(a_), str(b_)
            if len(sa) > 60 or len(sb) > 60:
                sa, sb = sa[:57] + "…", sb[:57] + "…"
            print(f"    {path}\n        이전 {sa}\n        이후 {sb}")
        if any(p.startswith("reviewed_sha256") for p, _, _ in changes):
            print("\n  ⚠ 감리 입력(reviewed)이 바뀐다 — LLM 판정이 달라졌다는 뜻이다.")
            print(
                "    아래 값 변화가 '코드 개선' 인지 'LLM 흔들림' 인지 확인하고 고정하라."
            )
    else:
        print("  기존 고정본 없음 — 새로 만든다")
        print(
            f"    STEP2 {len(new['STEP2_정제'])}개 · STEP3 {len(new['STEP3_가중치'])}지표"
            f" · survive {new['STEP4']['survive']:,}"
        )

    if stale:
        print(f"\n  산출물 사본 {len(stale)}건 교체")
        for name, why in stale:
            print(f"    {name:28} {why}")
        if any(w == "현행 산출물 없음" for _, w in stale):
            print("  🔴 현행 산출물이 없다 — 파이프라인을 완주하지 않았다.")
            return 2
    else:
        print("  산출물 사본 최신")

    if not a.write:
        print("-" * 88)
        print(
            f"  dry-run 이다. 고정하려면: python app/tools/make_fixture.py {a.domain} --write"
        )
        print("=" * 88)
        return 0

    # ── 쓰기 ──────────────────────────────────────────────────────
    #   .bak 은 **실제로 교체되는 파일만** 남긴다(_backup 주석 참조).
    os.makedirs(fix_dir, exist_ok=True)
    os.makedirs(os.path.join(fix_dir, "산출물"), exist_ok=True)

    if changes or prev is None:
        _backup(base_path)
        with open(base_path, "w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False, indent=2)
    if not os.path.exists(fix_rev) or _sha(fix_rev) != _sha(live_rev):
        _backup(fix_rev)
        shutil.copyfile(live_rev, fix_rev)
    for live, fixed, _name in pairs:
        shutil.copyfile(live, fixed)

    print("-" * 88)
    print(f"  ✅ 고정 완료  {new['고정일']}  sha256 {new['reviewed_sha256'][:16]}…")
    print(f"     {fix_rev}")
    print(f"     {base_path}")
    print(f"     {os.path.join(fix_dir, '산출물')}  ({len(pairs)}종)")
    print("     (교체된 것만 *.bak 로 남겼다)")
    print(f"\n  확인: python app/tools/check_fixture.py {a.domain}")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
