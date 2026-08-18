# -*- coding: utf-8 -*-
"""데이터 의도(`data_intent_unclear`) 답변을 `result['roles']` 에 결정론적으로 반영.

패키지 안 어느 것도 import 하지 않는다 — 답을 받아 역할을 다시 쓰는 것이 전부다.
부호는 **선택**이 정하고 크기는 **입력값**이 정한다(규약: seed_weight 는 크기만).
"""
from __future__ import annotations

def _read_int(prompt: str, lo: int, hi: int) -> int:
    while True:
        try:
            v = int(input(prompt).strip())
            if lo <= v <= hi:
                return v
        except (ValueError, EOFError):
            pass
        print(f"  → {lo}~{hi} 사이 숫자로 입력하세요.")


def _read_weight() -> float:
    while True:
        try:
            return max(-1.0, min(1.0, float(input("  가중치 크기(-1~1): ").strip())))
        except (ValueError, EOFError):
            print("  → -1~1 사이 숫자로 입력하세요.")


def apply_intent_answer(result: dict, choice: int, weight: float | None = None) -> None:
    """data_intent_unclear 답변(1~5)을 result['roles']에 결정론적으로 반영. 새 필드 없음.
    부호는 선택이 정하고 크기는 입력값(가점=+, 감점=-)."""
    if choice == 1:  # 가점
        result["roles"] = [
            {
                "role": "positive_factor",
                "weight": abs(weight),
                "rationale": "HITL 확정",
                "confirmed": True,
            }
        ]
    elif choice == 2:  # 감점
        result["roles"] = [
            {
                "role": "negative_factor",
                "weight": -abs(weight),
                "rationale": "HITL 확정",
                "confirmed": True,
            }
        ]
    elif choice == 3:  # 배제 (드묾: 표시만, 반경은 추후)
        result["roles"] = [
            {
                "role": "hard_exclusion",
                "exclusion_type": "radius",
                "facility_type": None,
                "배제반경_m": None,
                "source": None,
                "confirmed": False,
                "need_review": True,
                "rationale": "HITL 배제 승격 — 반경 미정(추후 확인)",
            }
        ]
    elif choice == 4:  # 위치선정 참조용(감리 입력 아님) — reference_only 유지
        result["roles"] = [
            {
                "role": "reference_only",
                "confirmed": True,
                "rationale": "HITL — 위치선정 참조용",
            }
        ]
    else:  # 5 제외
        result["roles"] = []
