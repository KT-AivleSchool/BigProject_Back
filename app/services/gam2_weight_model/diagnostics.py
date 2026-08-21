# -*- coding: utf-8 -*-
"""진단 — 표본 편향 · alpha 민감도"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .matrix import normalize_matrix
from .weights import critic_weights, synthesize


# =========================================================
# [진단] 후보 표본 대표성 — 편향이 가중치를 왜곡하는지 검사
# =========================================================
def diagnose_sample_bias(
    mat: pd.DataFrame,
    indicators: list,
    sparse_ids: set,
    strata: pd.Series = None,
    seed: int = 0,
    verbose: bool = True,
) -> dict:
    """후보 집합이 한쪽에 쏠려 있을 때 CRITIC 가중치가 흔들리는지 검사한다.

    가중치는 '후보 집합'을 표본으로 계산되므로, 표본이 편향되면 가중치도 편향될
    수 있다. 표본을 여러 방식으로 바꿔가며 다시 계산해 변동 폭을 본다.
      · 전체        : 기준값
      · 층화 균등   : strata(동 등) 별로 같은 수만 뽑음 → 쏠림 제거
      · 최다 2계층 제외 : 가장 많은 두 계층을 통째로 제거
      · 무작위 절반 : 표본 크기 절반

    strata: 후보와 같은 길이의 계층 라벨(예: 법정동명). None이면 층화 검사 생략.
    반환: {지표: {"base","min","max","spread"}, "_max_spread":float, "_rank_stable":bool}
          — **그대로 json 직렬화 가능해야 한다.** 산출물(`weight_set.diagnostics`)로 나간다.

    판정 기준(경험적): spread < 0.05 이고 순위가 유지되면 '표본 편향의 영향 미미'.
    실측(용산 국유지 2,486, 후암동 21%·상위5동 49% 쏠림): 최대 spread 0.018,
    순위 완전 유지 → 편향은 있으나 가중치는 사실상 불변.
    """
    rng = np.random.default_rng(seed)
    variants = {}

    variants["전체"] = mat
    if strata is not None:
        s = pd.Series(list(strata)).reset_index(drop=True)
        n_each = int(np.median(s.value_counts()))
        idx = []
        for _, grp in s.groupby(s):
            take = grp.index[:n_each] if len(grp) > n_each else grp.index
            idx.extend(take)
        variants["층화균등"] = mat.loc[sorted(idx)].reset_index(drop=True)
        top2 = s.value_counts().head(2).index
        keep = s[~s.isin(top2)].index
        if len(keep) > 30:
            variants["최다2계층제외"] = mat.loc[sorted(keep)].reset_index(drop=True)
    half = rng.choice(len(mat), max(len(mat) // 2, 30), replace=False)
    variants["무작위절반"] = mat.loc[sorted(half)].reset_index(drop=True)

    ws = {
        k: critic_weights(normalize_matrix(v, indicators), sparse_ids=sparse_ids)
        for k, v in variants.items()
    }

    cols = [c for c in mat.columns if c not in sparse_ids]
    out, max_spread = {}, 0.0
    for c in cols:
        vals = [ws[k].get(c, 0.0) for k in ws]
        sp = max(vals) - min(vals)
        max_spread = max(max_spread, sp)
        # float() — numpy 스칼라는 json.dump 가 못 삼킨다. 이 값은 이제 산출물로 나간다(S4).
        out[c] = {
            "base": float(ws["전체"].get(c, 0.0)),
            "min": float(min(vals)),
            "max": float(max(vals)),
            "spread": float(sp),
        }

    ranks = [tuple(sorted(cols, key=lambda c: -ws[k].get(c, 0.0))) for k in ws]
    rank_stable = len(set(ranks)) == 1

    # 순위가 뒤집힌 쌍을 찾아 '왜' 뒤집혔는지까지 남긴다.
    #   두 지표의 기준값 격차가 각자의 변동폭보다 작으면, 순서가 바뀌는 것은
    #   편향의 증거가 아니라 **근접 동률의 당연한 결과**다.
    #   (새 임계값을 정하지 않는다 — 이미 측정된 변동폭을 잣대로 쓴다)
    base_order = sorted(cols, key=lambda c: -ws["전체"].get(c, 0.0))
    pos = {c: i for i, c in enumerate(base_order)}
    flips, seen = [], set()
    for k in ws:
        p = {c: i for i, c in enumerate(sorted(cols, key=lambda c: -ws[k].get(c, 0.0)))}
        for a in cols:
            for b in cols:
                if pos[a] < pos[b] and p[a] > p[b] and (a, b) not in seen:
                    seen.add((a, b))
                    gap = abs(out[a]["base"] - out[b]["base"])
                    tol = max(out[a]["spread"], out[b]["spread"])
                    flips.append(
                        {
                            "pair": [a, b],
                            "gap": float(gap),
                            "tol": float(tol),
                            "explained": bool(gap < tol),
                            "variant": k,
                        }
                    )
    unexplained = [f for f in flips if not f["explained"]]

    out["_max_spread"] = float(max_spread)
    out["_rank_stable"] = bool(rank_stable)
    out["_rank_flips"] = flips
    out["_variants"] = {k: len(v) for k, v in variants.items()}

    if verbose:
        print(
            "\n[표본 대표성 진단]  변형: "
            + ", ".join(f"{k}({n})" for k, n in out["_variants"].items())
        )
        for c in cols:
            d = out[c]
            print(
                f"  {c:<10} {d['base']:.3f}  범위 [{d['min']:.3f}, {d['max']:.3f}]"
                f"  변동폭 {d['spread']:.3f}"
            )
        verdict = (
            "영향 미미 — 표본 편향이 가중치를 왜곡하지 않음"
            if max_spread < 0.05 and not unexplained
            else "⚠ 표본 편향 영향 있음 — 후보 집합 재검토 필요"
        )
        for f in flips:
            a, b = f["pair"]
            tag = "근접 동률" if f["explained"] else "⚠ 유의미한 역전"
            print(
                f"  순위 교체 {a}↔{b}  격차 {f['gap']:.3f} vs 변동폭 "
                f"{f['tol']:.3f}  [{tag}]  ({f['variant']})"
            )
        note = (
            ""
            if rank_stable
            else f" · 순위 교체 {len(flips)}쌍(설명 안 되는 것 {len(unexplained)})"
        )
        print(f"  최대 변동폭 {max_spread:.3f}{note} → {verdict}")
    return out


# =========================================================
# [진단] alpha 민감도 — 합성 비율이 결과를 얼마나 바꾸는가
# =========================================================
def diagnose_alpha(
    w_human: dict,
    w_critic: dict,
    sparse_ids: set,
    alphas=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5),
    verbose: bool = True,
) -> dict:
    """alpha 를 바꿔가며 최종 가중치·순위가 얼마나 달라지는지 표로 본다.
    'alpha=0.3 인 이유'를 관례가 아니라 근거로 설명하기 위한 진단.
    순위가 바뀌지 않는 구간이 넓으면 그 중앙값을 택하는 것이 방어 가능하다.

    반환 dict
      weights      {alpha: {지표: w_final}}      — json 직렬화 가능
      rank_groups  [{"alphas": [...], "order": [...]}]  순위가 같은 alpha 구간
      table        pandas DataFrame. **사람이 보는 용도 — json 에는 싣지 않는다.**

    순위 구간은 verbose 여부와 무관하게 계산한다. 예전에는 print 블록 안에서만
    만들어져, `--no-diag` 가 아니어도 **근거가 콘솔에만 남고 사라졌다**(S4).
    """
    rows = {}
    for a in alphas:
        rows[a] = synthesize(w_human, w_critic, alpha=a, sparse_ids=sparse_ids)
    df = pd.DataFrame(rows)

    ranks = {a: tuple(df[a].sort_values(ascending=False).index) for a in alphas}
    uniq = {}
    for a, r in ranks.items():
        uniq.setdefault(r, []).append(a)

    if verbose:
        print("\n[alpha 민감도]")
        print(df.round(3).to_string())
        print("  순위가 같은 alpha 구간:")
        for r, aa in uniq.items():
            print(f"    α={aa}  →  {' > '.join(r)}")

    return {
        "weights": {str(a): {k: float(v) for k, v in rows[a].items()} for a in alphas},
        "rank_groups": [
            {"alphas": [float(x) for x in aa], "order": list(r)}
            for r, aa in uniq.items()
        ],
        "table": df,
    }
