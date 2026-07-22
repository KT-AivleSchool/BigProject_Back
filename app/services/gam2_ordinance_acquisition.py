# -*- coding: utf-8 -*-
"""
OmniSite 상위법 배제반경 연계 (search 모드 사용분)
=================================================
조례에 배제반경이 없을 때, 조례가 「」로 인용한 상위법(+시행령·시행규칙)에서
배제반경(m)을 찾는다.
audit_judgment_test.py 의 enrich_with_search()(= search 모드)가 아래 2개를 import:
  · extract_cited_laws   : 조례에서 인용 상위법명 추출
  · find_radius_in_laws  : 상위법 텍스트를 모아 LLM 이 배제반경(m) 판단

키: .env 의 LAW_GO_KR_OC (법제처 OC). 키 없으면 _mock_* 폴백.
법제처 법령 API: lawSearch.do(target=law) / lawService.do(target=law), type=JSON.

※ 조례 '자동취득' 기능(검색→선별→본문→rag)은 ordinance_acquisition_legacy.py 로 분리됨.
   현재 파이프라인은 조례를 <도메인>/law/ 에서 직접 읽으므로 여기(A)만 사용.
"""

from __future__ import annotations

import json
import re

from app.config import LAW_GO_KR_OC, OPENAI_API_KEY

LAW_SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
LAW_SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"

# 세션 캐시 — 같은 법령을 시설마다 다시 조회하지 않도록(조례 없을 때 호출 폭증 방지).
#   프로세스 1회 실행 동안만 유효. 법령ID·본문 각각 캐시.
_LAW_ID_CACHE: dict = {}
_LAW_TEXT_CACHE: dict = {}


def extract_cited_laws(ordinance_rag: str) -> list[str]:
    """조례 본문에서 「」로 인용된 상위 법령명을 추출(중복 제거).
    예: '「국민건강증진법」', '「교육환경 보호에 관한 법률」'"""
    laws = re.findall(r"「([^」]+)」", ordinance_rag)
    # 시행령·시행규칙 등도 포함하되 조례 자기참조는 제외
    seen, out = set(), []
    for law in laws:
        law = law.strip()
        if not law or law in seen:
            continue
        if "조례" in law or "규칙" in law and "시행규칙" not in law:
            continue
        seen.add(law)
        out.append(law)
    return out


def search_law_id(law_name: str) -> tuple[str, str] | None:
    """법령명으로 (법령일련번호 MST, 시행일자 efYd) 조회. 없으면 None.
    efYd는 본문 조회에 필요(없으면 조문 전문이 안 옴).
    ※ 세션 캐시 — 같은 법령을 시설마다 재조회하지 않는다."""
    if law_name in _LAW_ID_CACHE:
        return _LAW_ID_CACHE[law_name]
    result = _search_law_id_uncached(law_name)
    _LAW_ID_CACHE[law_name] = result
    return result


def _search_law_id_uncached(law_name: str) -> tuple[str, str] | None:
    if not LAW_GO_KR_OC:
        mst = _mock_law_id(law_name)
        return (mst, "") if mst else None
    import requests

    params = {
        "OC": LAW_GO_KR_OC,
        "target": "law",
        "query": law_name,
        "type": "JSON",
        "display": 5,
    }
    try:
        data = requests.get(LAW_SEARCH_URL, params=params, timeout=15).json()
    except Exception as e:
        print(f"  [법령검색 오류] {e}")
        return None
    root = data.get("LawSearch", data)
    items = root.get("law") or []
    if isinstance(items, dict):
        items = [items]
    # 정확히 일치하는 법령명 우선(시행령·시행규칙 아닌 본법)
    pick = None
    for it in items:
        if it.get("법령명한글", "").strip() == law_name:
            pick = it
            break
    pick = pick or (items[0] if items else None)
    if not pick:
        return None
    mst = pick.get("법령일련번호") or pick.get("MST", "")
    ef = pick.get("시행일자", "")
    return (mst, ef)


