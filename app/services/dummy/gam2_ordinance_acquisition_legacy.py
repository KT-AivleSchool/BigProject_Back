# -*- coding: utf-8 -*-
"""
OmniSite 조례 자동 취득 (STEP 0.7) — [LEGACY / 현재 파이프라인 미사용]
====================================================================
facility + region 으로 관련 조례를 법제처 API 에서 자동 취득한다.
현재는 조례를 사용자가 <도메인>/law/ 에 직접 업로드하는 방식으로 대체되어,
이 모듈은 파이프라인에서 호출되지 않는다(단독 실행 전용). 재활성화 대비 보존.

흐름 (acquire_ordinances 진입):
  1) search_ordinances    : 목록 API 로 "지역+시설" 검색 (후보 다수)
  2) select_ordinances    : LLM(mini)이 노이즈 제거 + 관련 조례 선별
  3) fetch_ordinance_text : 본문 API 로 선별 조례 조문 취득
  4) build_ordinance_rag  : 확인된 조례를 감리용 ordinance_rag 텍스트로
  ※ 취득 결과는 confirmed=false → HITL 확인 대상

단독 실행:  python ordinance_acquisition_legacy.py <시설> <지역>
키: .env 의 LAW_GO_KR_OC. 키 없으면 _mock_* 폴백.
자치법규 API: lawSearch.do(target=ordin) / lawService.do(target=ordin), type=JSON.

※ 상위법 배제반경 연계(extract_cited_laws·find_radius_in_laws)는
   ordinance_acquisition.py 로 분리됨(그쪽이 search 모드에서 실제 사용됨).
"""

from __future__ import annotations

import json
import os

from app.config import (
    LAW_GO_KR_OC,
    FACILITY_LLM_MODEL,
    SEARCH_CACHE_DIR,
    OPENAI_API_KEY,
)

ORDINANCE_CACHE_DIR = os.path.join(SEARCH_CACHE_DIR, "ordinances")

LAW_SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
LAW_SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"


def search_ordinances(region: str, facility: str) -> list[dict]:
    """지역+시설 키워드로 자치법규 목록 검색(법제처 lawSearch.do, target=ordin).
    키 없으면 mock 후보(용산구 흡연 예시 + 노이즈)를 반환해 구조 검증."""
    if not LAW_GO_KR_OC:
        return _mock_search(region, facility)

    import requests

    query = f"{region} {_facility_to_keyword(facility)}"
    params = {
        "OC": LAW_GO_KR_OC,
        "target": "ordin",
        "query": query,
        "type": "JSON",
        "display": 20,
    }
    try:
        r = requests.get(LAW_SEARCH_URL, params=params, timeout=15)
        data = r.json()
    except Exception as e:
        print(f"  [검색 오류] {e} → mock 사용")
        return _mock_search(region, facility)
    # 법제처 자치법규 응답: {"OrdinSearch": {"law": [ {...}, ... ]}}
    root = data.get("OrdinSearch", data)
    items = root.get("law") or []
    if isinstance(items, dict):
        items = [items]
    out = []
    for it in items:
        out.append(
            {
                "제목": it.get("자치법규명", ""),
                "id": it.get("자치법규일련번호", ""),  # 본문조회 시 MST 로 사용
                "지자체": it.get("지자체기관명", ""),
                "시행일": it.get("시행일자", ""),
            }
        )
    return out or _mock_search(region, facility)


def _facility_to_keyword(facility: str) -> str:
    """시설명 → 조례 검색 키워드(도메인 무관하게 LLM 확장도 가능하나, 기본 매핑)."""
    m = {
        "흡연부스": "흡연 금연",
        "EV 충전소": "전기자동차 충전",
        "음식물 쓰레기 수거함": "폐기물 음식물",
    }
    return m.get(facility, facility)


def _mock_search(region: str, facility: str) -> list[dict]:
    """키 없을 때 구조 검증용 — 실제 용산구 흡연 검색과 유사한 후보(노이즈 포함)."""
    return [
        {
            "제목": f"서울특별시 {region} 금연구역 지정 및 간접흡연피해방지 조례",
            "id": "1052687",
            "지자체": region,
            "시행일": "20251226",
        },
        {
            "제목": "서울특별시 금연환경 조성 및 간접흡연 피해방지 조례",
            "id": "2000001",
            "지자체": "서울특별시",
            "시행일": "20250101",
        },
        {
            "제목": f"서울특별시 {region} 폐기물 관리 조례",  # 노이즈
            "id": "1658985",
            "지자체": region,
            "시행일": "20251226",
        },
        {
            "제목": f"서울특별시 {region} 보건소 수가 조례",  # 노이즈
            "id": "1285808",
            "지자체": region,
            "시행일": "20240101",
        },
    ]


