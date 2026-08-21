# -*- coding: utf-8 -*-
"""§3 판정 하네스 — 프로파일 한 건을 프롬프트로 만들어 LLM 에 묻고 결과를 모은다."""
from __future__ import annotations

import json
from dataclasses import dataclass

from .exclusions import enrich_hitl_flags
from .llm import LLMClient
from .prompts import build_prompt

# ══════════════════════════════════════════════════════════════════
# 3. 채점 — 역할 적중 / op 집합 적중 / 누락·과잉
# ══════════════════════════════════════════════════════════════════


@dataclass
class Judgment:
    """감리 AI 판정 1건. (참고값 채점은 제거 — 실제 검토 관문은 HITL)"""

    dataset_id: str
    summary: str
    roles: list  # [{role, weight|배제반경_m, ...}]
    coord_status: str
    ops: list
    exclusions: list  # hard_exclusion 중 조례에 반경 없어 검토 필요한 것


def review_one(pred: dict, dataset_id: str) -> Judgment:
    summary = pred.get("summary", "")
    roles = pred.get("roles", [])
    coord = pred.get("coord_status", "")
    ops = [op["op_id"] for op in pred.get("cleaning_ops", [])]
    # 배제반경 미확정(조례에 없음) → 검토/서핑 대상
    exclusions = [
        r
        for r in roles
        if r.get("role") == "hard_exclusion"
        and (r.get("need_review") or r.get("배제반경_m") is None)
    ]
    return Judgment(
        dataset_id=dataset_id,
        summary=summary,
        roles=roles,
        coord_status=coord,
        ops=ops,
        exclusions=exclusions,
    )


def run_harness(llm: LLMClient, fixtures: dict, domain: dict, progress=None):
    """감리 AI 판정을 수집. 반환: (judgments, raw_preds).
    progress: 선택. 각 데이터셋 처리 후 호출되는 콜백(did) — 진행바(tqdm) 연결용.
    """
    out, raw_preds = [], {}
    for did, profile in fixtures.items():
        prompt = build_prompt(profile, domain, fixtures=fixtures)
        raw = llm.complete(prompt["system"], prompt["user"])
        try:
            pred = json.loads(raw)
        except json.JSONDecodeError:
            pred = {
                "dataset_id": did,
                "summary": "(파싱 실패)",
                "roles": [],
                "coord_status": "",
                "cleaning_ops": [],
                "hitl_flags": [],
                "_raw": raw[:200],
            }
        pred.setdefault("dataset_id", did)
        pred = (
            enrich_hitl_flags(  # 배제반경 null·지역코드 등 → hitl_flags 자동 생성(코드)
                pred, region=(domain or {}).get("region", ""), fixtures=fixtures
            )
        )
        raw_preds[did] = pred
        out.append(review_one(pred, did))
        if progress:
            progress(did)
    return out, raw_preds
