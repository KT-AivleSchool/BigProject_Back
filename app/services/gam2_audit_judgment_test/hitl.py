# -*- coding: utf-8 -*-
"""§HITL — 사람이 확정하는 대화형 루프(배제반경·데이터의도·지역코드).

여기 있는 `input()` 이 STEP1 의 사람 확정 지점 전부다. API 게이트(A)는 이 함수가
아니라 같은 **정본 적용 함수**(`apply_radius_answer`·`apply_intent_answer`)를 부른다 —
두 경로가 각자 답을 적용하면 CLI 와 API 가 갈린다.
"""
from __future__ import annotations

import json
import os

from .admin_code import _code_samples, resolve_code_prefix
from .exclusions import _read_radius, apply_radius_answer
from .intents import _read_int, _read_weight, apply_intent_answer
from .state import _out_path

def review_hitl(in_path: str | None = None, out_path: str | None = None) -> str:
    """HITL — 사람이 확인·확정하는 단계. 두 종류의 flag 를 처리한다.
      1) exclusion_radius_missing : 배제반경 확인/입력 (need_review=true 인 배제)
           · 제안값 있음(search 가 상위법에서 찾음) → 보여주고 승인/수정
           · 제안값 없음(조례 없어 검색 생략)      → 근거만 보여주고 직접 입력
      2) data_intent_unclear      : 애매한 데이터의 용도 확인(1~5)
      3) filter_by_code_prefix    : 지역 코드 접두 확인(AI 가 추측한 행정코드 — 검증 불가)
    입력: audit_result_enriched.json 이 있으면 우선(제안값 포함), 없으면 audit_result.json.
    출력: audit_result_reviewed.json (원본 보존)
    """
    import os

    if in_path is None:
        enriched = _out_path("audit_result_enriched.json")
        in_path = (
            enriched if os.path.exists(enriched) else _out_path("audit_result.json")
        )
    out_path = out_path or _out_path("audit_result_reviewed.json")
    print(f"[입력] {os.path.basename(in_path)}")
    doc = json.load(open(in_path, encoding="utf-8"))
    results = doc.get("results", [])

    # ── 1) 배제반경 확인 ────────────────────────────────────────────
    radius_jobs = [
        (r, f)
        for r in results
        for f in r.get("hitl_flags", [])
        if f.get("type") == "exclusion_radius_missing" and not f.get("confirmed")
    ]
    if not radius_jobs:
        print("[HITL] 배제반경 확인 대상 없음.")
    else:
        print(f"\n{'#' * 60}\n# 배제반경 확인 — {len(radius_jobs)}건")
        print("#  AI 제안값은 확정이 아닙니다. 출처를 보고 승인하거나 수정하세요.")
        print("#" * 60)
    for r, f in radius_jobs:
        idx = f.get("role_index", 0)
        roles = r.get("roles", [])
        role = roles[idx] if idx < len(roles) else {}
        ftype = role.get("facility_type", "?")
        etype = role.get("exclusion_type", "radius")

        print("\n" + "=" * 60)
        print(f"[{r.get('dataset_id', '')}] {ftype}  (배제 방식: {etype})")
        print(f"  데이터: {r.get('summary', '')}")
        print(f"  AI 판단근거: {role.get('rationale', '')}")

        제안 = f.get("제안값")
        if 제안 is not None:
            src = f.get("출처") or "?"
            근거 = f.get("근거문장") or ""
            print(f"\n  ▶ AI 제안: {제안}m   (출처: {src})")
            if 근거:
                print(f"    근거문장: {근거[:110]}")
            if f.get("근거_시설_일치") is False:
                print(
                    f"    ⚠ 근거-시설 불일치 — 근거문장에 '{ftype}'가 없습니다."
                    f" 다른 시설 규정일 수 있으니 반드시 확인하세요."
                )
        else:
            why = (
                "조례가 없어 검색을 생략했습니다"
                if not f.get("source_type")
                else "검색에서 근거를 찾지 못했습니다"
            )
            print(f"\n  ▶ AI 제안 없음 — {why}. 직접 입력이 필요합니다.")

        radius = _read_radius(default=제안)
        if radius == "skip":
            print("  → 건너뜀 (미확정 유지 — 이 상태로는 STEP2 가 시작되지 않는다)")
            continue
        apply_radius_answer(r, f, radius)
        if radius is None:
            print("  → 반경 없음(면 배제 등)으로 확정")
        else:
            print(f"  → {radius}m 확정 (이 실행에만 적용 — 다음 실행에서 다시 묻는다)")

    # ── 2) 데이터 용도 확인 ─────────────────────────────────────────
    pending = [
        r
        for r in results
        if any(f.get("type") == "data_intent_unclear" for f in r.get("hitl_flags", []))
    ]
    if not pending:
        print("\n[HITL] 의도 확인 대상 없음(data_intent_unclear 0).")
    else:
        print(f"\n{'#' * 60}\n# 데이터 용도 확인 — {len(pending)}건\n{'#' * 60}")
    for r in pending:
        print("\n" + "=" * 60)
        print(f"[{r.get('dataset_id', '')}] {r.get('summary', '')}")
        print("  이 데이터를 어떤 용도로 넣으셨나요?")
        print("   1) 가점(수요)  2) 감점(민감도)  3) 배제(금지)")
        print("   4) 위치선정 참조용(감리 입력 아님)  5) 잘못 넣음·제외")
        choice = _read_int("  선택(1~5): ", 1, 5)
        weight = _read_weight() if choice in (1, 2) else None
        apply_intent_answer(r, choice, weight)
        role = r["roles"][0]["role"] if r["roles"] else "excluded"
        tail = f", weight={r['roles'][0]['weight']}" if choice in (1, 2) else ""
        print(f"  → {role} 확정{tail}")
        if choice in (3, 4):
            print("    ※ 표시만 — 위치선정(GIS) 단계에서 참고/처리")

    # ── 3) 지역 코드 접두 확인 ──────────────────────────────────────
    #  filter_by_code_prefix 는 감리 AI 가 '행정 코드'라는 외부 지식을 알아야 하는
    #  유일한 op 다. 다른 경로(좌표·주소·자치구명)는 데이터 안에서 검증되지만 이건 아니다.
    #  실제 사고: 용산구(11170) 대신 마포구(11440) 를 써서 데이터 전체가 다른 구였는데,
    #  마포구도 행정동이 16개라 행수 검증(11904=16x24x31)을 통과해 조용히 넘어갔다.
    code_jobs = [
        (r, op)
        for r in results
        for op in (r.get("cleaning_ops") or [])
        if op.get("op_id") == "filter_by_code_prefix"
    ]
    if code_jobs:
        region = (doc.get("facility_inference", {}) or {}).get("region", "")
        print(f"\n{'#' * 60}\n# 지역 코드 확인 — {len(code_jobs)}건")
        print(f"#  AI 가 '{region}' 의 행정 코드를 추측한 값입니다. 반드시 확인하세요.")
        print("#  (틀려도 행수가 그럴듯하게 나와 자동 검증으로는 못 걸러냅니다)")
        print("#" * 60)
        for r, op in code_jobs:
            prm = op.setdefault("params", {})
            cur = prm.get("prefix", "")
            # 감리 단계에서 이미 판정했으면 그대로 쓴다 — CLI 와 프런트가 같은 답을 본다.
            #   단 verdict=="unknown" 은 **판정이 아니라 '판정 못 함'** 이다.
            #   코드표가 없던 실행에서 박힌 값을 그대로 쓰면, 코드표를 고쳐도
            #   HITL 이 계속 '(대조 불가)' 를 보여준다 → STEP1 재실행이 강요된다.
            chk = prm.get("prefix_check")
            if not chk or chk.get("verdict") == "unknown":
                chk = resolve_code_prefix(
                    cur, region, _code_samples(r.get("dataset_id"), prm.get("col"))
                )
                prm["prefix_check"] = chk
            hint = chk.get("suggestion")
            print(f"\n[{r.get('dataset_id')}] {r.get('summary', '')[:60]}")
            print(f"  컬럼 '{prm.get('col')}' 이 '{cur}' 로 시작하는 행만 남깁니다.")
            print(f"  코드표: {chk.get('detail') or '(대조 불가)'}")
            if chk.get("status") == "auto_confirmed":
                prm["prefix_confirmed"] = True
                prm["prefix_confirmed_by"] = "code_table" + (
                    f":{chk['system']}" if chk.get("system") else ""
                )
                print(f"  ✅ {chk.get('resolved') or region} — {chk.get('reason')}")
                print("     → 코드표로 확정 (사람 확인 생략)")
                continue
            print(f"  ⚠ 확인 필요 [{chk.get('verdict')}] — {chk.get('reason')}")
            if chk.get("resolved"):
                print(f"     이 접두는 실제로 '{chk['resolved']}' 입니다.")
            if hint:
                print(f"     참고: '{region}' 의 자치구 코드는 '{hint}' 입니다.")
            default = hint if (chk.get("verdict") == "mismatch" and hint) else cur
            print(f"  ▶ 이 코드가 '{region}' 이 맞습니까?")
            ans = input(
                f"  [Enter='{default}' 적용] · 다른 코드 입력 · s=건너뜀: "
            ).strip()
            if not ans:
                ans = default if default != cur else ""
            if ans.lower() == "s":
                print("  → 건너뜀 (미확인 상태로 진행 — 결과가 다른 지역일 수 있음)")
                prm["prefix_confirmed"] = False
                continue
            if ans:
                prm["prefix"] = ans
                print(f"  → '{ans}' 로 수정")
            else:
                print(f"  → '{cur}' 확정")
            prm["prefix_confirmed"] = True

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(doc, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 미확정 잔여 요약(건너뛴 것) — 위치선정 전에 반드시 처리해야 함
    left = [
        (
            r.get("dataset_id"),
            (r.get("roles", []) + [{}])[f.get("role_index", 0)].get(
                "facility_type", "?"
            ),
        )
        for r in results
        for f in r.get("hitl_flags", [])
        if f.get("type") == "exclusion_radius_missing" and not f.get("confirmed")
    ]
    unconf = [
        r.get("dataset_id")
        for r in results
        for op in (r.get("cleaning_ops") or [])
        if op.get("op_id") == "filter_by_code_prefix"
        and not (op.get("params") or {}).get("prefix_confirmed")
    ]
    if unconf:
        print(
            f"\n⚠ 미확인 지역코드 {len(unconf)}건: {', '.join(unconf)} "
            f"— 다른 지역 데이터일 수 있습니다."
        )
    if left:
        print(
            f"\n⚠ 미확정 배제반경 {len(left)}건 남음 (건너뜀): "
            f"{', '.join(f'{d}:{t}' for d, t in left)}"
        )
        print("  위치선정(GIS) 단계 전에 다시 hitl 을 실행해 확정하세요.")
    print(f"\n[저장] {out_path}")
    return out_path
