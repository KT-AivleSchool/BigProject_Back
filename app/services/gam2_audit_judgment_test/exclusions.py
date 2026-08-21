# -*- coding: utf-8 -*-
"""배제반경 HITL + 상위법 검색.

🔴 확정은 **그 run 안에서만** 유효하다. 예전엔 사람이 한 번 답한 값을 run 폴더 밖
   캐시에 적어두고 다음 실행에서 묻지 않고 채웠는데, 그러면 화면에 안 뜨는 항목이
   생기고 그게 사람이 확인한 것처럼 기록된다(원칙 4). 2026-08-10 제거.
"""
from __future__ import annotations

import json
import os
import re

from .admin_code import _enrich_code_prefix
from .fixtures import load_ordinance
from .state import _out_path

def apply_radius_answer(
    result: dict, flag: dict, radius_m: int | None, source: str = "human_confirmed"
) -> None:
    """HITL 답변을 roles·flag 에 반영(메모리). radius_m=None 이면 '반경 없음(면 배제 등)'.

    확정은 **이 run 안에서만** 유효하다. 다음 실행으로 넘기지 않는다(캐시 제거, 2026-08-10).
    """
    idx = flag.get("role_index", 0)
    roles = result.get("roles", [])
    if idx >= len(roles):
        return
    role = roles[idx]

    role["배제반경_m"] = radius_m
    role["confirmed"] = True  # 사람이 확인함 → 확정
    role["need_review"] = False
    role["source"] = source
    flag["제안값"] = radius_m
    flag["confirmed"] = True
    flag["confirmed_by_human"] = True


def reset_exclusion_confirmations(doc: dict) -> int:
    """이미 확정돼 있던 배제(hard_exclusion)를 **제안값으로 되돌린다**. 반환 = 되돌린 건수.

    `mode:"hitl"` 은 STEP1 을 안 돈다 — 고정된 감리 산출물(픽스처/정본)을 그대로 쓰는데
    거기엔 예전에 확정된 `confirmed:true` 가 이미 박혀 있다. 그대로 두면 게이트A 가
    그 항목을 `editable:false` 로 내보내 **사람이 볼 수는 있어도 고칠 수 없다.**
    "HITL 인데 사람이 전부 확인한다"가 성립하려면 이 값들이 제안값이어야 한다
    (2026-08-10 사람 지시).

    값은 지우지 않는다 — `배제반경_m`·`source` 는 그대로 두고 flag 의 `제안값` 으로도
    올린다. 사람이 Enter 로 그대로 승인하면 같은 값이 다시 확정된다.
    """
    n = 0
    for r in doc.get("results", []):
        flags = r.setdefault("hitl_flags", [])
        by_idx = {
            f.get("role_index", 0): f
            for f in flags
            if f.get("type") == "exclusion_radius_missing"
        }
        for i, role in enumerate(r.get("roles", [])):
            if role.get("role") != "hard_exclusion":
                continue
            was_confirmed = role.get("confirmed") is True
            f = by_idx.get(i)
            if f is None:
                f = {"type": "exclusion_radius_missing", "role_index": i}
                flags.append(f)
            if f.get("제안값") is None and role.get("배제반경_m") is not None:
                f["제안값"] = role["배제반경_m"]
                f["출처"] = role.get("source") or f.get("출처")
            f["message"] = (
                "이전 실행에서 확정된 값입니다(이번 실행에서는 제안값). 다시 확인하세요."
                if was_confirmed
                else "배제 대상이나 반경이 확정되지 않았습니다. 확인이 필요합니다."
            )
            f["이전_확정"] = was_confirmed
            # 확정 표시를 지운다 — 남겨두면 게이트A 가 editable:false 로 내보낸다.
            f.pop("confirmed", None)
            f.pop("confirmed_by_human", None)
            role["confirmed"] = False
            role["need_review"] = True
            n += 1
    return n


