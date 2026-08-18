# -*- coding: utf-8 -*-
"""행정코드 크로스워크 — 코드 접두를 **값 대조**로 판정한다.

🔴 한 파이프라인에 체계가 셋이다(행자부·통계청·법정동). 이름이 비슷해 섞이는데
   직접 조인하면 0건이 아니라 **일부만 맞는다** — 0이면 터지지만 부분일치는 안 터진다.
"""
from __future__ import annotations

import os

from app import config

from .fixtures import build_fixtures

ADM_CODE_SHEET = "행정동코드"  # (폴백) 엑셀 시트명
_ADM_CODE_CACHE: dict | None = None  # {체계: {코드접두: (시도명, 시군구명)}} 세션 캐시

# 시도 표기 흔들림 흡수 — 크로스워크는 '서울특별시', 엑셀은 '서울', 사용자는 '서울시'.
_SIDO_ALIAS = {
    "서울": "서울특별시",
    "서울시": "서울특별시",
    "부산": "부산광역시",
    "대구": "대구광역시",
    "인천": "인천광역시",
    "광주": "광주광역시",
    "대전": "대전광역시",
    "울산": "울산광역시",
    "세종": "세종특별자치시",
    "세종시": "세종특별자치시",
    "경기": "경기도",
    "강원": "강원특별자치도",
    "충북": "충청북도",
    "충남": "충청남도",
    "전북": "전북특별자치도",
    "전남": "전라남도",
    "경북": "경상북도",
    "경남": "경상남도",
    "제주": "제주특별자치도",
}


def _norm_sido(name: str) -> str:
    """'서울' · '서울시' · '서울특별시' 를 한 형태로."""
    n = (name or "").strip()
    return _SIDO_ALIAS.get(n, n)


def split_region(region: str) -> tuple:
    """'서울특별시 용산구' -> ('서울특별시','용산구') · '용산구' -> ('','용산구').
    시도가 없으면 빈 문자열. 지역명은 코드에 박지 않고 문자열에서만 읽는다."""
    toks = [t for t in str(region or "").replace("\u3000", " ").split() if t]
    if not toks:
        return "", ""
    if len(toks) == 1:
        return "", toks[0]
    return _norm_sido(toks[0]), toks[-1]


def _crosswalk_path() -> str:
    """행정동 크로스워크 경로. config 에 없으면 REGION_DATA_DIR 에서 찾는다.

    gam2_weight_model · gam2_audit_ops_catalog 는 이미 이 폴백을 갖고 있는데
    여기만 없었다. config 에 ADMIN_CROSSWALK_PATH 가 정의돼 있지 않으면
    **STEP3 는 되는데 STEP1 감리만 코드 검증이 꺼지는** 상태가 된다.
    (2026-08-03 실측: CW="" → 11440(마포구) 이 검증 없이 HITL 기본값이 됐다)
    """
    p = getattr(config, "ADMIN_CROSSWALK_PATH", "")
    if p:
        return str(p)
    rd = getattr(config, "REGION_DATA_DIR", "")
    return os.path.join(str(rd), "행정동_크로스워크.csv") if rd else ""


