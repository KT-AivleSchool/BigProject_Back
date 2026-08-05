# -*- coding: utf-8 -*-
"""
OmniSite 시설 파라미터 판정 (STEP 4)
=====================================
시설의 물리적 규격 3종을 LLM 이 제안하고, 범위 검증 후 캐시한다.

  설치_소요_폭_m  부스 본체가 들어갈 최소 폭     -> 내접폭 필터
  서비스_반경_m    이용자가 접근하는 통상 거리    -> MCLP R_cover
  최소_이격_m      인접 시설 간 최소 거리        -> d_min

왜 코드에 안 박는가
  흡연부스 2m / 재활용정거장 3~4m / EV충전소 5m+ 로 도메인마다 다르다.
  "엔진 고정, 데이터 교체" 를 지키려면 시설에 종속된 값을 코드에 두면 안 된다.

왜 HITL 이 아닌가
  배제반경과 달리 **틀려도 위법이 아니다** — 후보 수와 분산이 달라질 뿐이다.
  그래서 자동 수용하되 범위 검증으로 터무니없는 값만 막는다.
  최종 판단은 결과화면 슬라이더에서 담당자가 한다(설계 확정 턴23).

  ※ 이 값들은 **점수를 바꾸지 않는다.** 필터와 선정 규칙에만 쓰이므로
    결과화면에서 조절해도 점수 재계산이 필요 없다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

try:
    from app.config import (OPENAI_API_KEY, AUDIT_LLM_MODEL,
                            FACILITY_PARAM_CACHE_PATH)
except Exception:                                   # 단독 실행 폴백
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    AUDIT_LLM_MODEL = "gpt-4o"
    FACILITY_PARAM_CACHE_PATH = os.path.join(".", "facility_params_cache.json")

# 키: (최소, 최대, 폴백, 설명)
#   범위는 '물리적으로 말이 되는가' 선이지 도메인 지식이 아니다.
#   폭 50m 짜리 흡연부스나 반경 10km 서비스는 어떤 시설이든 오류다.
PARAM_SPEC = {
    "설치_소요_폭_m": (0.5, 20.0, 2.0, "시설 본체가 들어갈 최소 폭"),
    "서비스_반경_m": (10.0, 5000.0, 150.0, "이용자가 접근하는 통상 거리"),
    "최소_이격_m": (5.0, 5000.0, 100.0, "인접 시설 간 최소 거리"),
}


def cache_path(path: str | None = None) -> str:
    return path or FACILITY_PARAM_CACHE_PATH


def _load_cache(path: str) -> dict:
    if os.path.isfile(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception as e:
            print(f"  ⚠ 캐시 읽기 실패({e}) — 새로 판정합니다")
    return {}


def _save_cache(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _call_llm(facility: str, model: str) -> dict:
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY 없음 — 시설 파라미터 판정 불가.\n"
            "  키를 설정하거나 CLI 인자(--min-width/--r-cover/--dmin)로 직접 지정하세요.")
    from openai import OpenAI

    spec = "\n".join(f"  {k}: {d} (범위 {lo:g}~{hi:g})"
                     for k, (lo, hi, _, d) in PARAM_SPEC.items())
    prompt = (
        f"공공시설 '{facility}' 의 입지 선정에 쓸 물리적 파라미터를 정하라.\n\n"
        f"[요청 항목]\n{spec}\n\n"
        f"[판단 기준]\n"
        f"1. 해당 시설의 **통상적인 설치 규격과 이용 행태**를 근거로 하라.\n"
        f"2. 서비스_반경은 이용자가 이 시설을 쓰려고 이동하는 거리다. "
        f"도보 이용 시설이면 수백 m, 차량 이용 시설이면 km 단위가 된다.\n"
        f"3. 최소_이격은 서비스_반경보다 크지 않아야 한다 "
        f"(크면 시설 사이에 사각지대가 생긴다).\n"
        f"4. 법적 규제 거리(금연구역 이격 등)는 여기서 다루지 마라. 별도 처리한다.\n\n"
        f'JSON 하나만: {{"설치_소요_폭_m": <숫자>, "서비스_반경_m": <숫자>, '
        f'"최소_이격_m": <숫자>, "근거": "<두 문장 이내>"}}'
    )
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=model, temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}])
    return json.loads(resp.choices[0].message.content)


def _validate(raw: dict) -> tuple[dict, list]:
    """범위 검증. 벗어나면 폴백값으로 대체하고 플래그를 남긴다(fail loudly).

    LLM 제안값은 실행마다 흔들린다 — 집계반경 R 이 temperature=0 인데도
    250->400 으로 튄 이력이 있다. 여기 값들은 Top-N 구성을 직접 좌우하므로
    범위 밖은 받지 않는다.
    """
    out, flags = {}, []
    for k, (lo, hi, fb, _) in PARAM_SPEC.items():
        v = raw.get(k)
        try:
            v = float(v)
        except (TypeError, ValueError):
            out[k] = fb
            flags.append(f"[{k}] 값 없음/형식오류 → 폴백 {fb:g}")
            continue
        if not (lo <= v <= hi):
            out[k] = fb
            flags.append(f"[{k}] {v:g} 는 범위 {lo:g}~{hi:g} 밖 → 폴백 {fb:g}")
        else:
            out[k] = v

    # 일관성: 이격 > 서비스반경이면 시설 사이에 사각지대가 생긴다.
    #   틀렸다고 단정하지 않고 플래그만 남긴다 — 정책적으로 선택할 수도 있다.
    if out["최소_이격_m"] > out["서비스_반경_m"]:
        flags.append(f"[일관성] 최소이격({out['최소_이격_m']:g}m) > "
                     f"서비스반경({out['서비스_반경_m']:g}m) — 사각지대 발생 가능")
    return out, flags


def judge(facility: str, model: str | None = None, path: str | None = None,
          force: bool = False, verbose: bool = True) -> dict:
    """시설 파라미터 판정. 캐시가 있으면 LLM 호출 없이 반환.

    캐시 키는 시설명 + 모델. 모델이 바뀌면 재판정한다
    (mini 가 주유소용지를 오판한 것처럼 판정 품질이 모델에 좌우된다).
    """
    p = cache_path(path)
    cache = _load_cache(p)
    hit = cache.get(facility)
    m = model or AUDIT_LLM_MODEL

    if hit and not force:
        if hit.get("model") == m and set(hit.get("params", {})) == set(PARAM_SPEC):
            if verbose:
                print(f"  [시설 파라미터] 캐시 사용 ({hit.get('judged_at','')[:19]}, {m})")
            return hit
        if verbose:
            print(f"  [시설 파라미터] 캐시 불일치 → 재판정")

    if verbose:
        print(f"  [시설 파라미터] {m} 호출")
    raw = _call_llm(facility, m)
    params, flags = _validate(raw)

    rec = {"judged_at": datetime.now().isoformat(timespec="seconds"),
           "model": m, "facility": facility, "params": params,
           "근거": str(raw.get("근거", ""))[:200], "flags": flags}
    cache[facility] = rec
    _save_cache(p, cache)
    if verbose:
        print(f"  [시설 파라미터] 캐시 저장: {p}")
    return rec


def print_params(rec: dict) -> None:
    ps = rec.get("params", {})
    print("\n" + "=" * 66)
    print(f"[시설 파라미터] {rec.get('facility','')} · {rec.get('model','')}")
    print("=" * 66)
    for k, (lo, hi, _, desc) in PARAM_SPEC.items():
        print(f"  {k:<16} {ps.get(k, 0):>8,.1f}   {desc}")
    if rec.get("근거"):
        print(f"\n  근거: {rec['근거']}")
    for f in rec.get("flags") or []:
        print(f"  ⚠ {f}")
    print("=" * 66)
