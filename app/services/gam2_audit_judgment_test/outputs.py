# -*- coding: utf-8 -*-
"""산출물 저장 · 리포트 — 적용 안 한 것은 「안 했다」고 적는다(원칙 4)."""
from __future__ import annotations

import json
import os

from app.config import STEP1_OUTPUT_DIR

from .harness import Judgment
from .state import _out_path

def facility_inference_doc(facility_info: dict) -> dict:
    """`resolve_facility()` 결과 → 산출물에 실을 모양.

    두 곳이 같은 모양을 써야 한다 — `audit_result.json` 의 `facility_inference` 키와
    STEP 0.5 가 따로 내보내는 `facility_inference.json`. 모양이 갈리면 프런트가
    「감리 전」과 「감리 후」에 다른 값을 그린다.
    """
    return {
        "facility": facility_info.get("facility"),
        "region": facility_info.get("region"),
        "근거": facility_info.get("근거"),
        "mismatch": facility_info.get("mismatch", False),
        "mismatch_reason": facility_info.get("mismatch_reason", ""),
        "source_input": facility_info.get("source_input", ""),
        "confirmed": False,  # HITL 확인 대상
        "_설명": "사용자 입력+데이터명으로 확정한 선정 시설. HITL에서 확인/수정 후 confirmed=true.",
    }