def select_ordinances(
    candidates: list[dict], facility: str, region: str, model: str | None = None
) -> dict:
    """후보 조례 제목들에서 facility 입지에 관련된 것만 선별.
    반환: {"선택": [id...], "제외": [id...], "근거": str, "confirmed": false}
    구·시·상위법 다층을 인식해 관련된 것을 모두 고른다."""
    if not OPENAI_API_KEY:
        return _mock_select(candidates, facility)

    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    m = model or FACILITY_LLM_MODEL
    titles = [
        {"id": c["id"], "제목": c["제목"], "지자체": c["지자체"]} for c in candidates
    ]
    prompt = (
        f"'{region}'에서 '{facility}' 입지를 선정한다. 아래 조례 후보 중 이 시설의 "
        f"설치·배제·입지 규정과 관련된 것만 고르라. 관련 없는 조례(예: 폐기물·보건소 수가 등)는 제외.\n"
        f"구 조례·시 조례가 모두 관련되면 둘 다 선택하라(다층).\n\n"
        f"[후보] {json.dumps(titles, ensure_ascii=False)}\n\n"
        f'JSON 하나만: {{"선택":[id...], "제외":[id...], "근거":"..."}}'
    )
    resp = client.chat.completions.create(
        model=m,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        out = json.loads(resp.choices[0].message.content)
    except json.JSONDecodeError:
        out = _mock_select(candidates, facility)
    out["confirmed"] = False  # HITL 확인 전
    return out


def _mock_select(candidates: list[dict], facility: str) -> dict:
    """키 없을 때 — 제목에 흡연/금연 있으면 선택, 폐기물·보건소는 제외."""
    pick, drop = [], []
    for c in candidates:
        t = c["제목"]
        if any(k in t for k in ("금연", "흡연")):
            pick.append(c["id"])
        else:
            drop.append(c["id"])
    return {
        "선택": pick,
        "제외": drop,
        "근거": "제목에 금연·흡연 포함된 조례 선택, 폐기물·보건소 제외(mock)",
        "confirmed": False,
    }


def fetch_ordinance_text(ordinance_id: str) -> dict:
    """선별된 조례의 조문 전문 취득(법제처 lawService.do, target=ordin).
    반환: {"id", "제목", "조문": [{"조번호","제목","내용"}...], "상위법": [...]}"""
    if not LAW_GO_KR_OC:
        return _mock_text(ordinance_id)

    import requests

    params = {
        "OC": LAW_GO_KR_OC,
        "target": "ordin",
        "MST": ordinance_id,
        "type": "JSON",
    }
    try:
        r = requests.get(LAW_SERVICE_URL, params=params, timeout=15)
        data = r.json()
    except Exception as e:
        print(f"  [본문 오류] {e} → mock")
        return _mock_text(ordinance_id)
    # 법제처 자치법규 본문 응답: {"LawService": {"조문": {"조": [ {...} ]}}}
    root = data.get("LawService", data) if isinstance(data, dict) else {}
    basic = root.get("자치법규기본정보", {}) or {}
    title = basic.get("자치법규명", "")
    articles = []
    jo = (root.get("조문", {}) or {}).get("조", [])
    if isinstance(jo, dict):
        jo = [jo]
    for j in jo:
        # 조문번호는 문자열/배열 모두 가능
        num = j.get("조문번호", "")
        if isinstance(num, list):
            num = num[0] if num else ""
        # 조문 내용: 실제 키는 '조내용' (조문번호·조제목·조내용·조문여부 구조)
        content = j.get("조내용", "") or j.get("조문내용", "")
        # 항이 별도 배열로 오는 경우도 대비해 이어붙임
        hang = j.get("항", [])
        if isinstance(hang, dict):
            hang = [hang]
        for h in hang:
            hc = h.get("항내용", "")
            if hc:
                content += "\n" + hc
        articles.append(
            {
                "조번호": _fmt_jo_num(num),
                "제목": j.get("조제목", "") or j.get("조문제목", ""),
                "내용": content.strip(),
            }
        )
    return {"id": ordinance_id, "제목": title, "조문": articles, "상위법": []}


def _fmt_jo_num(num: str) -> str:
    """법제처 조문번호(예: '000100')를 '제1조' 형태로."""
    s = str(num)
    if len(s) >= 6 and s.isdigit():
        jo = int(s[:4])
        ji = int(s[4:])
        return f"제{jo}조" + (f"의{ji}" if ji else "")
    return s


def _mock_text(ordinance_id: str) -> dict:
    """키 없을 때 — 용산구 조례 핵심 조문(제5조·제8조) 예시."""
    return {
        "id": ordinance_id,
        "제목": "서울특별시 용산구 금연구역 지정 및 간접흡연피해방지 조례",
        "조문": [
            {
                "조번호": "제5조",
                "제목": "금연구역의 지정",
                "내용": "버스정류소 및 택시 승차대로부터 10미터 이내, 지하철역 출입구로부터 "
                "10미터 이내, 교육환경보호구역 중 절대보호구역 등을 금연구역으로 지정한다.",
            },
            {
                "조번호": "제8조",
                "제목": "흡연구역의 설치",
                "내용": "제5조제1항제2호부터 제4호까지의 장소에는 흡연구역을 설치·운영할 수 없다.",
            },
        ],
        "상위법": [
            {
                "법령명": "국민건강증진법",
                "조항": "제9조",
                "내용": "학교·어린이집 등 시설 경계 30미터 이내 금연구역(2024.8.17 시행).",
            },
        ],
    }


def build_ordinance_rag(texts: list[dict]) -> str:
    """취득한 조례들(구·시·상위법)을 감리용 ordinance_rag 텍스트로 결합.
    조내용에 이미 '제N조(제목)'가 포함되어 있으므로 내용을 그대로 쓴다."""
    parts = []
    for t in texts:
        parts.append(f"[{t.get('제목', '')}]")
        for a in t.get("조문", []):
            content = a.get("내용", "").strip()
            if content:
                parts.append(content)  # 조내용에 조번호·제목 이미 포함
            else:
                parts.append(f"{a.get('조번호', '')}({a.get('제목', '')})")
        for up in t.get("상위법", []):
            parts.append(
                f"[상위법: {up.get('법령명', '')} {up.get('조항', '')}] {up.get('내용', '')}"
            )
        parts.append("")
    return "\n".join(parts)


def acquire_ordinances(facility: str, region: str) -> dict:
    """전체 파이프라인: 검색→선별→본문취득→rag조립. HITL 확인 대상(confirmed=false).
    반환: {facility, region, 선택조례[], ordinance_rag, confirmed:false}"""
    print(f"[조례 취득] '{region}' + '{facility}'")
    candidates = search_ordinances(region, facility)
    print(f"  검색 후보 {len(candidates)}건: {[c['제목'][:25] for c in candidates]}")
    sel = select_ordinances(candidates, facility, region)
    print(f"  선별: {len(sel['선택'])}건 선택 / {len(sel.get('제외', []))}건 제외")
    picked = [c for c in candidates if c["id"] in sel["선택"]]
    texts = [fetch_ordinance_text(c["id"]) for c in picked]
    rag = build_ordinance_rag(texts)
    result = {
        "facility": facility,
        "region": region,
        "선택조례": [{"id": c["id"], "제목": c["제목"]} for c in picked],
        "제외조례": sel.get("제외", []),
        "선별근거": sel.get("근거", ""),
        "ordinance_rag": rag,
        "confirmed": False,  # HITL 확인 후 true
        "_설명": "자동 취득한 조례. HITL에서 '이 조례들이 맞는지' 확인 후 confirmed=true.",
    }
    # 캐시 저장(지역+시설 키)
    os.makedirs(ORDINANCE_CACHE_DIR, exist_ok=True)
    key = f"{region}_{facility}".replace(" ", "")
    json.dump(
        result,
        open(os.path.join(ORDINANCE_CACHE_DIR, f"{key}.json"), "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=2,
    )
    return result


if __name__ == "__main__":
    import sys

    facility = sys.argv[1] if len(sys.argv) > 1 else "흡연부스"
    region = sys.argv[2] if len(sys.argv) > 2 else "용산구"
    res = acquire_ordinances(facility, region)
    print("\n=== 취득 결과 ===")
    print("선택 조례:", [c["제목"] for c in res["선택조례"]])
    print("선별 근거:", res["선별근거"])
    print("\n--- ordinance_rag (감리 주입용) ---")
    print(res["ordinance_rag"])
    print(f"\n※ confirmed={res['confirmed']} — HITL 확인 필요")