def fetch_law_text(law_mst: str, ef_yd: str = "") -> str:
    """법령 본문에서 텍스트를 최대한 긁어모아 반환(구조 파싱 대신 통짜 텍스트).
    조문·항·호·개정문 등 어디에 있든 문자열을 재귀 수집 → LLM 이 반경을 찾음.
    (정밀 파싱 불필요: 어차피 LLM 추출 + HITL 확인)
    ※ 세션 캐시 — 같은 법령 본문을 시설마다 재다운로드하지 않는다."""
    key = (law_mst, ef_yd)
    if key in _LAW_TEXT_CACHE:
        return _LAW_TEXT_CACHE[key]
    text = _fetch_law_text_uncached(law_mst, ef_yd)
    _LAW_TEXT_CACHE[key] = text
    return text


def _fetch_law_text_uncached(law_mst: str, ef_yd: str = "") -> str:
    if not LAW_GO_KR_OC:
        return _mock_law_text(law_mst)
    import requests

    params = {"OC": LAW_GO_KR_OC, "target": "law", "MST": law_mst, "type": "JSON"}
    if ef_yd:
        params["efYd"] = ef_yd
    try:
        data = requests.get(LAW_SERVICE_URL, params=params, timeout=15).json()
    except Exception as e:
        print(f"  [법령본문 오류] {e}")
        return ""
    return _collect_strings(data)


def _collect_strings(obj, out=None) -> str:
    """중첩 JSON에서 모든 문자열을 재귀로 모아 합침(법령 텍스트 통짜 확보)."""
    if out is None:
        out = []
    if isinstance(obj, str):
        if len(obj) > 3:  # 짧은 코드값 제외
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_strings(v, out)
    return "\n".join(out)


