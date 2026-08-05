# -*- coding: utf-8 -*-
"""
OmniSite 지목 판정 (STEP 4)
============================
연속지적도의 지목별로 "이 시설을 놓을 수 있는 땅인가"를 LLM 이 판정한다.

  판정 대상 : 지목 21~28종 (필지 4만 개가 아니다 — 호출 1회로 끝난다)
  캐시      : 시설명 기준. 같은 시설이면 두 번째 실행부터 LLM 호출 0회.
  HITL      : 없음. 하천에 못 놓는다는 건 물리적 사실이라 확인이 불필요하고,
              틀려도 결과에 바로 드러난다.

왜 코드에 안 박는가
  팀원 노트북에 `g['지목'].isin(['도','잡'])` 이 박혀 있었다. 흡연부스엔 맞아도
  재활용정거장·EV충전소로 바꾸면 틀린다. "엔진 고정, 데이터 교체" 가 깨지는 지점.

게이트 (설계 확정, 턴13)
  role = candidate / unusable      -> 자동 수용 (숫자 없음, 물리적 판단)
  role = hard_exclusion + 반경숫자 -> **자동 수용 금지.** 플래그만 남기고 candidate 취급.
  규제 배제는 사용자 데이터 + 감리(reviewed.json) 경로에서만 나와야 한다.
  지적도에서 규제를 파생시키면 감리 경로를 우회하게 된다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

try:
    from app.config import OPENAI_API_KEY, SEARCH_LLM_MODEL, JIMOK_CACHE_PATH
except Exception:                                   # 단독 실행 폴백
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    SEARCH_LLM_MODEL = "gpt-4o-mini"
    JIMOK_CACHE_PATH = os.path.join(".", "jimok_role_cache.json")

# 지목 부호 -> 명칭. 「공간정보의 구축 및 관리 등에 관한 법률 시행령」 제58조의
# 법정 표준이다. 시설 종류와 무관하게 고정 — 도메인 지식이 아니라 참조 데이터다.
#   (LLM 에 '대','도' 같은 한 글자만 주면 오독하므로 명칭을 함께 넘긴다)
JIMOK_NAMES = {
    "전": "전(밭)", "답": "답(논)", "과": "과수원", "목": "목장용지",
    "임": "임야", "광": "광천지", "염": "염전", "대": "대(대지)",
    "장": "공장용지", "학": "학교용지", "차": "주차장", "주": "주유소용지",
    "창": "창고용지", "도": "도로", "철": "철도용지", "제": "제방",
    "천": "하천", "구": "구거(수로)", "유": "유지(연못)", "양": "양어장",
    "수": "수도용지", "공": "공원", "체": "체육용지", "원": "유원지",
    "종": "종교용지", "사": "사적지", "묘": "묘지", "잡": "잡종지",
}

VALID_ROLES = {"candidate", "unusable"}
# 게이트가 만들어내는 제3상태. LLM 이 판정하는 값이 아니다.
#   응답 누락·형식 위반 → unknown → **후보에서 제외**하고 리포트에 남긴다.
#   candidate 로 두면 조용히 후보에 섞인다: 철도용지 1,033필지가 그렇게 들어가면
#   선로 위 필지가 지하철역 근처라 고득점을 받아 Top-N 에 올라올 수 있다.
UNKNOWN = "unknown"


# =========================================================
# 통계 / 캐시
# =========================================================
def jimok_stats(parcels) -> dict:
    """지적도 GeoDataFrame -> {지목: {필지수, 면적중앙, 면적합_km2}}.
    표준 부호가 아닌 것(지목=None)은 제외한다 — load_parcels 가 이미 격리했다."""
    out = {}
    g = parcels[parcels["지목"].notna()]
    for jm, sub in g.groupby("지목"):
        out[jm] = {
            "필지수": int(len(sub)),
            "면적중앙": round(float(sub["면적"].median()), 1),
            "면적합_km2": round(float(sub["면적"].sum()) / 1e6, 3),
        }
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["필지수"]))


def cache_path(path: str | None = None) -> str:
    """캐시 위치. config 규약상 캐시는 SEARCH_CACHE_DIR 에 둔다(결과물과 분리)."""
    return path or JIMOK_CACHE_PATH


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


# =========================================================
# LLM 판정
# =========================================================
def _call_llm(facility: str, stats: dict, model: str) -> dict:
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY 없음 — 지목 판정 불가.\n"
            "  키를 설정하거나, 캐시를 미리 만들어 두거나,\n"
            "  make_parcel_candidates.py --jimok 로 직접 지정하세요.")
    from openai import OpenAI

    items = [{"부호": k, "명칭": JIMOK_NAMES.get(k, k), **v} for k, v in stats.items()]
    prompt = (
        f"'{facility}'를 설치할 부지를 고르려 한다. 아래는 연속지적도의 지목별 현황이다.\n"
        f"각 지목이 이 시설을 놓을 수 있는 땅인지 판정하라.\n\n"
        f"role 은 둘 중 하나다:\n"
        f"  candidate : 설치 가능한 땅\n"
        f"  unusable  : 물리적·기능적으로 설치 불가 "
        f"(예: 하천은 물, 철도용지는 선로, 묘지)\n\n"
        f"[중요]\n"
        f"1. 법적 규제(금연구역·이격거리 등)로 인한 배제는 여기서 판정하지 마라.\n"
        f"   그건 별도 데이터와 조례로 처리한다. 반경·미터 값을 내지 마라.\n"
        f"2. 애매하면 candidate 로 두어라. 규제 배제는 뒤 단계가 따로 거른다.\n"
        f"   잘못 unusable 로 넣으면 그 땅은 영영 후보에서 사라진다.\n"
        f"3. 해당 시설의 통상적 설치 관행을 근거로 판단하라.\n\n"
        f"[지목 현황] {json.dumps(items, ensure_ascii=False)}\n\n"
        f'JSON 하나만 출력: {{"부호": {{"role": "candidate|unusable", '
        f'"이유": "<한 문장>"}}, ...}}'
    )
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=model, temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}])
    return json.loads(resp.choices[0].message.content)


def _apply_gate(raw: dict, stats: dict) -> tuple[dict, list]:
    """LLM 응답 검증. 규격 밖은 candidate 로 강등하고 플래그를 남긴다(fail loudly)."""
    roles, flags = {}, []
    for jm in stats:
        r = raw.get(jm)
        if not isinstance(r, dict):
            roles[jm] = {"role": UNKNOWN, "이유": "응답 누락"}
            flags.append(f"[{jm}] 응답 누락 → unknown (후보 제외)")
            continue
        role = str(r.get("role", "")).strip()
        why = str(r.get("이유", ""))[:120]

        # 게이트 ①: candidate/unusable 이 아니면 유효한 판정이 아니다.
        #   규제성 role(hard_exclusion)도 여기서 걸린다 — 지적도에서 규제를
        #   파생시키면 감리 경로를 우회하게 되므로 받지 않는다.
        if role not in VALID_ROLES:
            flags.append(f"[{jm}] 허용 밖 role '{role}' → unknown (후보 제외) {why[:40]}")
            role = UNKNOWN
        # 게이트 ②: 반경 숫자를 냈다면 규제 판정 시도로 본다
        for k in ("배제반경_m", "radius_m", "반경"):
            if r.get(k) not in (None, ""):
                flags.append(f"[{jm}] 반경값 {r[k]} 제안됨 → 무시 "
                             f"(규제 배제는 감리 경로 전용)")
        roles[jm] = {"role": role, "이유": why}
    # 응답에만 있고 실제 데이터엔 없는 지목
    for jm in set(raw) - set(stats):
        flags.append(f"[{jm}] 데이터에 없는 지목 — 무시")
    return roles, flags


def judge(facility: str, stats: dict, model: str | None = None,
          path: str | None = None, force: bool = False,
          verbose: bool = True) -> dict:
    """지목 판정. 캐시가 있으면 LLM 호출 없이 반환.

    캐시 무효화(설계 확정 Q-j3 (가)): **시설명 기준**.
    지목 부호는 법정 표준이라 지적도 파일이 갱신돼도 의미가 안 변한다.
    단, 캐시에 없는 지목이 새로 나타나면 그때만 재판정한다.
    """
    p = cache_path(path)
    cache = _load_cache(p)
    hit = cache.get(facility)

    m = model or SEARCH_LLM_MODEL

    if hit and not force:
        missing = set(stats) - set(hit.get("roles", {}))
        cached_model = hit.get("model")
        if missing:
            if verbose:
                print(f"  [지목 판정] 캐시에 없는 지목 {sorted(missing)} → 재판정")
        elif cached_model != m:
            # 모델이 바뀌면 판정 품질이 달라진다 — 캐시를 그대로 쓰면
            # "4o 로 돌렸다" 고 믿으면서 실제로는 mini 결과를 쓰게 된다.
            if verbose:
                print(f"  [지목 판정] 캐시 모델 불일치 ({cached_model} != {m}) → 재판정")
        else:
            if verbose:
                print(f"  [지목 판정] 캐시 사용 ({hit.get('judged_at','')[:19]}, "
                      f"{cached_model})")
            return hit

    if verbose:
        print(f"  [지목 판정] {m} 호출 — {len(stats)}종")
    raw = _call_llm(facility, stats, m)
    roles, flags = _apply_gate(raw, stats)

    rec = {"judged_at": datetime.now().isoformat(timespec="seconds"),
           "model": m, "facility": facility, "roles": roles, "flags": flags}
    cache[facility] = rec
    _save_cache(p, cache)
    if verbose:
        print(f"  [지목 판정] 캐시 저장: {p}")
    return rec


# =========================================================
# 출력 / 사용
# =========================================================
def candidate_jimok(rec: dict) -> list:
    """role=candidate 인 지목 부호 목록."""
    return [k for k, v in rec.get("roles", {}).items() if v.get("role") == "candidate"]


def print_judgment(rec: dict, stats: dict) -> None:
    """판정 결과 표시. HITL 은 아니지만 눈으로 확인할 수 있게 출력한다."""
    roles = rec.get("roles", {})
    print("\n" + "=" * 66)
    print(f"[지목 판정] {rec.get('facility','')} · {rec.get('model','')}")
    print("=" * 66)
    for want, label in (("candidate", "설치 가능"), ("unusable", "설치 불가"),
                        (UNKNOWN, "판정 실패 → 후보 제외")):
        sel = [(k, v) for k, v in roles.items() if v.get("role") == want]
        if not sel:
            continue
        sel.sort(key=lambda kv: -stats.get(kv[0], {}).get("필지수", 0))
        n = sum(stats.get(k, {}).get("필지수", 0) for k, _ in sel)
        print(f"\n  {want:<10} {label}   {n:,}필지")
        for k, v in sel:
            s = stats.get(k, {})
            print(f"    {k} {JIMOK_NAMES.get(k,k):<10} {s.get('필지수',0):>7,}필지  "
                  f"{v.get('이유','')[:44]}")
    n_unk = sum(1 for v in roles.values() if v.get("role") == UNKNOWN)
    if n_unk:
        print(f"\n  ⚠ 판정 실패 {n_unk}종 — 후보에서 제외했습니다. "
              f"재실행하려면 --force-jimok")
    flags = rec.get("flags") or []
    if flags:
        print(f"\n  ⚠ 게이트 {len(flags)}건 — 자동 수용하지 않음")
        for f in flags:
            print(f"    {f}")
    print("=" * 66)