def _load_admin_code_map() -> dict:
    """행자부 행정동코드 ↔ 시군구명 매핑을 읽어 {코드접두: 시군구명} 으로 만든다.
    5자리(자치구)와 8자리(행정동) 접두를 모두 담아 어느 길이로 걸러도 검증된다.
    파일이 없으면 빈 dict → 검증을 건너뛰고 HITL 확인만 남는다(조용히 통과시키지 않음).
    """
    global _ADM_CODE_CACHE
    if _ADM_CODE_CACHE is not None:
        return _ADM_CODE_CACHE
    _ADM_CODE_CACHE = {"행자부": {}, "통계청": {}}
    import pandas as pd

    def _put(sys_name, code, sido, gu):
        if not isinstance(code, str):
            return
        code = code.strip()
        if not (code and sido and gu):
            return
        _ADM_CODE_CACHE[sys_name][code] = (sido, gu)
        if len(code) >= 5:
            _ADM_CODE_CACHE[sys_name][code[:5]] = (sido, gu)

    # 1) 크로스워크(전국) 우선
    cw = _crosswalk_path()
    if cw and os.path.isfile(cw):
        try:
            df = pd.read_csv(cw, dtype=str)
            for _, r in df.iterrows():
                sido, gu = _norm_sido(r.get("시도명")), r.get("시군구명")
                _put("행자부", r.get("행정동코드"), sido, gu)
                _put("행자부", r.get("행정동코드8"), sido, gu)
                _put("통계청", r.get("행정구역코드"), sido, gu)
            # 파일은 읽혔는데 표가 비면 **컬럼명이 다른 것**이다.
            #   그냥 반환하면 '표 없음' 과 구분이 안 되고 verify 는 전부 unknown 이
            #   된다 — 조용한 실패다. 실제 컬럼을 찍어 원인을 바로 보게 한다.
            n_gu = len({gu for t in _ADM_CODE_CACHE.values() for _, gu in t.values()})
            if n_gu == 0:
                print(
                    f"  🔴 크로스워크를 읽었으나 코드 0건 — 컬럼명 불일치.\n"
                    f"    파일: {cw}\n"
                    f"    실제 컬럼: {list(df.columns)}\n"
                    f"    필요 컬럼: 시도명 · 시군구명 · "
                    f"행정동코드 / 행정동코드8 / 행정구역코드"
                )
            else:
                print(
                    f"  [코드표] 크로스워크 {len(df):,}행 · 시군구 {n_gu}종 "
                    f"— {os.path.basename(cw)}"
                )
            return _ADM_CODE_CACHE
        except Exception as e:
            print(
                f"  [경고] 크로스워크 로드 실패({e}) — 엑셀 폴백을 시도합니다\n"
                f"    파일: {cw}"
            )

    # 2) 엑셀 폴백 (서울 한정)
    path = str(getattr(config, "ADM_CODE_MAP", "") or "")
    if not path or not os.path.isfile(path):
        # 어느 경로를 봤는지 알려준다. 종전에는 경로를 담은 메시지가
        #   이 early return **뒤**에 있어서, 둘 다 없을 때 끝내 안 보였다.
        print(
            "  [경고] 행정동 코드표 없음 — 코드 검증 없이 HITL 확인만 수행\n"
            f"    크로스워크 : {cw or '(config 에 ADMIN_CROSSWALK_PATH 없음)'}\n"
            f"    엑셀 폴백  : {path or '(config 에 ADM_CODE_MAP 없음)'}\n"
            "    → make_admin_crosswalk.py 로 행정동_크로스워크.csv 를 만드세요."
        )
        return _ADM_CODE_CACHE
    # 폴백이 조용히 발동하면 '전국 3,555동'인 줄 알면서 실제로는 서울 424동만
    # 보게 된다. 서울 밖 도메인에서는 전부 unknown 이 되어 HITL 만 늘어난다.
    print(
        f"  ⚠ 크로스워크 없음({cw or '경로 미설정'}) — 엑셀 폴백 사용(서울 한정).\n"
        f"    전국 대응하려면 make_admin_crosswalk.py 로 크로스워크를 만드세요."
    )
    try:
        df = pd.read_excel(path, sheet_name=ADM_CODE_SHEET, dtype=str, skiprows=1)
        df.columns = [
            "통계청행정동코드",
            "행자부행정동코드",
            "시도명",
            "시군구명",
            "행정동명",
        ][: len(df.columns)]
        for _, r in df.iterrows():
            sido, gu = _norm_sido(r.get("시도명")), r.get("시군구명")
            _put("행자부", r.get("행자부행정동코드"), sido, gu)
            _put("통계청", r.get("통계청행정동코드"), sido, gu)
    except Exception as e:
        print(
            f"  [경고] 행정동 코드표 로드 실패({e}) — 코드 검증 없이 HITL 확인만 수행"
        )
    return _ADM_CODE_CACHE


def verify_code_prefix(prefix: str, region: str) -> tuple:
    """코드 접두가 대상 지역인지 대조. 반환: (판정, 설명문자열|None)

      'ok'        모든 코드 체계에서 대상 지역
      'ambiguous' 체계에 따라 다른 지역 — **자동 확정 금지**, 사람이 판단
      'mismatch'  어떤 체계로도 대상 지역이 아님
      'unknown'   표에 없음

    감리 AI 가 추측한 행정코드를 **데이터로 검증**하는 유일한 수단이다.
    (실제 사고: 용산구인데 11440(마포구)을 써서 데이터 전체가 다른 구였다)

    ⚠ 'ambiguous' 가 필요한 이유 — 11170 은 행자부로 용산구, 통계청으로 구로구다.
      체계를 모른 채 하나로 합쳐 보면 '검증 통과'가 오답이 된다.
    """
    m = _load_admin_code_map()
    pf = str(prefix or "").strip()
    if not m or not pf:
        return "unknown", None
    _, want_gu = split_region(region)
    want_sido, _ = split_region(region)

    hits = {}  # 체계 -> (시도, 시군구)
    for sys_name, table in m.items():
        got = table.get(pf)
        if got:
            hits[sys_name] = got
    if not hits:
        return "unknown", None

    def _same(v):
        sido, gu = v
        if gu != want_gu:
            return False
        return (not want_sido) or (sido == want_sido)

    oks = {k: v for k, v in hits.items() if _same(v)}
    desc = " · ".join(f"{k}={v[0]} {v[1]}" for k, v in hits.items())
    if len(oks) == len(hits):
        return "ok", desc
    if oks:
        return "ambiguous", desc
    return "mismatch", desc