def assert_exclusions_confirmed(doc: dict, src: str = "") -> None:
    """배제(hard_exclusion) 중 미확정이 하나라도 있으면 **멈춘다**(SystemExit).

    2026-08-10 사람 결정. 예전엔 미확정인 채로 STEP2~4 를 완주하고 `report.json` 의
    gap(`배제판정_확인요청`)에만 남았다 — 산출물은 정직했지만 **아무도 안 봤고**
    배제가 빠진 Top-N 이 그대로 화면4·5 로 갔다. 배제는 후보를 지우는 조건이라
    빠지면 결과가 뒤집힌다. 「경고하고 진행」이 아니라 「멈춤」이 맞다(원칙 1).

    푸는 방법은 하나다 — 게이트A(API `POST /runs/{id}/hitl/audit`) 또는
    CLI `hitl` 에서 **사람이 반경을 확정**한다. 반경 없이 면으로 배제할 항목은
    `radius_m: null`(CLI 는 `n`)로 확정한다. 이것도 확정이다.
    """
    bad: list[str] = []
    for r in doc.get("results", []):
        did = r.get("dataset_id", "?")
        for i, role in enumerate(r.get("roles", [])):
            if role.get("role") != "hard_exclusion":
                continue
            if role.get("confirmed") is True:
                continue
            bad.append(
                f"  [{did}] roles[{i}] {role.get('facility_type') or role.get('rationale') or '?'}"
                f"  (배제반경_m={role.get('배제반경_m')} · exclusion_type="
                f"{role.get('exclusion_type')})"
            )
    if not bad:
        return
    raise SystemExit(
        f"🔴 배제 {len(bad)}건이 미확정이라 STEP2 로 넘어가지 않는다"
        + (f" — {src}" if src else "")
        + "\n"
        + "\n".join(bad)
        + "\n   게이트A(HITL)에서 배제반경을 확정할 것. 반경 없이 면으로 배제할"
        " 항목은 null(CLI 는 n)로 확정한다 — 그것도 확정이다."
    )


def _read_radius(default: int | None = None) -> int | None | str:
    """배제반경(m) 입력.
      숫자   → 그 값으로 확정
      Enter  → 제안값 있으면 승인, 없으면 건너뜀(미확정 유지)
      n      → 반경 없음(면 배제 등)으로 확정
      s      → 건너뜀(미확정 유지 — 나중에 다시)
    반환: int | None(반경없음 확정) | "skip"(미확정 유지)
    """
    hint = f"[Enter={default}m 승인]" if default is not None else "[Enter=건너뜀]"
    while True:
        s = input(f"  배제반경(m) {hint} · n=반경없음 · s=건너뜀: ").strip().lower()
        if s == "":
            return default if default is not None else "skip"
        if s == "n":
            return None
        if s == "s":
            return "skip"
        try:
            # '100m', '30 m', '30미터' 같은 단위 표기도 허용(프론트 입력칸도 관대하게)
            num = s.replace("미터", "").replace("m", "").replace("ｍ", "").strip()
            v = int(float(num))
            if v < 0:
                print("    0 이상으로 입력하세요.")
                continue
            return v
        except ValueError:
            print("    숫자 · n · s 중 하나를 입력하세요.")


def confirm_exclusion_radius(
    enriched_path: str, dataset_id: str, radius_m: int, out_path: str | None = None
) -> str:
    """HITL 담당자가 서핑 제안값을 확인·확정할 때 호출. confirmed=true 로 바꾼다."""
    doc = json.load(open(enriched_path, encoding="utf-8"))
    for r in doc["results"]:
        if not r["dataset_id"].startswith(dataset_id):
            continue
        for f in r.get("hitl_flags", []):
            if f.get("type") != "exclusion_radius_missing":
                continue
            idx = f.get("role_index", 0)
            f["제안값"] = radius_m
            f["confirmed_by_human"] = True
            # roles 쪽도 확정 반영
            if idx < len(r.get("roles", [])):
                r["roles"][idx]["배제반경_m"] = radius_m
                r["roles"][idx]["confirmed"] = True
                r["roles"][idx]["need_review"] = False
    path = out_path or enriched_path
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return path


