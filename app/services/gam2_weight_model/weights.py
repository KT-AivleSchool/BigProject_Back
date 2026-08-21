# -*- coding: utf-8 -*-
"""[C][D][E] 가중치 — 감리 seed · CRITIC · 합성"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .util import _safe_corr, rankdata


def critic_weights(
    norm: pd.DataFrame, sparse_ids: set = None, spearman: bool = True
) -> dict:
    """CRITIC. 반환 {id: weight}. sparse_ids 제외."""
    cols = [c for c in norm.columns if not (sparse_ids and c in sparse_ids)]
    if not cols:
        return {}
    X = norm[cols].values
    Xr = (
        np.column_stack([rankdata(X[:, j]) for j in range(X.shape[1])])
        if spearman
        else X
    )
    std = Xr.std(axis=0, ddof=1)
    corr = _safe_corr(Xr) if len(cols) > 1 else np.array([[1.0]])
    conflict = np.sum(1 - corr, axis=1)
    C = std * conflict
    if C.sum() <= 0:
        return {c: 1.0 / len(cols) for c in cols}
    w = C / C.sum()
    return dict(zip(cols, w))


def critic_bootstrap(
    norm: pd.DataFrame, sparse_ids: set = None, B: int = 1000, seed: int = 0
) -> dict:
    rng = np.random.default_rng(seed)
    cols = [c for c in norm.columns if not (sparse_ids and c in sparse_ids)]
    acc = {c: [] for c in cols}
    n = len(norm)
    for _ in range(B):
        idx = rng.integers(0, n, n)
        w = critic_weights(norm.iloc[idx].reset_index(drop=True), sparse_ids)
        for c in cols:
            acc[c].append(w.get(c, 0.0))
    return {
        c: {
            "mean": float(np.mean(v)),
            "std": float(np.std(v)),
            "ci_low": float(np.percentile(v, 2.5)),
            "ci_high": float(np.percentile(v, 97.5)),
        }
        for c, v in acc.items()
    }


# =========================================================
# [C] w_human / [E] 합성
# =========================================================
def human_weights(indicators: list) -> dict:
    """seed_weight 를 합=1 로 정규화.

    seed_weight 는 **크기만** 담는다(항상 >=0). 방향은 `direction` 필드가 갖는다
    — define_indicators 가 negative_factor 에 abs() 를 씌우는 이유다.
    부호를 여기 넣으면 normalize_matrix 의 cost 반전과 이중으로 걸려
    감점 지표가 조용히 가점으로 작동한다.
    """
    s = {i["id"]: float(i["seed_weight"]) for i in indicators}

    # 지표 0개 — 아래 합계 0 가드에도 걸리지만 원인이 전혀 다르다.
    #   합계 0 = 사람이 슬라이더를 전부 0으로 내림
    #   지표 0개 = 감리가 가점/감점을 하나도 판정하지 않음 (STEP1 문제)
    #   같은 메시지를 내면 STEP3 를 붙잡고 있게 된다.
    if not s:
        raise ValueError(
            "지표가 0개입니다 — 가중치를 계산할 대상이 없습니다.\n"
            "  감리 결과에 positive_factor / negative_factor 판정이 없습니다.\n"
            "  reviewed.json 의 roles 를 확인하세요 "
            "(전부 hard_exclusion / reference_only 로 판정됐을 수 있습니다)."
        )

    # 음수 방지 — [W] HITL 이 슬라이더 -1~+1 을 분해하지 않고 그대로 넣으면 여기 걸린다.
    neg = {k: v for k, v in s.items() if v < 0}
    if neg:
        raise ValueError(
            f"seed_weight 에 음수가 있습니다: {neg}\n"
            f"  가중치는 크기(>=0)만 담습니다. 방향은 indicator['direction'] "
            f"('benefit'|'cost') 로 표현하세요."
        )

    tot = sum(s.values())
    if tot <= 0:
        raise ValueError(
            f"가중치 합이 {tot} 입니다 — 최소 하나는 0보다 커야 합니다.\n"
            f"  입력값: {s}\n"
            f"  전부 0이면 모든 후보 점수가 0이 되어 순위가 무의미해집니다."
        )

    return {k: v / tot for k, v in s.items()}


def synthesize(
    w_human: dict, w_critic: dict, alpha: float = 0.3, sparse_ids: set = None
) -> dict:
    """w = (1-a)*human + a*critic. 희소지표는 human 만. 최종 sum=1 재정규화."""
    out = {}
    for i in w_human:
        h = w_human.get(i, 0.0)
        if sparse_ids and i in sparse_ids:
            out[i] = h
        else:
            out[i] = (1 - alpha) * h + alpha * w_critic.get(i, 0.0)

    tot = sum(out.values())
    if tot <= 0:
        raise ValueError(
            f"합성 가중치 합이 {tot} 입니다 — 정규화 불가.\n"
            f"  w_human={w_human}\n  w_critic={w_critic}\n  alpha={alpha}"
        )

    return {k: v / tot for k, v in out.items()}