def suggest_code_prefix(region: str, system: str = "행자부") -> str | None:
    """대상 지역명 -> 자치구 5자리 접두. 후보가 정확히 1개일 때만 돌려준다.

    시군구명은 전국에서 유일하지 않다(중구 6 · 동구 6 · 서구 5 · 남구 4 · 북구 4 …).
    region 에 시도가 함께 오면 252종 전부 유일해진다 → 자동 확정이 가능해진다.
    """
    m = _load_admin_code_map().get(system, {})
    want_sido, want_gu = split_region(region)
    if not want_gu:
        return None
    cands = sorted(
        {
            c
            for c, (sido, gu) in m.items()
            if len(c) == 5 and gu == want_gu and ((not want_sido) or sido == want_sido)
        }
    )
    return cands[0] if len(cands) == 1 else None


def region_is_unique(region: str) -> bool:
    """대상 지역이 전국에서 하나로 특정되는가.

    시군구명만으로는 유일하지 않다 — 중구 6 · 동구 6 · 서구 5 · 남구 4 · 북구 4 ·
    고성군 2 · 강서구 2 (전국 230종 중 7종). 시도가 함께 오면 252종 전부 유일해진다.
    유일하지 않으면 코드 검증이 '어느 중구인지' 를 못 가리므로 자동 확정하지 않는다.
    """
    m = _load_admin_code_map()
    w_sido, w_gu = split_region(region)
    if not w_gu:
        return False
    found = set()
    for table in m.values():
        for sido, gu in table.values():
            if gu == w_gu and ((not w_sido) or sido == w_sido):
                found.add((sido, gu))
    return len(found) == 1


_FIXTURE_CACHE: dict | None = None


def _code_samples(
    dataset_id: str, col: str, n: int = 8, fixtures: dict | None = None
) -> list:
    """프로파일 sample_rows 에서 해당 컬럼의 값 표본을 꺼낸다. 실패하면 빈 리스트.
    감리 결과 JSON 에는 표본이 없으므로 fixture(profiles.json)를 읽는다.
    fixtures 를 직접 받으면(감리 중) 다시 로드하지 않는다."""
    global _FIXTURE_CACHE
    if fixtures is not None:
        _FIXTURE_CACHE = _FIXTURE_CACHE or fixtures
        f = fixtures.get(dataset_id) or {}
        return [
            row.get(col)
            for row in (f.get("sample_rows") or [])
            if row.get(col) not in (None, "")
        ][:n]
    if _FIXTURE_CACHE is None:
        try:
            _FIXTURE_CACHE = build_fixtures()
        except Exception as e:
            print(f"  [경고] fixture 로드 실패({e}) — 코드 체계 판정 생략")
            _FIXTURE_CACHE = {}
    f = _FIXTURE_CACHE.get(dataset_id) or {}
    out = []
    for row in f.get("sample_rows") or []:
        v = row.get(col)
        if v not in (None, ""):
            out.append(v)
    return out[:n]


def detect_code_system(values) -> tuple:
    """코드 표본이 어느 체계인지 **데이터로** 가린다. 반환 (체계|None, 설명)

    행자부/통계청은 같은 접두를 다른 구에 쓴다(11170 = 용산구 / 구로구).
    접두만 보면 영원히 못 가리지만, 실제 값은 체계마다 코드표 적중률이 갈린다.
      실측(용산): 생활인구 값 -> 행자부 16/16 · 통계청 7/16
                  경계 SHP 값 -> 행자부  0/16 · 통계청 16/16

    표본 전체를 설명하는 체계가 **정확히 하나**일 때만 결정한다.
    새 임계값을 만들지 않으며, 애매하면 None 을 돌려 사람에게 넘긴다(fail safe).
    """
    m = _load_admin_code_map()
    vals = [str(v).strip() for v in (values or []) if str(v).strip()]
    vals = [v for v in vals if v.isdigit() and len(v) >= 5]
    if not vals or not any(m.values()):
        return None, "코드 표본 없음"
    score = {k: sum(1 for v in vals if v in t or v[:8] in t) for k, t in m.items()}
    desc = " · ".join(f"{k} {v}/{len(vals)}" for k, v in score.items())
    full = [k for k, v in score.items() if v == len(vals)]
    return (full[0] if len(full) == 1 else None), desc