def _norm(s: str) -> str:
    """조례 대조용 정규화(NFC). 시설유형·값이 조례 텍스트에 있는지 substring 비교에 사용."""
    import unicodedata

    return unicodedata.normalize("NFC", s or "")


def enrich_hitl_flags(
    pred: dict, region: str = "", fixtures: dict | None = None
) -> dict:
    """LLM 판정을 받은 뒤, 사람 검토가 필요한 항목을 코드가 결정론적으로 hitl_flags에 채운다.
    (LLM 판정 실수와 무관하게 항상 보장 — '판정=LLM, 확정=코드' 원칙)

    🔴 2026-08-10(사람 지시) — **배제(hard_exclusion)는 예외 없이 전부 사람이 본다.**
       예전엔 두 경로가 사람을 건너뛰었다: ⓐ 캐시 히트(제거됨) ⓑ 조례 텍스트에
       시설유형·값이 둘 다 있으면 `confirmed=True`. ⓑ 는 substring 대조라
       「제5조의 10m 가 이 시설 얘기인지」까지는 알 수 없다 — 근거는 되지만
       확정은 아니다. 자동으로 끝까지 가야 할 때는 `mode:"full"` 이 따로 있다.
       이제 조례 근거는 **제안값**으로 내려가고, 확정은 사람 답변으로만 붙는다.
    """
    flags = list(pred.get("hitl_flags", []))
    ord_norm = _norm(load_ordinance())  # 현재 도메인 조례 텍스트(제안 근거)
    existing = {(f.get("type"), f.get("role_index")) for f in flags}
    for i, r in enumerate(pred.get("roles", [])):
        if r.get("role") != "hard_exclusion":
            continue
        ftype = r.get("facility_type")
        radius = r.get("배제반경_m")
        is_radius = r.get("exclusion_type", "radius") == "radius"

        # 조례 대조: radius 값 존재 + 시설유형·값이 조례에 실제로 있으면 **제안값**.
        ftype_in_ord = bool(ftype) and _norm(ftype) in ord_norm
        value_in_ord = radius is not None and str(radius) in ord_norm
        ord_backed = bool(is_radius and radius is not None and ftype_in_ord and value_in_ord)

        # 배제는 전부 미확정으로 내려간다. LLM 자가확정도 여기서 벗긴다.
        # ※ source·rationale·배제반경_m 은 지우지 않는다 — 사람이 판단 근거로 봐야 하므로.
        r["confirmed"] = False
        r["need_review"] = True
        key = ("exclusion_radius_missing", i)
        if key not in existing:
            # 중복 필드(dataset_id·facility_type·exclusion_type) 없음 —
            # 이 flag 는 해당 데이터셋의 hitl_flags 안에 있고, role_index 로 roles[i] 를 가리킨다.
            flags.append(
                {
                    "type": "exclusion_radius_missing",
                    "role_index": i,
                    "message": (
                        "조례에서 시설유형·반경 문구를 찾았습니다(제안값). 같은 시설 규정이 맞는지 확인하세요."
                        if ord_backed
                        else "배제 대상이나 조례에서 반경/근거 확인 안 됨(LLM 자가판정). 검색·사람 확인 필요."
                    ),
                    "제안값": radius if ord_backed else None,
                    "출처": (r.get("source") or "ordinance") if ord_backed else None,
                    # 조례 제안일 때만 의미가 있다. substring 대조라 True 여도
                    # '그 시설의 규정'임을 보장하지 않는다 — 그래서 사람이 본다.
                    "근거_시설_일치": ftype_in_ord if ord_backed else None,
                }
            )

    # reference_only(참조/하류/무관 데이터) → 사람에게 '의도'를 묻는 HITL flag.
    #   LLM 은 reference_only 판정만, 질문 flag 생성은 코드가 결정론적으로.
    role_names = {r.get("role") for r in pred.get("roles", [])}
    if "reference_only" in role_names and not any(
        f.get("type") == "data_intent_unclear" for f in flags
    ):
        flags.append(
            {
                "type": "data_intent_unclear",
                "message": (
                    f"'{pred.get('summary', '')}' — 입지 판정의 입력 팩터로 보이지 않습니다"
                    "(참조·하류·무관 가능). 이 데이터를 어떤 용도로 넣으셨나요?"
                ),
                "질문": "이 데이터의 의도는?",
                "선택지": [
                    "가점(수요) 요인",
                    "감점(민감도) 요인",
                    "배제(금지) 요인",
                    "위치선정 참조용(감리 입력 아님)",
                    "잘못 넣음 · 제외",
                ],
                "제안": "참조용이면 감리에서 제외하고 위치선정 단계에서 사용",
                "confirmed": False,
            }
        )
    pred["hitl_flags"] = flags
    return _enrich_code_prefix(pred, region, fixtures)


