# -*- coding: utf-8 -*-
"""HITL 게이트 — **질문을 만드는 쪽**.

🔴 질문 목록은 `hitl_flags` 같은 **부산물**이 아니라 **본체**(`hard_exclusion` role)
   에서 만든다. 부산물에서 만들면 그 부산물을 안 만드는 경로가 곧 구멍이 된다
   (2026-08-10 — 배제 5건 중 2건이 게이트A 에 아예 안 떴다).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.config import domain_prefix

from .state import _StepFailed, _now_iso, run_dir


# ══════════════════════════════════════════════════════════════════
# 8b. HITL 게이트 (계약 7절)
# ══════════════════════════════════════════════════════════════════
# 질문을 만드는 쪽과 답을 적용하는 쪽이 **같은 파일을 본다.**
#   게이트A → `runs/<id>/step1/<pre>_audit_result_reviewed.json`
#   게이트B → `runs/<id>/step3/<domain>_weight_proposal_<run_id>.json`
# 질문을 따로 계산해 두었다가 적용할 때 다시 계산하면 그 사이에 갈릴 수 있다.
#
# 🔴 답을 적용하는 함수는 **정본을 그대로 부른다**(`apply_radius_answer`·
#    `apply_intent_answer`). 새로 짜면 CLI 와 API 가 갈리고, 그게 이 프로젝트가
#    반복해서 당한 유형이다(CLAUDE.md '모듈 사본').


def _hitl_dir(run_id: str) -> Path:
    return run_dir(run_id) / "hitl"


def _answer_path(run_id: str, gate_id: str) -> Path:
    return _hitl_dir(run_id) / f"{gate_id}_answer.json"


def _save_answer(run_id: str, gate_id: str, payload: dict,
                 by: str = "human") -> None:
    """누가 무엇을 답했는지 원본 그대로 남긴다.

    규약 '값마다 누가 정했는지 남긴다' 의 게이트판이다. reviewed.json 에는
    적용 **결과**만 남고 '무엇을 건너뛰었는지'는 안 남는다 — 그건 여기 있다.

    🔴 `by` 를 같이 적는다. 「고속 자동 분석」이 넣은 답은 모양이 사람 답과 똑같아서
       (같은 검증기를 타므로 당연히 그렇다) 이 필드가 없으면 파일만 보고는 구분할
       방법이 없다 — 나중에 「사람이 이렇게 답했다」로 읽힌다(원칙 4).
    """
    _hitl_dir(run_id).mkdir(parents=True, exist_ok=True)
    doc = {"gate": gate_id, "answered_at": _now_iso(),
           "answered_by": by, "answer": payload}
    _answer_path(run_id, gate_id).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_answer(run_id: str, gate_id: str) -> dict | None:
    p = _answer_path(run_id, gate_id)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))["answer"]


def _reviewed_path(run_id: str, domain: str) -> Path:
    return run_dir(run_id) / "step1" / f"{domain_prefix(domain)}_audit_result_reviewed.json"


def _seed_reviewed(run_id: str, domain: str) -> str:
    """full 모드에서 게이트A 가 읽을 `reviewed.json` 을 만든다. 반환: 출처 파일명.

    STEP1 은 `audit_result.json`(감리 판정)과 — 미확정 배제반경이 있으면 —
    `audit_result_enriched.json`(상위법 검색 제안값 포함)까지 쓴다. `reviewed` 는
    **사람이 확정한 판**이라 STEP1 이 만들지 않는다. CLI 에서는 `review_hitl` 이
    대화형으로 만들지만, API 는 그 자리를 게이트A 가 대신한다.

    🔴 폴백 순서(enriched > audit_result)는 `review_hitl`(:1619 주석)과 **같다.**
       여기서 다른 순서를 쓰면 CLI 로 돌린 결과와 API 로 돌린 결과가 갈린다.
    🔴 게이트A 답변은 이 파일을 **제자리에서 고친다**(`_apply_audit`). 즉 이 시드는
       "빈 껍데기"가 아니라 **LLM 제안값 그대로**이고, 사람이 손대지 않은 항목은
       제안값이 그대로 남는다 — `confirmed` 플래그가 그 사실을 구분해 준다.
    """
    d = run_dir(run_id) / "step1"
    pre = domain_prefix(domain)
    for name in (f"{pre}_audit_result_enriched.json", f"{pre}_audit_result.json"):
        src = d / name
        if src.is_file():
            shutil.copyfile(src, _reviewed_path(run_id, domain))
            return name
    raise _StepFailed(
        f"STEP1 감리 산출물이 없습니다: {d}/{pre}_audit_result[_enriched].json — "
        "STEP0·1 이 파일을 남기지 않고 끝났습니다.")


def _proposal_path(run_id: str, domain: str) -> Path:
    """`save_weight_proposal` 이 쓴 곳. 자식은 STEP3_OUTPUT_DIR 이 run 폴더로 잡혀 있다."""
    return run_dir(run_id) / "step3" / f"{domain}_weight_proposal_{run_id}.json"


def build_gate(gate_id: str, run_id: str, domain: str) -> dict:
    if gate_id == "audit":
        return {"id": "audit", "label": "감리 확인 — 배제반경 · 데이터 용도 · 지역 코드",
                "questions": _questions_audit(run_id, domain)}
    if gate_id == "weight":
        return {"id": "weight", "label": "집계반경 · 가중치 확정",
                "questions": _questions_weight(run_id, domain)}
    raise ValueError(f"알 수 없는 게이트: {gate_id!r}")


# ── 게이트A 질문 ───────────────────────────────────────────────────
#  확정분도 **보여준다.** 감추면 사람은 "무엇이 이미 정해졌는지" 를 모른 채 남은 것만
#  답하게 된다 — 화면이 사실의 일부만 보여주는 것이다(원칙 4).
#
#  🔴 **확정분도 이제 고칠 수 있다**(`editable` 은 항상 true). 2026-08-05 에는
#     `false` 였다 — 「HITL 전 confirmed 는 조례에서 근거를 확실히 찾았을 때뿐이라
#     고칠 이유가 없다」가 근거였다. 그 근거가 무너졌다: 조례 대조는 2026-08-10 에
#     **확정에서 제안으로 강등**됐고(함정표 「자동 확정이 flag 를 안 남겨…」),
#     자동 확정 경로 둘을 없앤 지금 남은 confirmed 는 **앞선 게이트 답변** 뿐이다.
#     자기가 방금 넣은 값을 못 고치면 화면은 「다시 시작」 말고는 길이 없다.
#
#  🔴 그래서 **`confirmed` 를 따로 싣는다.** `editable` 을 true 로 바꾸는 것만으로
#     끝내면 「이미 확정된 항목」이라는 사실이 산출물에서 **사라진다** — 그 사실을
#     들고 있던 필드가 `editable` 하나뿐이었기 때문이다(원칙 4). 화면은 이 값으로
#     「확정됨 · 수정 가능」을 표시한다. 두 값은 뜻이 다르다:
#       editable  = 지금 고칠 수 있는가   ·  confirmed = 이미 정해진 값인가
def _source_geometry(domain: str) -> dict[str, dict]:
    """STEP0 프로파일에서 **원본 형태**를 읽는다. dataset_id → `{geometry, rows, why}`.

    🔴 **`exclusion_type` 을 쓰지 않는 이유.** 그 필드는 감리 AI 의 판정이고,
       CLAUDE.md 함정표 「exclusion_type 오판」이 가리키는 바로 그 값이다 —
       S9(`gam4_exclusion_shape.resolve`)가 존재하는 이유가 그걸 뒤집기 위해서다.
       반면 **원본이 점이냐**는 추측이 아니라 파일에서 읽히는 사실이다
       (좌표 컬럼이 있는 csv/xlsx 는 점, `.shp`·`.gpkg` 는 면일 수 있다).

    🔴 **판정이 아니라 사실만 싣는다.** 여기서 「반경 없음은 안 된다」까지 정하지
       않는다 — 점 레이어라도 지목 배수 판정으로 면 필지가 잡히면 반경 없이도
       배제 면적이 나온다(`resolve` 의 `parts.append(...g...)` 갈래). 그래서
       게이트는 **막지 않고 알린다**. 못 읽으면 `unknown` 이다(지어내지 않는다).
    """
    try:
        from app.config import domain_paths
        p = Path(domain_paths(domain)["profiles"])
        if not p.is_file():
            return {}
        prof = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                      # 프로파일은 부가정보다 — 게이트를 죽이지 않는다
        return {}

    out: dict[str, dict] = {}
    for did, pr in (prof or {}).items():
        if not isinstance(pr, dict):
            continue
        ext = str(pr.get("extension") or "").lower().lstrip(".")
        if ext in ("shp", "gpkg", "geojson", "json"):
            geom, why = "unknown", f"공간 파일(.{ext}) — 점/면은 레이어를 열어야 안다"
        elif pr.get("has_coord_col"):
            geom, why = "point", f"좌표 컬럼 {pr.get('coord_cols')}"
        elif pr.get("has_addr_col"):
            geom, why = "point", "주소만 있음 — 지오코딩되어 점이 된다"
        else:
            geom, why = "unknown", "좌표·주소 컬럼이 없다"
        out[str(did)] = {"geometry": geom, "rows": pr.get("row_count"), "why": why}
    return out


def _questions_audit(run_id: str, domain: str) -> list[dict]:
    p = _reviewed_path(run_id, domain)
    if not p.is_file():
        raise _StepFailed(f"감리 결과가 없습니다: {p}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    region = (doc.get("facility_inference") or {}).get("region", "")
    geo = _source_geometry(domain)
    out: list[dict] = []

    def _exclusion_q(did, summary, roles, idx, f):
        role = roles[idx] if idx < len(roles) else {}
        g = geo.get(str(did)) or {}
        return {
            "kind": "exclusion",
            "dataset_id": did,
            "role_index": idx,
            "editable": True,
            # flag 가 없는 배제 role 도 질문이 된다 → **role 쪽 확정도 본다.**
            # flag 만 보면 flag 없는 확정 항목이 `confirmed:false` 로 나가
            # 「이미 정해졌다」는 표시가 항목마다 달라진다.
            "confirmed": bool(f.get("confirmed") or role.get("confirmed")),
            "summary": summary,
            "facility_type": role.get("facility_type"),
            "exclusion_type": role.get("exclusion_type"),
            "rationale": role.get("rationale", ""),
            "radius_m": role.get("배제반경_m"),
            "radius_source": role.get("source"),
            # 제안값은 확정값이 아니다 — 둘을 한 필드로 합치지 않는다.
            "proposed_m": f.get("제안값"),
            "proposal_source": f.get("출처"),
            "evidence": f.get("근거문장"),
            # False 면 "다른 시설 규정일 수 있다" — 화면에 경고로 띄울 것
            "evidence_matches_facility": f.get("근거_시설_일치"),
            # 🔴 **감리 판정이 아니라 원본 파일에서 읽은 사실**이다(`_source_geometry`).
            #    `point` 인데 「반경 없음」으로 확정하면 STEP4 에서 배제 면적 0 으로
            #    run 이 죽을 수 있다 — 그 사실을 **게이트에서** 알리기 위한 값이다.
            #    막지는 않는다: 지목 배수 판정이 면 필지를 잡으면 반경 없이도 배제가 생긴다.
            "source_geometry": g.get("geometry", "unknown"),
            "source_rows": g.get("rows"),
            "source_geometry_why": g.get("why"),
        }

    for r in doc.get("results", []):
        did = r.get("dataset_id")
        summary = r.get("summary", "")
        roles = r.get("roles") or []
        asked: set[int] = set()

        for f in r.get("hitl_flags") or []:
            ftype = f.get("type")
            if ftype == "exclusion_radius_missing":
                idx = f.get("role_index", 0)
                asked.add(idx)
                out.append(_exclusion_q(did, summary, roles, idx, f))
            elif ftype == "data_intent_unclear":
                out.append({
                    "kind": "intent",
                    "dataset_id": did,
                    "editable": True,
                    "confirmed": bool(f.get("confirmed")),
                    "summary": summary,
                    "message": f.get("message", ""),
                    "current_roles": [x.get("role") for x in roles],
                    # `needs_radius` 는 프런트가 반경 입력칸을 띄울 근거다. 배제로
                    # 승격하면 반경이 필요한데 그 질문은 **답변 전에** 만들어질 수
                    # 없으므로(role 이 아직 없다) 같은 항목의 `radius_m` 으로 받는다.
                    "choices": [
                        {"value": 1, "label": "가점(수요)",
                         "needs_weight": True, "needs_radius": False},
                        {"value": 2, "label": "감점(민감도)",
                         "needs_weight": True, "needs_radius": False},
                        {"value": 3, "label": "배제(금지)",
                         "needs_weight": False, "needs_radius": True},
                        {"value": 4, "label": "위치선정 참조용",
                         "needs_weight": False, "needs_radius": False},
                        {"value": 5, "label": "잘못 넣음·제외",
                         "needs_weight": False, "needs_radius": False},
                    ],
                })

        # 🔴 flag 가 없는 배제도 **묻는다**(2026-08-10). 배제는 미확정이면 STEP2 가
        #    멈추는데(`assert_exclusions_confirmed`), 게이트에 안 뜨면 답할 방법이
        #    없어 run 이 죽는다. 지금 두 경로(`enrich_hitl_flags`·
        #    `reset_exclusion_confirmations`)가 flag 를 보장하지만, 보장이 깨졌을 때
        #    조용히 사라지는 쪽이 아니라 **묻는 쪽**으로 넘어져야 한다.
        for idx, role in enumerate(roles):
            if role.get("role") != "hard_exclusion" or idx in asked:
                continue
            out.append(_exclusion_q(did, summary, roles, idx, {}))

        for oi, op in enumerate(r.get("cleaning_ops") or []):
            if op.get("op_id") != "filter_by_code_prefix":
                continue
            prm = op.get("params") or {}
            chk = prm.get("prefix_check") or {}
            out.append({
                "kind": "code_prefix",
                "dataset_id": did,
                # `cleaning_ops` **전체** 기준 인덱스다. filter_by_code_prefix 만
                # 센 번호가 아니다 — 적용할 때 같은 방식으로 찾는다.
                "op_index": oi,
                "editable": True,
                "confirmed": bool(prm.get("prefix_confirmed")),
                "summary": summary,
                "col": prm.get("col"),
                "prefix": prm.get("prefix", ""),
                "region": region,
                "verdict": chk.get("verdict"),
                "reason": chk.get("reason"),
                "detail": chk.get("detail"),
                "suggestion": chk.get("suggestion"),
                "confirmed_by": prm.get("prefix_confirmed_by"),
                # 🔴 감리 때 코드표 대조를 못 했으면(`prefix_check` 없음/unknown)
                #    여기서 다시 판정하지 않는다. `_code_samples` 가 `build_fixtures()`
                #    를 부르고 모듈 전역에 캐시하는데, 이건 오래 사는 API 프로세스가
                #    할 일이 아니다. 못 한 건 못 했다고 내보낸다(원칙 4·5).
                "recheck_skipped": not chk or chk.get("verdict") == "unknown",
            })
    return out


# ── 게이트B 질문 ───────────────────────────────────────────────────
def _questions_weight(run_id: str, domain: str) -> list[dict]:
    p = _proposal_path(run_id, domain)
    if not p.is_file():
        raise _StepFailed(f"가중치 제안이 없습니다: {p}")
    prop = json.loads(p.read_text(encoding="utf-8"))
    conflicts = {c["indicator_id"]: c for c in prop.get("conflicts", [])}
    out = []
    for ind in prop["indicators"]:
        iid = ind["id"]
        rp = (prop.get("radius_proposed") or {}).get(iid) or {}
        out.append({
            "kind": "weight",
            "indicator_id": iid,
            "indicator_kind": ind["kind"],
            # admin 지표는 행정동 단위라 반경 개념이 없다. 답에 넣으면 400 이다.
            "radius_required": ind["kind"] != "admin",
            "direction": ind["direction"],
            "seed_weight": ind["seed_weight"],
            "components": ind.get("components"),
            "rationale": ind.get("rationale", ""),
            "data_note": ind.get("data_note", ""),
            "radius_proposed": rp.get("radius_m"),
            "radius_rationale": rp.get("rationale", ""),
            "radius_source": rp.get("source"),
            "slider_proposed": (prop.get("slider_proposed") or {}).get(iid),
            # 방향 판정 충돌 — 사람이 슬라이더 **부호**로 정해야 넘어간다.
            "conflict": conflicts.get(iid),
        })
    return out


