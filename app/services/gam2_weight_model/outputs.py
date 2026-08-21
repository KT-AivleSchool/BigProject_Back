# -*- coding: utf-8 -*-
"""[F] 산출물 — 지문 · weight_set · 제안본 저장"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime

from .state import SPARSE_THRESHOLD, WEIGHT_OUTPUT_DIR
from .hitl import data_note


# =========================================================
# [F] weight_set 조립 + 저장 (DB 이관 전 JSON)
# =========================================================
def fingerprint(path: str | None) -> dict | None:
    """입력 파일 지문. **어느 데이터로 계산된 가중치인지** 특정한다.

    왜 필요한가 — STEP4 의 check_consistency 는 지표 ID·kind·구성 데이터셋만 본다.
    정제를 다시 돌려 clean 산출물 내용이 바뀌어도 **구조는 그대로**라 통과한다.
    그러면 옛 데이터로 만든 가중치로 새 데이터를 점수화하게 되는데, 조용히 지나간다.

    대상은 제어 파일 3개(reviewed · clean_report · 후보)로 한정한다.
    정제 산출물 본체(버스 127만행 등)까지 해시하면 실행마다 수 초가 붙는데,
    clean_report 를 해시하면 정제 재실행 자체는 어차피 잡힌다.
    """
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    st = os.stat(path)
    return {
        "file": os.path.basename(path),
        "sha256": h.hexdigest(),
        "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        "size": int(st.st_size),
    }


def build_weight_set(
    domain: str,
    facility: str,
    region: str,
    indicators: list,
    radius_conf: dict,
    alpha: float,
    w_human: dict,
    w_critic: dict,
    w_final: dict,
    boot: dict,
    sparse_ids: set,
    n_candidates: int,
    engine_version: str = "wm-1.0",
    candidate_unit: str | None = None,
    candidate_source: dict | None = None,
    inputs: dict | None = None,
    hitl: dict | None = None,
) -> dict:
    """DB 한 행이 될 dict. '왜 이 값인가' 근거를 전부 동봉(B2G 설명책임).

    candidate_unit / candidate_source
      후보 1건이 무엇인지는 **코드가 알 수 없는 도메인 지식**이다.
      과거엔 "국유부동산 필지" 가 박혀 있었는데, 후보가 지적도 42,216필지로
      바뀐 뒤에도 그대로 찍혀 산출물이 존재하지 않는 숫자를 주장했다.
      호출부가 주입하고, 없으면 후보 파일에서 사실만 기술한다.

    generated_at / inputs
      **언제, 무엇으로** 만든 파일인지. 없으면 여러 실행분을 구분할 수 없다.
      설계노트 6절의 '인수인계 표 ↔ JSON 불일치' 가 이것 때문에 원인 규명이 늦었다.

    hitl / radius_source / w_human_source
      **누가 정했나.** 대외 설명이 "사람 70% / 데이터 30%" 인데 실제로는 LLM 값이
      들어갈 수 있으므로, 값마다 출처를 남겨야 그 주장이 방어된다.
      radius_source 가 없으면 --radius 로 덮어쓴 뒤에도 rationale 은 LLM 제안값
      기준 문장이 남아 산출물이 앞뒤 안 맞는 근거를 주장하게 된다.
    """
    return {
        "domain": domain,
        "facility": facility,
        "region": region,
        "engine_version": engine_version,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": inputs,
        "hitl": hitl,
        "alpha": alpha,
        "n_candidates": n_candidates,
        "candidate_unit": candidate_unit or "미기록",
        "candidate_source": candidate_source,
        "indicators": [
            {
                "id": i["id"],
                "kind": i["kind"],
                "direction": i["direction"],
                "components": {"geo": i["geo_dataset"], "val": i["val_dataset"]},
                "radius_m": radius_conf.get(i["id"], {}).get("radius_m"),
                "radius_source": radius_conf.get(i["id"], {}).get("source", "llm"),
                "radius_rationale": radius_conf.get(i["id"], {}).get("rationale", ""),
                "seed_rationale": i.get("rationale", ""),
                "sparse_excluded": i["id"] in sparse_ids,
                "w_human": round(w_human.get(i["id"], 0), 4),
                # 출처 — "사람 70%" 를 방어하려면 누가 정했는지가 근거가 된다(B2G 설명책임).
                "w_human_source": i.get("w_human_source", "llm"),
                "direction_source": i.get("direction_source", "llm"),
                "direction_llm": i.get("direction_llm", i["direction"]),
                "direction_conflict": i.get("direction_conflict"),
                "adjusted_at": i.get("adjusted_at"),
                "w_critic": None
                if i["id"] in sparse_ids
                else round(w_critic.get(i["id"], 0), 4),
                "w_critic_ci": boot.get(i["id"]),
                "w_final": round(w_final.get(i["id"], 0), 4),
            }
            for i in indicators
        ],
        "notes": {
            "critic_method": "Spearman-CRITIC",
            "sparse_threshold": SPARSE_THRESHOLD,
            "sparse_excluded_ids": sorted(sparse_ids),
            "weight_meaning": "지표 간 상대 중요도(평가단위 독립). 위치선정이 이 값으로 후보 점수화.",
        },
    }


def save_weight_set(ws: dict, domain: str) -> str:
    os.makedirs(WEIGHT_OUTPUT_DIR, exist_ok=True)
    path = os.path.join(WEIGHT_OUTPUT_DIR, f"{domain}_weight_set.json")
    json.dump(ws, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return path


def build_weight_proposal(
    domain: str,
    facility: str,
    region: str,
    indicators: list,
    radius_conf: dict,
    slider: dict,
    run_id: str | None = None,
) -> dict:
    """[W] 게이트 화면에 보여줄 **제안값**. 확정값이 아니다.

    왜 별도 산출물인가 —
      [R] 반경 제안과 [W] 슬라이더 초기값은 `run_weight_model` 프로세스 **안에서만**
      만들어진다(LLM 제안 + define_indicators). 파일로 꺼내지 않으면 사람에게
      "AI 가 뭘 제안했는지"를 보여줄 방법이 없고, 그러면 원칙 3(LLM 제안 → 사람 확정)이
      화면에서 성립하지 않는다.

    weight_set 과 이름이 비슷하지만 **성격이 반대**다:
      · weight_set      = 확정 결과 (w_human·w_critic·w_final)
      · weight_proposal = 확정 **전** 제안 (radius_proposed·slider_proposed)
    그래서 w_* 를 담지 않는다. 담으면 확정 전 값이 확정값인 척한다(원칙 4).

    `conflicts` 는 define_indicators 가 reviewed.json 만으로 판정한다(:300).
    반경·CRITIC 과 접점이 없으므로 이 시점에 전부 알 수 있다.
    """
    return {
        "run_id": run_id,
        "domain": domain,
        "facility": facility,
        "region": region,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "indicators": [
            {
                "id": i["id"],
                "kind": i["kind"],
                "direction": i["direction"],
                "seed_weight": i["seed_weight"],
                "components": {"geo": i["geo_dataset"], "val": i["val_dataset"]},
                "rationale": i.get("rationale", ""),
                "data_note": data_note(i),
            }
            for i in indicators
        ],
        "radius_proposed": {
            i["id"]: {
                "radius_m": radius_conf.get(i["id"], {}).get("radius_m"),
                "rationale": radius_conf.get(i["id"], {}).get("rationale", ""),
                "source": radius_conf.get(i["id"], {}).get("source", "llm"),
            }
            for i in indicators
        },
        "slider_proposed": {i["id"]: slider[i["id"]] for i in indicators},
        "conflicts": [
            {
                "indicator_id": i["id"],
                **i["direction_conflict"],
            }
            for i in indicators
            if i.get("direction_conflict")
        ],
    }


def save_weight_proposal(prop: dict, domain: str, run_id: str | None = None) -> str:
    """weight_set 과 **같은 디렉터리**에 저장한다(save_weight_set 규칙 재사용).

    별도 경로를 파면 mock/실제 오염 방지 규칙을 한 곳 더 관리해야 한다.
    run_id 가 있으면 파일명에 붙인다 — API 는 run 마다 이 디렉터리를 가르지만,
    CLI 로 직접 부르면 한 폴더에 여러 실행분이 쌓이기 때문이다.
    """
    os.makedirs(WEIGHT_OUTPUT_DIR, exist_ok=True)
    name = f"{domain}_weight_proposal{'_' + run_id if run_id else ''}.json"
    path = os.path.join(WEIGHT_OUTPUT_DIR, name)
    json.dump(prop, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return path
