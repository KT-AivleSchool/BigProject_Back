# -*- coding: utf-8 -*-
"""[R] 집계반경 제안 — mini 호출 · 목"""
from __future__ import annotations

import json

from .state import OPENAI_API_KEY, SEARCH_LLM_MODEL


# =========================================================
# [R] 집계반경 제안 (mini) — 하드코딩 방지
# =========================================================
def suggest_radius(facility: str, indicators: list, model: str = None) -> dict:
    """facility 와 지표별 rationale 을 mini 에게 주고 집계반경 R(m) 을 제안받는다.
    반환: {indicator_id: {"radius_m": int|None, "rationale": str}} (confirmed=False).
    'admin' 지표는 반경 개념이 없으므로 None.  HITL 에서 확정 후 build_matrix 에 주입.

    조례에 없는 값(도보 동선 등 상식)이라 배제반경과 달리 법에서 못 뽑는다 ->
    시설 특성 기반 LLM 제안 + 사람 확정.  도메인마다 자릿수가 다르다
    (흡연 150 / 재활용 50~100 / EV 500~1000).
    """
    askable = [i for i in indicators if i.get("kind") != "admin"]
    payload = [{"id": i["id"], "설명": i.get("rationale", "")[:120]} for i in askable]

    if not OPENAI_API_KEY:
        return _mock_radius(facility, indicators)

    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    m = model or SEARCH_LLM_MODEL
    prompt = (
        f"'{facility}' 입지 분석에서, 각 지표의 '수요 집계 반경(미터)'을 제안하라.\n"
        f"집계 반경 = 후보지 주변 몇 m 안의 해당 요소를 그 후보의 수요로 합칠지의 거리다.\n"
        f"시설 특성에 따라 자릿수가 다르다. 예: 도보로 잠깐 들르는 흡연부스 ~150m, "
        f"무거운 재활용을 들고 나오는 재활용정거장 ~50~100m, 차로 가는 EV충전소 ~500~1000m.\n"
        f"지표 성격도 반영하라(광역 유동인구는 넓게, 국소적 요소는 좁게).\n\n"
        f"[지표] {json.dumps(payload, ensure_ascii=False)}\n\n"
        f'JSON 하나만: {{"<id>": {{"radius_m": <정수>, "rationale": "<한 문장>"}}, ...}}'
    )
    try:
        resp = client.chat.completions.create(
            model=m,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        out = json.loads(resp.choices[0].message.content)
    except Exception as e:
        print(f"  [R 제안 오류] {e} -> mock")
        return _mock_radius(facility, indicators)

    result = {}
    for i in indicators:
        if i.get("kind") == "admin":
            result[i["id"]] = {
                "radius_m": None,
                "rationale": "행정동 단위 지표(반경 무관)",
            }
        else:
            r = out.get(i["id"], {})
            result[i["id"]] = {
                "radius_m": r.get("radius_m"),
                "rationale": r.get("rationale", ""),
            }
    result["_confirmed"] = False
    return result


def _mock_radius(facility: str, indicators: list) -> dict:
    """키 없을 때 — 흡연 기준 기본값(수요형 150 / 국소형 100)."""
    demand = ("버스", "지하철", "정류", "역", "인구")
    out = {}
    for i in indicators:
        if i.get("kind") == "admin":
            out[i["id"]] = {"radius_m": None, "rationale": "행정동 단위(반경 무관)"}
        else:
            is_demand = any(k in i.get("rationale", "") for k in demand)
            out[i["id"]] = {
                "radius_m": 150 if is_demand else 100,
                "rationale": "(mock) 수요형 150 / 국소형 100",
            }
    out["_confirmed"] = False
    return out