def search_exclusion_radius(
    dataset_summary: str, region: str, facility: str = "", model: str | None = None
) -> dict:
    """[폴백] OpenAI Responses API + web_search 로 배제반경 후보 검색(법령 API 실패 시).
    반환: {"제안값": int|null, "출처": url|null, "근거문장": str, "source_type": "web_search"}
    ※ 확정 아님 — confirmed 는 호출부에서 계속 false 로 둔다(사람 확인 필수).
    """
    from openai import OpenAI
    from app.config import OPENAI_API_KEY, SEARCH_LLM_MODEL

    client = OpenAI(api_key=OPENAI_API_KEY)
    m = model or SEARCH_LLM_MODEL

    fac = facility or "대상 시설"
    prompt = (
        f"한국 {region}에서 '{fac}' 입지를 선정한다. '{dataset_summary}'에 해당하는 시설로부터 "
        f"'{fac}' 설치가 금지되는 법정 이격거리(배제 반경, 미터)를 찾아라. "
        f"근거는 반드시 법령·시행령·조례 등 공식 출처여야 한다. "
        f"블로그·뉴스의 인용값은 신뢰하지 말고, 원 법령을 확인하라. "
        f"★중요: 반드시 '현행(현재 시행 중인)' 최신 기준을 찾아라. 법은 개정되므로 "
        f"과거 폐지된 수치를 쓰지 말고, 개정 이력을 확인해 가장 최근 시행 값을 쓰고 "
        f"근거문장에 시행일을 포함하라.\n"
        f"찾으면 아래 JSON 형식 하나만 출력(설명 금지):\n"
        f'{{"제안값": <정수 미터 또는 null>, "출처": "<법령명·조항 또는 URL>", '
        f'"근거문장": "<해당 거리를 규정한 문장 요약 + 시행일>"}}'
    )
    resp = client.responses.create(
        model=m,
        tools=[{"type": "web_search"}],
        input=prompt,
    )
    text = resp.output_text.strip()
    text = re.sub(r"^```(json)?|```$", "", text).strip()
    try:
        found = json.loads(text)
    except json.JSONDecodeError:
        found = {"제안값": None, "출처": None, "근거문장": text[:200]}
    found["source_type"] = "web_search"
    return found