def resolve_code_prefix(prefix: str, region: str, samples=None) -> dict:
    """지역 코드 접두 판정을 **한 곳에서** 내린다. 감리·HITL·프런트가 같은 답을 본다.

    반환(그대로 audit_result.json 의 params.prefix_check 에 실린다):
      status        "auto_confirmed" | "needs_review"   ← 프런트가 볼 값
      verdict       ok | ambiguous | mismatch | unknown
      system        데이터 표본으로 판정된 코드 체계(가릴 수 있었을 때만)
      resolved      이 접두가 실제로 가리키는 '시도 시군구'
      region_unique 대상 지역이 전국에서 유일한가
      suggestion    대상 지역의 자치구 코드(있으면)
      reason        사람이 읽을 판단 근거
    """
    pf = str(prefix or "").strip()
    verdict, detail = verify_code_prefix(pf, region)
    uniq = region_is_unique(region)
    m = _load_admin_code_map()
    out = {
        "status": "needs_review",
        "verdict": verdict,
        "prefix": pf,
        "region": region,
        "region_unique": bool(uniq),
        "system": None,
        "resolved": None,
        "detail": detail,
        "suggestion": suggest_code_prefix(region),
        "reason": "",
    }
    if not any(m.values()):
        out["reason"] = "행정동 코드표 없음 — 검증 불가"
        return out

    def _fill(got):
        out["resolved"] = f"{got[0]} {got[1]}" if got else None

    if verdict == "ok":
        _fill(next((t.get(pf) for t in m.values() if t.get(pf)), None))
        if uniq:
            out["status"] = "auto_confirmed"
            out["reason"] = "코드표 대조 — 이 접두를 아는 모든 체계가 대상 지역"
        else:
            out["reason"] = (
                f"'{region}' 이 전국에서 유일하지 않음 (시도를 함께 적으면 자동 확정)"
            )
        return out

    if verdict == "ambiguous":
        sysname, sdesc = detect_code_system(samples)
        out["system"] = sysname
        out["detail"] = f"{detail} / 표본판정: {sdesc}"
        if not sysname:
            out["reason"] = "코드 체계를 데이터 표본으로 가릴 수 없음"
            return out
        got = m[sysname].get(pf)
        _fill(got)
        w_sido, w_gu = split_region(region)
        same = bool(got) and got[1] == w_gu and ((not w_sido) or got[0] == w_sido)
        if same and uniq:
            out["status"] = "auto_confirmed"
            out["reason"] = f"데이터 표본이 {sysname} 체계로 판정됨"
        elif same:
            out["reason"] = (
                f"'{region}' 이 전국에서 유일하지 않음 (시도를 함께 적으면 자동 확정)"
            )
        else:
            out["reason"] = f"{sysname} 체계에서 이 접두는 대상 지역이 아님"
        return out

    out["reason"] = (
        "대상 지역이 아님" if verdict == "mismatch" else "코드표에 없는 접두"
    )
    return out


def _enrich_code_prefix(pred: dict, region: str, fixtures: dict | None) -> dict:
    """filter_by_code_prefix 판정을 감리 단계에서 미리 내려 결과에 남긴다.

    왜 감리 단계인가 — HITL 실행 중에만 판정하면 `audit_result.json` 만 읽는
    프런트가 '이 항목이 통과인지 확인 대상인지' 알 수 없다.
    """
    flags = pred.setdefault("hitl_flags", [])
    did = pred.get("dataset_id", "")
    for i, op in enumerate(pred.get("cleaning_ops") or []):
        if op.get("op_id") != "filter_by_code_prefix":
            continue
        prm = op.setdefault("params", {})
        chk = resolve_code_prefix(
            prm.get("prefix", ""),
            region,
            _code_samples(did, prm.get("col"), fixtures=fixtures),
        )
        chk["col"] = prm.get("col")
        prm["prefix_check"] = chk
        if chk["status"] == "auto_confirmed":
            prm["prefix_confirmed"] = True
            prm["prefix_confirmed_by"] = "code_table" + (
                f":{chk['system']}" if chk["system"] else ""
            )
            continue
        prm.setdefault("prefix_confirmed", False)
        if not any(
            f.get("type") == "code_prefix_unverified" and f.get("op_index") == i
            for f in flags
        ):
            flags.append(
                {
                    "type": "code_prefix_unverified",
                    "op_index": i,
                    "col": chk["col"],
                    "prefix": chk["prefix"],
                    "verdict": chk["verdict"],
                    "reason": chk["reason"],
                    "detail": chk["detail"],
                    "suggestion": chk["suggestion"],
                    # message 는 요약 출력이 쓰는 공통 필드다(다른 flag 와 동일 규약).
                    "message": (
                        f"'{chk['col']}' 접두 '{chk['prefix']}' — "
                        f"{chk['reason']}"
                        + (f" ({chk['detail']})" if chk.get("detail") else "")
                        + (
                            f" · 제안 '{chk['suggestion']}'"
                            if chk.get("suggestion")
                            else ""
                        )
                    ),
                    "confirmed": False,
                }
            )
    return pred
