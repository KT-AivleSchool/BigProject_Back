# -*- coding: utf-8 -*-
"""게이트B — 슬라이더 환산 · 사람 확정 적용"""
from __future__ import annotations

from datetime import datetime


# =========================================================
# [W] 가중치 HITL — 슬라이더 -1~+1 (부호=방향, abs=크기)
# =========================================================
#   설계: STEP3_가중치_설계 11·12절
#
#   🔴 핵심 규약 — 슬라이더 값을 seed_weight 에 그대로 넣으면 안 된다.
#      seed_weight 는 크기만 담고(항상 >=0), 방향은 direction 필드가 갖는다.
#      부호를 seed_weight 에 넣으면 normalize_matrix 의 cost 반전과 이중으로 걸려
#      감점 지표가 조용히 가점으로 작동한다. 반드시 경계에서 분해한다.
#
#      슬라이더 -0.6  ─┬─ abs → seed_weight = 0.6
#                      └─ 부호 → direction  = "cost"
#
#   ⚠ 희소 판정은 여기서 표시할 수 없다. detect_sparse 는 [B] 지표 행렬이
#     있어야 계산되는데 [W] 는 그 앞이다. 대신 레코드 수를 근거로 보여준다.
def slider_from_indicators(indicators: list) -> dict:
    """지표 현재 상태 -> 슬라이더 초기값 {id: -1~+1}. cost 는 음수로 표시."""
    return {
        i["id"]: round(
            (-1.0 if i.get("direction") == "cost" else 1.0) * float(i["seed_weight"]), 3
        )
        for i in indicators
    }


def slider_pct(slider: dict) -> dict:
    """슬라이더 -> 정규화 비중 %. **abs 기준**이라 감점 지표도 양수 %가 된다.

    그냥 sum() 으로 나누면 감점이 분모를 깎아 합계가 100%가 안 된다.
      sum : 0.70 + 0.75 + (-0.55) = 0.90  ->  78% + 83% - 61%
      abs : 0.70 + 0.75 +   0.55  = 2.00  ->  35% + 37.5% + 27.5% = 100%
    """
    tot = sum(abs(float(v)) for v in slider.values())
    if tot <= 0:
        return {k: 0.0 for k in slider}
    return {k: abs(float(v)) / tot * 100.0 for k, v in slider.items()}


def data_note(ind: dict) -> str:
    """[W] 화면용 데이터 근거 한 줄. 희소 판정이 아니라 레코드 수다."""
    if ind.get("kind") == "admin":
        agg = ind.get("_admin_agg")
        return f"행정동 {len(agg)}종" if agg is not None else "행정동 집계"
    pts = ind.get("_points")
    n = len(pts) if pts is not None else 0
    return f"{n:,}건" + (" × 값" if ind.get("val_dataset") else "")


def apply_weight_hitl(indicators: list, slider: dict, sources="hitl") -> None:
    """[W] 확정값을 지표에 반영. 슬라이더를 (크기, 방향) 으로 분해한다.

    sources : str(전체 동일) 또는 {id: "llm"|"hitl"|"cli"}
    """
    ids = {i["id"] for i in indicators}
    unknown = set(slider) - ids
    if unknown:
        raise ValueError(f"없는 지표 ID: {sorted(unknown)}\n  사용 가능: {sorted(ids)}")

    tot = sum(abs(float(v)) for v in slider.values())
    if tot <= 0:
        raise ValueError(
            f"가중치 절대값 합이 {tot} 입니다 — 최소 하나는 0이 아니어야 합니다.\n"
            f"  입력값: {slider}\n"
            f"  전부 0이면 모든 후보 점수가 0이 되어 순위가 무의미해집니다."
        )

    at = datetime.now().isoformat(timespec="seconds")
    src_of = (
        (lambda k: sources)
        if isinstance(sources, str)
        else (lambda k: sources.get(k, "llm"))
    )

    for i in indicators:
        v = slider.get(i["id"])
        if v is None:
            continue
        v = float(v)
        if not (-1.0 <= v <= 1.0):
            raise ValueError(f"[{i['id']}] 슬라이더 범위는 -1 ~ +1 입니다: {v}")

        src = src_of(i["id"])
        # 0 은 '그 지표 제외' — 방향이 무의미하므로 원래 값을 유지한다.
        new_dir = i["direction"] if v == 0 else ("cost" if v < 0 else "benefit")
        if new_dir != i["direction"]:
            i["direction_source"] = src
        i["direction"] = new_dir
        i["seed_weight"] = round(abs(v), 3)
        i["w_human_source"] = src
        i["adjusted_at"] = at if src != "llm" else i.get("adjusted_at")