def enrich_with_search(
    in_path: str | None = None,
    out_path: str | None = None,
    region: str | None = None,
    ordinance_rag: str = "",
) -> str:
    """audit_result.json 의 exclusion_radius_missing flag 를, 조례가 인용한 상위법을
    법령 API 로 조회해 배제반경 후보로 채워 별도 저장. 원본 보존, confirmed=false(HITL 확인).
    ordinance_rag: 업로드된 조례 본문(「」 인용 법령 파싱용). 없으면 조례 텍스트 파일 사용.
    region: 안 주면 감리 결과의 `facility_inference.region` 을 쓴다. 🔴 예전 기본값은
      **"용산구"** 였다(2026-08-10 제거) — `search` 모드로 성동구를 돌리면 인자를 안 주므로
      용산구로 web_search 했다. 시설명(`facility`)은 이미 그 파일에서 읽고 있었다."""
    import copy
    import os
    from app.services.gam2_ordinance_acquisition import (
        extract_cited_laws,
        find_radius_in_laws,
    )
    from app.services.gam2_ordinance_select import has_siting_provision

    in_path = in_path or _out_path("audit_result.json")
    out_path = out_path or _out_path("audit_result_enriched.json")
    doc = json.load(open(in_path, encoding="utf-8"))
    enriched = copy.deepcopy(doc)

    # 조례 본문에서 인용된 상위법 목록 추출(한 번만)
    rag = ordinance_rag or load_ordinance()
    cited = extract_cited_laws(rag)
    print(f"  조례 인용 상위법: {cited}")

    # ── 검색 스킵 → HITL 직행 ─────────────────────────────────────────
    # 검색의 출발점은 '조례가 인용한 상위법'이다. 조례가 없으면 법령 API 진입로가 없고,
    # 남는 건 web_search 뿐인데 실측 결과 비용·시간만 쓰고 소득이 없었다(128s, 제안 대부분 null).
    #
    # 🔴 2026-08-03 — 게이트가 틀린 질문을 하고 있었다.
    #   기존: "조례가 있는가"(`not cited`)
    #   성동구 폐기물 조례는 **있고 상위법을 11개나 인용**하는데 이격 규정만 없다.
    #   → 게이트가 안 걸려 11개 × 배제 3건 검색으로 들어갔고 크레딧이 소진됐다.
    #   물어야 할 것은 "조례에 **이격 규정**이 있는가" 다. → has_siting_provision (LLM 0회)
    has_prov, prov_sig = has_siting_provision(rag)
    if not cited or not has_prov:
        if not cited:
            reason = "조례(또는 인용 상위법) 없음"
            stype = "ordinance_absent"
        else:
            reason = (
                f"조례에 이격거리·설치금지 규정 없음(전문 {len(rag):,}자 · 신호 0건)"
            )
            stype = "ordinance_no_provision"

        n_missing = 0
        for r in enriched["results"]:
            for f in r.get("hitl_flags", []):
                if f.get("type") != "exclusion_radius_missing":
                    continue
                n_missing += 1
                # 출처를 값마다 남긴다 — 안 한 것은 "안 했다"고 기록한다(절대원칙 4).
                f["source_type"] = stype
                f["근거문장"] = f"{reason} · 상위법 검색 생략"

        print(f"\n  ※ {reason} → 배제반경 검색을 건너뜁니다.")
        print(f"     미확정 배제반경 {n_missing}건은 HITL 에서 직접 확인·입력하세요:")
        print("       python audit_judgment_test.py hitl <도메인폴더>")
        # ⚠️ 상위법을 '검색했는데 없었다'가 아니라 '검색하지 않았다'. 구분해서 남긴다.
        enriched["_schema"]["상위법검색"] = (
            f"생략 — {reason}. 상위법은 **검색하지 않았다**(규정 없음을 확정한 것이 아니다). "
            f"배제반경은 HITL 에서 사람이 입력."
        )
        enriched["_schema"]["조례_입지규정_신호"] = prov_sig
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(enriched, f, ensure_ascii=False, indent=2)
        print(f"\n[검색 생략] 원본 그대로 저장 → {out_path}")
        return out_path
    # ─────────────────────────────────────────────────────────────

    # facility·region(폴백 web_search 프롬프트용) — 결과 JSON의 facility_inference에서.
    # 두 값의 출처를 같은 곳으로 맞춘다 — facility 만 파일에서 읽고 region 은 기본값을
    # 쓰면 "성동구 재활용정거장" 이 아니라 "용산구 재활용정거장" 을 검색하게 된다.
    _fi = doc.get("facility_inference", {}) or {}
    facility = _fi.get("facility", "")
    region = str(region or _fi.get("region") or "").strip()
    if not region:
        # 여기까지 왔다는 건 검색할 flag 가 실제로 있다는 뜻이다(위 스킵 경로 통과).
        raise SystemExit(
            f"🔴 {in_path} 에 facility_inference.region 이 없다. "
            "지역을 추측하지 않는다 — region= 으로 넘기거나 감리를 다시 돌릴 것."
        )

    n_filled = 0
    for r in enriched["results"]:
        for f in r.get("hitl_flags", []):
            if f.get("type") != "exclusion_radius_missing":
                continue
            # facility_type 은 flag 에 중복 저장 안 함 — role_index 로 roles[i] 에서 조회.
            _roles = r.get("roles", [])
            _idx = f.get("role_index", 0)
            ftype = (
                _roles[_idx].get("facility_type") if _idx < len(_roles) else None
            ) or r.get("summary", "")[:6]
            print(f"  [법령검색] {r['dataset_id']}: '{ftype}' 배제반경 상위법 조회")

            # 1차: 조례 인용 상위법을 법령 API로 조회
            try:
                found = find_radius_in_laws(cited, ftype, facility=facility)
            except Exception as e:
                print(f"           [법령 API 오류] {e} → web_search 폴백")
                found = {"제안값": None, "source_type": "law_api_failed"}
            # 폴백: 법령 API가 통신오류/미발견이면 web_search(감리 결과 참고)
            if found.get("제안값") is None:
                print("           법령 API 미발견 → web_search 폴백")
                try:
                    found = search_exclusion_radius(
                        r.get("summary", ftype), region, facility
                    )
                except Exception as e:
                    print(f"           [web_search 오류] {e}")
                    found = {
                        "제안값": None,
                        "출처": None,
                        "근거문장": "검색 실패",
                        "source_type": "search_failed",
                    }
            f["제안값"] = found.get("제안값")
            f["출처"] = found.get("출처")
            f["source_type"] = found.get(
                "source_type"
            )  # law_api / web_search / *_failed
            f["근거문장"] = found.get("근거문장", "")
            # 근거-시설 일치 점검: 근거문장에 facility_type 이 실제로 있는지(오추출 방지).
            #   confirmed 재판정과 같은 substring(NFC) 방식. 자동 반려 아님 — 표시만.
            근거norm = _norm(f["근거문장"])
            f["근거_시설_일치"] = bool(ftype) and _norm(ftype) in 근거norm
            f["confirmed"] = False  # 어느 경로든 HITL 최종 확인 필수
            n_filled += 1
            mark = "" if f["근거_시설_일치"] else "  ⚠근거-시설 불일치"
            if f.get("제안값") is not None and not f["근거_시설_일치"]:
                f["message"] = (
                    f"⚠근거-시설 불일치: 근거문장에 '{ftype}'이(가) 없음 — "
                    f"다른 시설 규정을 긁었을 수 있음. 사람이 반드시 확인."
                )
            print(
                f"           → 제안 {found.get('제안값')}m (source: {found.get('source_type')}){mark}"
            )
    enriched["_schema"]["상위법검색"] = (
        "exclusion_radius_missing flag 를 조례가 인용한 상위법(법령 API)"
        "에서 반경을 찾아 제안값에 채움. confirmed=false, HITL 확인 필수. "
        "근거_시설_일치=false 면 근거문장에 해당 시설이 없어 오추출 의심(사람 확인)."
    )
    json.dump(
        enriched, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2
    )
    print(f"\n[상위법 검색 완료] {n_filled}건 채움 → {out_path}")
    return out_path
