# -*- coding: utf-8 -*-
"""[A] 지표 정의 — 감리 role + whitelist_resolved 로 지표 K개"""
from __future__ import annotations

import numpy as np

from .state import NEUTRAL_SEED


def _seed_magnitude(w, did: str = "", role: str = "") -> float:
    """role['weight'] -> seed 크기(항상 >=0).

    방향은 role 이 정하고 크기는 |weight| 다. 부호가 role 과 어긋나면
    LLM 출력 형식 오류이므로 크기만 취하되 조용히 넘기지 않고 알린다.
    """
    if w is None:
        return NEUTRAL_SEED
    w = float(w)
    if (role == "positive_factor" and w < 0) or (role == "negative_factor" and w > 0):
        print(
            f"  ⚠ [{did}] {role} 인데 weight={w:+g} — 부호가 role 과 어긋납니다. "
            f"크기 {abs(w):g} 만 사용합니다."
        )
    return abs(w)


def define_indicators(reviewed: dict, report: dict) -> list:
    """positive/negative role 데이터셋을 지표로. WR 있으면 좌표레이어(+)통계표 병합."""
    # 🔴 `whitelist_resolved` 는 **성공·실패가 섞인** 목록이다(gam2_clean_data.py:509~578):
    #   순환참조 기록 {"whitelist","reason"} · 해결실패 기록 {"reason","whitelist",...} ·
    #   성공 기록 {"from_dataset","from_column","key_col","normalize",...}.
    #   `wr[0]` 만 보면 ⓐ 0번이 실패 기록일 때 아래 `wr["from_dataset"]` 이 KeyError 이고
    #   ⓑ 뒤쪽에 있는 진짜 병합 관계가 조용히 버려진다. 생산자 쪽(clean_data:608)은
    #   이미 전량을 순회하며 `.get("from_dataset")` 으로 고른다 — 소비자만 어긋나 있었다.
    wr_by_id = {}
    for r in report.get("results", []):
        usable = [w for w in (r.get("whitelist_resolved") or []) if w.get("from_dataset")]
        if not usable:
            continue
        if len(usable) > 1:
            # 어느 좌표레이어에 붙일지 코드가 고를 수 없다 — 고르면 나머지가 소리 없이 사라진다.
            raise ValueError(
                f"[{r['dataset_id']}] 병합 대상 좌표레이어가 {len(usable)}개입니다 "
                f"({', '.join(w['from_dataset'] for w in usable)}).\n"
                f"  지표를 하나로 정할 수 없습니다 — clean_report.json 의 해당 데이터셋 "
                f"whitelist_resolved 를 확인하세요."
            )
        wr_by_id[r["dataset_id"]] = usable[0]

    pos = {}
    for r in reviewed.get("results", []):
        did = r["dataset_id"]
        for role in r.get("roles") or []:
            rt = role.get("role")
            if rt == "positive_factor":
                new = {
                    "seed_weight": _seed_magnitude(role.get("weight"), did, rt),
                    "direction": "benefit",
                    "rationale": role.get("rationale", ""),
                }
            elif rt == "negative_factor":
                new = {
                    "seed_weight": _seed_magnitude(role.get("weight"), did, rt),
                    "direction": "cost",
                    "rationale": role.get("rationale", ""),
                }
            else:
                continue
            # 같은 데이터셋에 상반된 역할이 동시에 붙으면 이전 코드는 뒤엣것으로
            # 조용히 덮어썼다(roles 리스트 순서에 결과가 좌우됨). 즉시 중단한다.
            prev = pos.get(did)
            if prev and prev["direction"] != new["direction"]:
                raise ValueError(
                    f"[{did}] 한 데이터셋에 상반된 역할이 동시에 판정됐습니다 "
                    f"({prev['direction']} / {new['direction']}).\n"
                    f"  감리 결과가 모순됩니다 — reviewed.json 의 해당 dataset "
                    f"roles 를 하나로 정리하세요."
                )
            pos[did] = new

    consumed = {wr["from_dataset"] for wr in wr_by_id.values()}

    indicators = []
    for did, meta in pos.items():
        if did in consumed:
            continue
        wr = wr_by_id.get(did)
        if wr:
            geo_id = wr["from_dataset"]
            val_id = did
            geo_meta = pos.get(geo_id, {})
            geo_seed = geo_meta.get("seed_weight")
            geo_dir = geo_meta.get("direction")
            seed = np.mean(
                [s for s in [geo_seed, meta["seed_weight"]] if s is not None]
            )
            # 🔴 방향 충돌 — seed_weight 는 둘을 평균하는데 direction 은 val 쪽만 쓴다.
            #   geo 판정이 조용히 사라지므로 플래그를 남겨 [W] HITL 에서 사람이 확정한다.
            #   여기서 규칙으로 정하지 않는 이유: 어느 쪽이 옳은지는 도메인마다 다르다.
            conflict = None
            if geo_dir and geo_dir != meta["direction"]:
                conflict = {
                    "geo_dataset": geo_id,
                    "geo_direction": geo_dir,
                    "val_dataset": val_id,
                    "val_direction": meta["direction"],
                }
            indicators.append(
                {
                    "id": f"{geo_id}+{val_id}",
                    "kind": None,
                    "geo_dataset": geo_id,
                    "val_dataset": val_id,
                    "join": {
                        "geo_key": wr["from_column"],
                        "val_key": wr["key_col"],
                        "normalize": wr.get("normalize", "none"),
                    },
                    "seed_weight": round(float(seed), 3),
                    "direction": meta["direction"],
                    "rationale": meta["rationale"],
                    "direction_conflict": conflict,
                    "direction_llm": meta["direction"],
                    "direction_source": "llm",
                    "w_human_source": "llm",
                    "adjusted_at": None,
                }
            )
        else:
            indicators.append(
                {
                    "id": did,
                    "kind": None,
                    "geo_dataset": did,
                    "val_dataset": None,
                    "join": None,
                    "seed_weight": meta["seed_weight"],
                    "direction": meta["direction"],
                    "rationale": meta["rationale"],
                    "direction_conflict": None,
                    "direction_llm": meta["direction"],
                    "direction_source": "llm",
                    "w_human_source": "llm",
                    "adjusted_at": None,
                }
            )
    return indicators