def find_radius_in_laws(
    cited_laws: list[str],
    facility_type: str,
    facility: str = "",
    model: str | None = None,
) -> dict:
    """조례가 인용한 상위법(+시행령·시행규칙)의 텍스트를 모아 LLM 이 배제 반경(m)을 판단.
    정밀 조문 파싱 대신 법령 텍스트를 통짜로 주고 LLM 이 찾음(어차피 HITL 확인).
    facility: 대상 시설(흡연부스·재활용정거장 등). 프롬프트에 사용(도메인 무관).
    반환: {제안값, 출처, 근거문장, source_type:'law_api', confirmed:false}."""
    # 1) 인용 법령 + 시행령/시행규칙 텍스트 수집(관련 키워드 포함 부분만)
    chunks = []
    targets = []
    for law in cited_laws:
        targets += [law, f"{law} 시행령", f"{law} 시행규칙"]
    for law_name in targets:
        found = search_law_id(law_name)
        if not found:
            continue
        mst, ef = found
        text = fetch_law_text(mst, ef)
        if not text:
            continue
        # 관련 조문 선별(토큰 절약). ★ 시설명이 실제로 든 줄을 '우선' 담는다.
        #   과거 버그: "설치"·"미터" 같은 일반 키워드로만 걸러 15줄을 채우면,
        #   '흡연실은 출입구로부터 10미터…'(설치 위치 규정) 같은 무관 문장이 앞을 차지하고
        #   정작 '학교 경계선으로부터 30미터'(진짜 배제반경)가 잘려나갔다.
        lines = text.split("\n")
        ft = facility_type or ""
        # 시설명 매칭: 전체명 우선. 과도한 축약(앞 2글자)은 다른 시설을 오매칭하므로 쓰지 않는다.
        #   (예: '어린이보호구역'[:2]='어린' → '어린이집' 조문에 잘못 걸림)
        ft_keys = {ft} if len(ft) <= 4 else {ft, ft[:4]}
        DIST = ("미터", "m 이내", "경계", "거리", "이격", "이내", "반경")

        primary = [
            ln
            for ln in lines
            if any(k in ln for k in ft_keys) and any(d in ln for d in DIST)
        ]
        # 시설명은 있지만 거리 표현이 없는 줄(정의·범위 조항 — 문맥용)
        context = [
            ln for ln in lines if any(k in ln for k in ft_keys) and ln not in primary
        ]
        # 시설명이 없는 거리 조항(폴백 — 없을 때만 소량)
        fallback = [
            ln
            for ln in lines
            if any(d in ln for d in DIST) and not any(k in ln for k in ft_keys)
        ]

        rel = primary[:10] + context[:5]
        if not rel:  # 시설명 매칭이 전혀 없을 때만 폴백
            rel = fallback[:8]
        if rel:
            chunks.append({"법령": law_name, "관련조문": "\n".join(rel)})

    if not chunks:
        return {
            "제안값": None,
            "출처": None,
            "근거문장": "인용 상위법에서 관련 조문 미발견",
            "source_type": "law_api",
            "confirmed": False,
        }

    # 2) LLM 이 반경 판단(법령 원문 근거)
    if not OPENAI_API_KEY:
        return _mock_llm_radius(facility_type, chunks)
    from openai import OpenAI
    from app.config import SEARCH_LLM_MODEL

    client = OpenAI(api_key=OPENAI_API_KEY)
    m = model or SEARCH_LLM_MODEL
    _fac = facility or "대상 시설"
    prompt = (
        f"아래는 법령 원문에서 추린 조문들이다. '{_fac}' 설치 시 "
        f"**'{facility_type}' 로부터** 떨어져야 하는 이격거리(배제 반경, 미터)를 찾아라.\n\n"
        f"[중요] 반드시 '{facility_type}' 를(을) 기준으로 한 거리여야 한다. 다음은 답이 아니다:\n"
        f"  · '{_fac}'(대상 시설) 자체를 건물 어디에 두라는 설치 위치 규정\n"
        f"    (예: '출입구로부터 10미터 이상 떨어져 설치', '옥상에 설치')\n"
        f"  · 다른 시설을 기준으로 한 거리\n"
        f"근거문장에 '{facility_type}' 가 나오지 않으면 그 문장은 답이 아니다. "
        f"그런 경우 제안값 null 로 하라(억지로 다른 수치를 쓰지 마라).\n"
        f"법 본문이 '대통령령으로 정한다'고만 하면 시행령 조문에서 실제 수치를 찾아라. "
        f"현행 최신 수치를 쓰라.\n\n"
        f"[조문들] {json.dumps(chunks, ensure_ascii=False)[:4000]}\n\n"
        f'JSON 하나만: {{"제안값": <정수 미터 또는 null>, "출처": "<법령명 조항>", '
        f'"근거문장": "<\'{facility_type}\' 기준 거리를 규정한 문장 그대로>"}}'
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
        print(f"  [반경추출 오류] {e}")
        out = {"제안값": None, "출처": None, "근거문장": ""}
    out["source_type"] = "law_api"
    out["confirmed"] = False
    return out


def _mock_llm_radius(facility_type: str, chunks: list) -> dict:
    """키 없을 때 — 수집 텍스트에서 첫 '숫자+미터' 반환."""
    for c in chunks:
        m = re.search(r"(\d+)\s*(?:미터|m|ｍ)", c["관련조문"])
        if m:
            return {
                "제안값": int(m.group(1)),
                "출처": c["법령"],
                "근거문장": c["관련조문"][:100],
                "source_type": "law_api",
                "confirmed": False,
            }
    return {
        "제안값": None,
        "출처": None,
        "근거문장": "(mock) 미발견",
        "source_type": "law_api",
        "confirmed": False,
    }


def _mock_law_text(law_mst: str) -> str:
    if law_mst == "001234":  # 국민건강증진법(mock)
        return (
            "제9조(금연을 위한 조치) 어린이집·유치원 및 초중고 학교의 경계선으로부터 "
            "30미터 이내를 금연구역으로 지정한다(2024.8.17 시행)."
        )
    return ""


def _mock_law_id(law_name: str) -> str | None:
    return {"국민건강증진법": "001234", "교육환경 보호에 관한 법률": "005678"}.get(
        law_name
    )