def save_facility_inference(facility_info: dict) -> str:
    """STEP 0.5 직후 시설·지역만 먼저 내보낸다.

    같은 값이 `audit_result.json` 에도 들어가지만 그건 감리(수백 초) **뒤**다.
    화면2 의 「선정 대상」은 감리를 기다릴 이유가 없어서 여기서 한 번 더 쓴다.
    ⚠ 사본이 아니라 **먼저 나오는 같은 값**이다 — 모양은 위 빌더 하나가 정한다.
    """
    path = _out_path("facility_inference.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(facility_inference_doc(facility_info), f, ensure_ascii=False, indent=2)
    return path


def save_results(
    judgments: list[Judgment],
    raw_preds: dict,
    model: str,
    out_dir: str | None = None,
    facility_info: dict | None = None,
) -> str:
    """감리 판정을 하나의 JSON으로 저장. 최상단 _schema에 필드 설명 포함(자기설명적).
    다음 단계(지오코딩·정제)가 이 파일만 보고 각 필드 의미를 알 수 있다."""
    import os
    from datetime import datetime

    out_dir = out_dir or STEP1_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    doc = {
        "_schema": {
            "설명": "OmniSite 감리 AI 1단계 산출물. 각 데이터셋의 역할·좌표상태·정제지시.",
            "생성모델": model,
            "생성시각": datetime.now().isoformat(timespec="seconds"),
            "필드설명": {
                "dataset_id": "데이터셋 식별자 (01, 02 … — data/ 파일명 가나다순 자동 부여)",
                "summary": "이 데이터가 대상 시설 입지에서 어떤 역할인지 한 줄 요약 (사람 HITL 확인용)",
                "roles": "입지 판단에서의 의미 role 리스트(공존 가능). 아래 role_types 참조",
                "coord_status": "좌표 상태. 다음 단계(지오코딩)가 이 값으로 처리 분기. 아래 coord_types 참조",
                "cleaning_ops": "정제에 필요한 연산 리스트(op_id + params). 정제 단계가 실행할 지시서",
                "hitl_flags": "사람 검토가 필요한 항목. role_index 로 이 데이터셋의 roles[i] 를 가리킴",
            },
            "role_types": {
                "positive_factor": "설치 수요를 높이는 가점 요인. weight(+, 0~1) 동반",
                "negative_factor": "갈등·민감도를 높이는 감점 요인. weight(-, -1~0) 동반",
                "hard_exclusion": "조례·법령상 설치 금지. weight 대신 배제반경_m·source·confirmed 동반",
                "reference_only": "입지 판정의 입력 팩터가 아님(참조·하류·무관). "
                "data_intent_unclear 플래그로 사람에게 용도를 되묻는다",
            },
            "role_필드": {
                "weight": "가중치 대략값(-1~1). HITL로 사람이 최종 조정",
                "exclusion_type": "배제 방식. radius=점+버퍼(반경 배제), polygon=구역 경계로 배제(면)",
                "배제반경_m": "radius일 때 배제 버퍼 반경(m). polygon이거나 미확정이면 null",
                "source": "LLM 이 제시한 배제 근거(조항 등). ※ confirmed=false 면 조례 대조에서 "
                "검증되지 않은 값 — HITL 에서 사람이 판단 근거로 참고만 할 것",
                "confirmed": "조례 본문 대조로 코드가 검증한 경우만 true. "
                "LLM 자가판정은 신뢰하지 않음(false → 검색·HITL)",
                "need_review": "true면 사람 확인 필요(조례 미명시·미확정)",
                "rationale": "판정 근거",
            },
            "coord_types": {
                "has_coords": "좌표 컬럼 이미 있음 → 그대로 사용",
                "needs_geocoding": "좌표 없고 주소만 있음 → 다음 단계에서 지오코딩 필요",
                "stat_join": "좌표 없는 통계 → 마스터/경계와 조인·공간조인으로 위치 부여",
                "spatial": "폴리곤(경계·지적도) 자체가 공간정보",
            },
            "주의": "roles·coord_status는 감리 AI 제안값이며 HITL 검토 후 확정됩니다.",
        },
        "results": [raw_preds[j.dataset_id] for j in judgments],
    }
    if facility_info is not None:
        doc["facility_inference"] = facility_inference_doc(facility_info)
    path = _out_path("audit_result.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    return path


def report(judgments: list[Judgment], raw_preds: dict | None = None) -> None:
    """감리 판정 리포트. 확정은 HITL(사람)에서 — 여기 출력은 검토 보조."""
    n = len(judgments)
    exclusion_review = []
    for j in judgments:
        role_str = (
            "+".join(
                r.get("role", "?")
                .replace("_factor", "")
                .replace("hard_exclusion", "배제")
                for r in j.roles
            )
            or "-"
        )
        print(f"[{j.dataset_id}] {role_str:24} 좌표:{j.coord_status:15}")
        print(f"     요약: {j.summary}")
        if j.exclusions:
            exclusion_review.append(j)
    print("-" * 92)
    n_excl = sum(
        1 for j in judgments if any(r.get("role") == "hard_exclusion" for r in j.roles)
    )
    n_pos = sum(
        1 for j in judgments if any(r.get("role") == "positive_factor" for r in j.roles)
    )
    n_ref = sum(
        1 for j in judgments if any(r.get("role") == "reference_only" for r in j.roles)
    )
    print(f"판정 완료: {n}개 데이터셋  (배제 {n_excl} · 가점 {n_pos} · 참조 {n_ref})")

    if exclusion_review:
        print(
            "\n[배제반경 검토 — 조례에 반경 미명시. 사람이 확인/입력 (search 로 후보 주입 가능)]"
        )
        for j in exclusion_review:
            for r in j.exclusions:
                print(
                    f"  {j.dataset_id}: 배제 대상이나 반경 미확정 → {r.get('rationale', '')[:60]}"
                )

    # 다음 단계(지오코딩)로 넘길 대상 요약
    geo = [j.dataset_id for j in judgments if j.coord_status == "needs_geocoding"]
    print(f"\n[다음 단계(지오코딩) 대상] 좌표 없어 지오코딩 필요: {geo or '없음'}")

    # HITL 대기 flag 요약(코드가 자동 생성한 것)
    if raw_preds:
        flag_items = [
            (did, f) for did, p in raw_preds.items() for f in p.get("hitl_flags", [])
        ]
        if flag_items:
            print(f"\n[HITL 대기 — 사람 입력 필요] {len(flag_items)}건")
            for did, f in flag_items:
                print(f"  {did}: {f.get('type')} — {f.get('message', '')}")
