# -*- coding: utf-8 -*-
"""HITL 게이트 — **답을 받는 쪽**.

🔴 화이트리스트는 **한 겹만 치면 안 친 것과 같다.** 바깥(payload 최상위 키)을
   막고 항목 내부를 안 보면, 거절당할 줄 알았던 필드가 조용히 사라진다
   (2026-08-10 — `intents` 의 `radius_m` 이 200 인데 값이 버려졌다).
🔴 답변 적용은 `apply_radius_answer`·`apply_intent_answer`·`apply_weight_hitl`
   **정본 함수로만** 한다. 여기서 다시 구현하면 CLI 와 갈라진다.
"""

from __future__ import annotations

import json
import os

from .gates import _answer_path, _read_answer, _reviewed_path, _save_answer
from .state import (
    MODE_FULL,
    MODE_HITL,
    _ACTIVE,
    _LOCK,
    RunConflict,
    RunRequestError,
    _StepFailed,
)
from .status import _write_status, read_status
from .steps import AUTO_APPROVE_SRC, GATE_IDS, _resume_index


# ── 답변 접수 ──────────────────────────────────────────────────────
def submit_gate(run_id: str, gate_id: str, payload: dict) -> dict:
    """게이트 답을 검증·적용하고 실행을 이어간다. 갱신된 status 를 돌려준다."""
    if gate_id not in GATE_IDS:
        raise RunRequestError(f"알 수 없는 게이트: {gate_id!r}")
    doc = read_status(run_id)
    if doc is None:
        raise KeyError(run_id)              # 라우터가 404
    if doc.get("status") != "awaiting_hitl":
        raise RunRequestError(
            f"이 run 은 사람 확정을 기다리고 있지 않습니다 (status={doc.get('status')!r})")
    gate = doc.get("gate") or {}
    if gate.get("id") != gate_id:
        raise RunRequestError(
            f"지금 기다리는 게이트는 '{gate.get('id')}' 입니다 (요청: '{gate_id}')")
    if not isinstance(payload, dict):
        raise RunRequestError("요청 본문이 객체가 아닙니다.")
    # 계약 7-5 가 body 에 run_id 를 둔다. 경로와 다르면 프런트가 다른 run 을 보고 있다 —
    # 조용히 경로 쪽을 쓰면 남의 run 에 답을 적용한다.
    if payload.get("run_id") not in (None, run_id):
        raise RunRequestError(
            f"본문 run_id 가 경로와 다릅니다: {payload.get('run_id')!r} != {run_id!r}")

    domain = doc["domain"]
    mode = doc.get("mode", MODE_HITL)
    questions = gate.get("questions") or []
    if gate_id == "audit":
        _apply_audit(run_id, domain, questions, payload)
    else:
        _validate_weight(questions, payload)

    # 🔴 서버가 재시작되면 `_ACTIVE` 는 비지만 `awaiting_hitl` 인 run 은 디스크에 남는다
    #    (`reap_orphans` 는 queued/running 만 닫는다 — 게이트 대기는 중단이 아니다).
    #    그 상태에서 답이 오면 여기서 다시 점유한다. 안 하면 같은 도메인에 새 run 이
    #    동시에 돌아 정본 캐시·데이터를 함께 건드린다.
    with _LOCK:
        other = _ACTIVE.get(domain)
        if other and other != run_id:
            raise RunConflict(f"'{domain}' 은 이미 실행 중입니다 (run_id={other})")
        _ACTIVE[domain] = run_id

    _save_answer(run_id, gate_id, payload)
    doc["status"] = "running"
    doc.pop("gate", None)
    _write_status(run_id, doc)
    # 🔴 **함수 안에서** 부른다 — 파일 맨 위로 올리면 `answers ↔ runner` 사이클이다
    #    (`runner` 는 답 적용을 위해 `answers._run_auto_gate`·`_stage_args` 를 쓴다).
    #    호출 시점에 이름을 다시 푸는 형태라 대조기의 monkeypatch 도 그대로 먹는다.
    from .runner import _spawn

    _spawn(run_id, domain, mode, _resume_index(mode, gate_id))
    return doc


def _q(questions: list[dict], kind: str, **key) -> dict:
    """질문 목록에서 대상 하나를 찾는다. 없으면 400 — 조용히 무시하지 않는다."""
    for q in questions:
        if q["kind"] == kind and all(q.get(k) == v for k, v in key.items()):
            return q
    raise RunRequestError(f"게이트에 없는 대상입니다: {kind} {key}")


def _int_in(v, lo: int, hi: int, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise RunRequestError(f"{what} 은 정수여야 합니다: {v!r}")
    if not (lo <= v <= hi):
        raise RunRequestError(f"{what} 범위는 {lo}~{hi} 입니다: {v}")
    return v


def _num_in(v, lo: float, hi: float, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise RunRequestError(f"{what} 은 숫자여야 합니다: {v!r}")
    if not (lo <= v <= hi):
        raise RunRequestError(f"{what} 범위는 {lo}~{hi} 입니다: {v}")
    return float(v)


def _only_keys(item: dict, allowed: tuple[str, ...], what: str) -> None:
    """항목 안의 알 수 없는 키를 400 으로 막는다.

    🔴 최상위 키는 예전부터 막았는데 **항목 내부는 안 봤다**(2026-08-10 실측).
       그래서 `radius_m` 오타(`radius_mm`)가 「건너뜀 = 미확정 유지」로 읽히고,
       `intents` 에 실은 `radius_m` 은 **200 인데 값이 버려졌다.** 조용히 버리면
       프런트는 성공으로 읽고 run 은 STEP2 에서 죽는다(원칙 1·4).
    """
    if not isinstance(item, dict):
        raise RunRequestError(f"{what} 항목은 객체여야 합니다: {item!r}")
    bad = [k for k in item if k not in allowed]
    if bad:
        raise RunRequestError(
            f"{what} 에 알 수 없는 필드: {bad}. 쓸 수 있는 것: {list(allowed)}")


def _exclusion_flag(result: dict, role_index: int, message: str) -> dict:
    """`exclusion_radius_missing` flag 를 찾고, 없으면 만든다.

    질문은 flag 가 없는 배제 role 로도 만들어진다(`_questions_audit`). 없다고 답을
    버리면 사람이 답한 것이 조용히 사라지고 STEP2 는 미확정이라며 멈춘다.
    """
    flags = result.setdefault("hitl_flags", [])
    for f in flags:
        if f.get("type") == "exclusion_radius_missing" and f.get("role_index", 0) == role_index:
            return f
    flag = {"type": "exclusion_radius_missing", "role_index": role_index,
            "message": message, "제안값": None, "출처": None}
    flags.append(flag)
    return flag


def _drop_exclusion(result: dict, q: dict, auto: bool) -> None:
    """배제(hard_exclusion) 를 **적용하지 않기로** 확정한다 → role 을 `reference_only` 로.

    `_guard_zero_area`(gam4_site_select) 가 직접 안내하는 두 선택지 중 하나다 —
    「배제반경을 입력하거나, **그 레이어를 hard_exclusion 에서 빼세요**」. 반경 근거가
    없는 점 레이어는 앞을 고르면 값을 지어내는 것이므로(원칙 2) 뒤가 유일한 답이다.

    🔴 **버리는 게 아니라 강등**이다. `reference_only` 는 STEP2 가 정제는 그대로 하고
       GIS 입력에서만 빼는 기존 어휘다(`gam2_clean_data:372`) — 데이터는 페르소나·참조로
       계속 쓰인다. `roles: []`(제외)로 만들면 그 데이터셋이 통째로 사라진다.

    🔴 배제를 **안 했다는 사실**을 세 곳에 남긴다: role(`배제_해제`·사유·이전 상태) ·
       flag · `report.json` 의 gap(`배제_해제`, `gam4_export`). 한 곳만 적으면 그 한 곳을
       안 보는 사람에게는 「배제가 원래 없었다」로 읽힌다(원칙 4).
    """
    idx = q.get("role_index", 0)
    roles = result.get("roles") or []
    if idx >= len(roles):
        raise RunRequestError(f"[{q.get('dataset_id')}] roles[{idx}] 이 없습니다.")
    role = roles[idx]
    why = (
        f"배제반경 근거가 없고 원본이 점 레이어입니다"
        f"({q.get('source_geometry_why') or '좌표 컬럼'}) — 반경 없이 확정하면 배제 면적이"
        f" 0 이라 STEP4 에서 멈춥니다. 배제를 적용하지 않고 참조용으로만 씁니다."
    )
    role["배제_해제_이전"] = {
        "role": role.get("role"),
        "exclusion_type": role.get("exclusion_type"),
        "facility_type": role.get("facility_type"),
        "배제반경_m": role.get("배제반경_m"),
    }
    role["role"] = "reference_only"
    role["배제_해제"] = True
    role["배제_해제_사유"] = why
    role["confirmed"] = True
    role["need_review"] = False
    role["source"] = AUTO_APPROVE_SRC if auto else "human_confirmed"

    flag = _exclusion_flag(result, idx, why)
    flag["message"] = why
    flag["배제_해제"] = True
    flag["confirmed"] = True
    flag["confirmed_by_human"] = not auto
    if auto:
        flag["자동승인"] = True


def _apply_audit(run_id: str, domain: str, questions: list[dict], payload: dict,
                 auto: bool = False) -> None:
    """게이트A 답을 reviewed.json 에 반영한다. **정본 함수를 그대로 부른다.**

    🔴 `radius_m` 은 `null`(반경 없음으로 확정)과 **키 생략**(건너뜀 — 미확정 유지)이
       다른 뜻이다. CLI 의 `n` 과 `s` 에 각각 대응한다.

    🔴 `auto` 는 「고속 자동 분석」이 AI 제안값을 그대로 넣은 실행이다. **검증·적용
       경로는 사람 답과 한 글자도 다르지 않다** — 갈라두면 자동 경로만 통과하는
       값이 생긴다. 갈리는 것은 **누가 정했는가** 하나뿐이고, 그래서 산출물에
       `human_confirmed`·`prefix_confirmed_by:"human"` 을 적지 않는다. 적으면
       사람이 본 적 없는 값이 「사람이 확정함」으로 남는다(원칙 4).
    """
    # 늦은 import — 2,000행짜리 감리 모듈을 서버 기동 때 끌고 오지 않는다.
    # (이 모듈 자체는 DB·네트워크를 안 건드린다. 실측 확인함)
    from app.services import gam2_audit_judgment_test as A

    for key in payload:
        if key not in ("run_id", "exclusions", "intents", "code_prefixes"):
            raise RunRequestError(f"알 수 없는 필드: {key!r}")

    path = _reviewed_path(run_id, domain)
    doc = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r.get("dataset_id"): r for r in doc.get("results", [])}

    # 정본 함수들이 쓰는 도메인 경로(프리픽스·data·law)를 확정한다.
    # (배제반경 캐시는 2026-08-10 제거됐다 — 확정은 이 run 안에서만 유효하다)
    A.set_domain(domain)

    def _radius_answer(result: dict, flag: dict, radius: int | None) -> None:
        A.apply_radius_answer(
            result, flag, radius,
            source=AUTO_APPROVE_SRC if auto else "human_confirmed")
        if auto:
            # 🔴 정본 함수는 「사람이 확인함」을 무조건 켠다(:723) — 그 함수의
            #    호출자가 여태 사람뿐이었기 때문이다. 자동승인은 사람이 본 적이
            #    없으므로 여기서 되돌리고, 대신 **무슨 일이 있었는지**를 남긴다.
            flag["confirmed_by_human"] = False
            flag["자동승인"] = True

    # 🔴 **「이미 확정됐으니 수정 불가」검사는 없다**(2026-08-12 제거). 예전엔 세 갈래
    #    각각에 `if not q["editable"]: raise` 가 있었다. 두 가지 이유로 지웠다:
    #      ⓐ `editable` 은 이제 **항상 true** 다(그 근거는 `_questions_audit` 위 주석).
    #         남겨두면 영원히 안 도는 분기가 「그런 규칙이 아직 있다」고 말한다.
    #      ⓑ 애초에 이 검사는 **우리가 방금 만든 질문 dict 를 우리가 되읽는** 것이라
    #         재는 자와 재어지는 자가 같았다. 요청이 보낸 값을 막는 게 아니었다.
    #    확정 여부는 이제 질문의 `confirmed` 로 **화면에 알리기만** 한다.
    for item in payload.get("exclusions") or []:
        _only_keys(item, ("dataset_id", "role_index", "radius_m", "drop"), "exclusions")
        q = _q(questions, "exclusion", dataset_id=item.get("dataset_id"),
               role_index=item.get("role_index"))
        if item.get("drop"):
            # 🔴 배제 해제도 **확정**이다(미확정 유지가 아니다). 반경과 같이 오면
            #    「빼겠다」와 「이 반경으로 배제하겠다」가 동시에 참일 수 없다.
            if "radius_m" in item:
                raise RunRequestError(
                    f"[{q['dataset_id']}] drop 과 radius_m 은 같이 못 씁니다 — "
                    "배제를 빼거나 반경을 정하거나 하나입니다.")
            _drop_exclusion(by_id[q["dataset_id"]], q, auto)
            continue
        if "radius_m" not in item:
            continue                    # 건너뜀 = 미확정 유지. CLI 의 's'
        radius = item["radius_m"]
        if radius is not None:
            radius = _int_in(radius, 1, 5000, f"[{q['dataset_id']}] 배제반경(m)")
        r = by_id[q["dataset_id"]]
        _radius_answer(
            r, _exclusion_flag(r, q["role_index"], "게이트A 에서 직접 확정"), radius)

    for item in payload.get("intents") or []:
        _only_keys(item, ("dataset_id", "choice", "weight", "radius_m"), "intents")
        q = _q(questions, "intent", dataset_id=item.get("dataset_id"))
        choice = _int_in(item.get("choice"), 1, 5, f"[{q['dataset_id']}] choice")
        weight = item.get("weight")
        if choice in (1, 2):
            # 🔴 `apply_intent_answer` 는 abs(weight) 를 쓴다 — None 이면 TypeError 다.
            #    부호는 choice 가 정하므로 여기서는 크기만 받는다.
            weight = _num_in(weight, -1.0, 1.0, f"[{q['dataset_id']}] weight")
            if weight == 0:
                raise RunRequestError(
                    f"[{q['dataset_id']}] 가점/감점인데 크기가 0 입니다. "
                    "제외하려면 choice=5 를 쓰세요.")
        elif weight is not None:
            raise RunRequestError(
                f"[{q['dataset_id']}] weight 는 choice 1·2 에서만 씁니다.")
        if choice != 3 and "radius_m" in item:
            raise RunRequestError(
                f"[{q['dataset_id']}] radius_m 은 choice 3(배제 승격)에서만 씁니다.")

        r = by_id[q["dataset_id"]]
        A.apply_intent_answer(r, choice, weight)

        # 🔴 배제 승격은 **반경을 같은 항목에서 받는다**(2026-08-10, 사람 결정).
        #    `apply_intent_answer(…, 3)` 은 `배제반경_m: None · confirmed: False` 인
        #    role 을 새로 만든다 → 미확정이라 STEP2 가 멈추는데
        #    (`assert_exclusions_confirmed`), 게이트A 질문 목록은 **답변 전에** 만들어져
        #    이 role 의 질문이 없다. 그래서 `exclusions` 로도 답할 수 없었다(400).
        #    여기서 안 받으면 그 run 은 **답할 자리가 없는 채** 죽는다.
        if choice == 3:
            # roles 를 통째로 갈아치웠으므로 옛 확정은 무효다. 지우지 않으면 게이트를
            # 다시 열었을 때 그 flag 가 `confirmed: true` 로 남아, 아무도 답하지 않은
            # 반경이 「사람이 확정했다」로 읽힌다(원칙 4). 예전엔 같은 값이
            # `editable: false` 로도 굳어 아예 답할 수가 없었다 — 그건 2026-08-12 에
            # 없어졌지만, 거짓 확정 표시는 여전히 남으므로 이 정리는 그대로 둔다.
            flag = _exclusion_flag(r, 0, "게이트A 배제 승격 — 반경 확정")
            for k in ("confirmed", "confirmed_by_human", "제안값", "출처", "근거_시설_일치"):
                flag.pop(k, None)
            if "radius_m" in item:      # 키 생략 = 미확정 유지(exclusions 와 같은 규약)
                radius = item["radius_m"]
                if radius is not None:
                    radius = _int_in(radius, 1, 5000, f"[{q['dataset_id']}] 배제반경(m)")
                _radius_answer(r, flag, radius)

    for item in payload.get("code_prefixes") or []:
        _only_keys(item, ("dataset_id", "op_index", "prefix"), "code_prefixes")
        q = _q(questions, "code_prefix", dataset_id=item.get("dataset_id"),
               op_index=item.get("op_index"))
        prefix = item.get("prefix")
        if not isinstance(prefix, str) or not prefix.strip():
            raise RunRequestError(f"[{q['dataset_id']}] prefix 가 비어 있습니다.")
        op = by_id[q["dataset_id"]]["cleaning_ops"][q["op_index"]]
        prm = op.setdefault("params", {})
        prm["prefix"] = prefix.strip()
        prm["prefix_confirmed"] = True
        prm["prefix_confirmed_by"] = AUTO_APPROVE_SRC if auto else "human"

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _validate_weight(questions: list[dict], payload: dict) -> None:
    """게이트B 답 검증. 적용은 `_stage_args` 가 `--radius`·`--weight` 로 넘긴다.

    여기서 막는 것은 **하류에서 조용히 틀릴 것들**이다:
      · 반경 누락 → run_weight_model 이 [R] HITL 로 내려가 stdin 없이 EOFError
      · 충돌 지표 슬라이더 누락 → `--auto-weight` 가 `:352` 에서 ValueError
        (그 자리에서 죽는 건 옳다. 다만 **게이트에서 400 으로 되돌리는 게 낫다** —
         사람이 답을 고칠 수 있는 곳이 게이트뿐이다)
      · 절대값 합 0 → `apply_weight_hitl:1108` ValueError → 전 후보 점수 0
    """
    for key in payload:
        if key not in ("run_id", "radius", "slider"):
            raise RunRequestError(f"알 수 없는 필드: {key!r}")
    radius = payload.get("radius") or {}
    slider = payload.get("slider") or {}
    if not isinstance(radius, dict) or not isinstance(slider, dict):
        raise RunRequestError("radius·slider 는 {지표ID: 값} 객체여야 합니다.")

    known = {q["indicator_id"]: q for q in questions if q["kind"] == "weight"}
    need_radius = {i for i, q in known.items() if q["radius_required"]}

    unknown = sorted((set(radius) | set(slider)) - set(known))
    if unknown:
        raise RunRequestError(f"게이트에 없는 지표ID: {unknown}")
    missing = sorted(need_radius - set(radius))
    if missing:
        raise RunRequestError(f"집계반경이 빠진 지표: {missing}")
    extra = sorted(set(radius) - need_radius)
    if extra:
        raise RunRequestError(
            f"행정동 단위 지표에는 집계반경이 없습니다: {extra}")
    for iid, v in radius.items():
        _int_in(v, 1, 5000, f"[{iid}] 집계반경(m)")

    conflicted = sorted(i for i, q in known.items() if q.get("conflict"))
    unresolved = sorted(set(conflicted) - set(slider))
    if unresolved:
        raise RunRequestError(
            f"방향 판정이 충돌한 지표는 슬라이더 부호로 확정해야 합니다: {unresolved}")
    merged = {i: q["slider_proposed"] for i, q in known.items()}
    for iid, v in slider.items():
        merged[iid] = _num_in(v, -1.0, 1.0, f"[{iid}] 슬라이더")
    if sum(abs(v or 0.0) for v in merged.values()) == 0:
        raise RunRequestError(
            "전 지표 슬라이더 절대값 합이 0 입니다 — 모든 후보 점수가 0 이 됩니다.")


def _auto_answer(gate_id: str, questions: list[dict]) -> dict:
    """게이트 질문을 **AI 제안값만으로** 채운 답을 만든다(「고속 자동 분석」).

    🔴 여기서 값을 **지어내지 않는다.** 넣는 것은 질문이 이미 들고 있는 제안값뿐이고,
       제안이 없으면 없는 대로 답한다. 없는 자리를 기본값으로 메우면 그건 자동승인이
       아니라 **러너가 도메인 값을 정하는 것**이다(원칙 2·5).

    갈래마다 「제안이 없다」의 뜻이 다르다 —
      · exclusion  : 제안값 → 현재값 순으로 쓰고, 둘 다 없으면 `radius_m: null`.
                     그건 **「반경 없음」 확정**이지 건너뜀이 아니다. 키를 빼면
                     미확정으로 남아 STEP2 가 `assert_exclusions_confirmed` 로
                     멈춘다 — 자동 모드가 게이트만 지나고 다음 칸에서 죽는다.
                     🔴 **원본이 점인데 제안이 없으면 `drop`(배제 해제)으로 답한다.**
                     `null` 로 확정하면 점들의 union 이라 배제 면적이 0 이고,
                     STEP4 `_guard_zero_area` 가 몇 분 뒤에 죽인다(실측
                     `r_20260813_005` — 재활용 03 도시공원, 게이트 통과 후
                     94초 뒤 STEP4 에서 중단). 그 자리에서 반경을 **지어내면**
                     러너가 도메인 값을 정하는 것이고(원칙 2), 예전처럼
                     **거절하면** 「모두 자동승인」이 성립하지 않는다.
                     남는 답은 하나다 — `_guard_zero_area` 가 스스로 안내하는
                     「그 레이어를 hard_exclusion 에서 빼세요」. 배제를 **적용하지
                     않았다는 사실**은 role·flag·gap(`배제_해제`) 세 곳에 남으므로
                     조용히 사라지지 않는다(원칙 4). 데이터는 안 버린다 —
                     `reference_only` 라 STEP2 정제는 그대로 돌고 GIS 입력에서만
                     빠진다.
                     ⚠ 사람 게이트는 같은 자리에서 막지 않는다: 화면에
                     `source_geometry` 가 실려 있어 사람이 보고 반경을 정할 수
                     있고, 지목 배수 판정이 면 필지를 잡으면 반경 없이도 배제가
                     생기기 때문이다. `drop` 은 사람도 쓸 수 있다 — 자동 전용
                     어휘를 만들면 「자동 경로에서만 통과하는 값」이 생긴다.
                     ⚠ 판정 근거는 감리의 `exclusion_type`(폴리곤이라 우겼다)이
                     아니라 원본 파일에서 읽은 `source_geometry` 다.
      · intent     : **choice 4(위치선정 참조용)** 하나뿐이다. 1·2 는 AI 가 제안한
                     적 없는 `weight` 숫자를 요구하고, 3 은 반경까지 지어내야 하며,
                     5 는 데이터를 버린다. 4 는 감리에서만 빼고 데이터는 살린다 —
                     이 flag 를 만드는 코드가 붙여 보내는 제안(「참조용이면 감리에서
                     제외하고 위치선정 단계에서 사용」)과 같은 뜻이다.
      · code_prefix: 대조기가 낸 `suggestion`, 없으면 감리가 쓰던 `prefix` 그대로.
      · weight     : 반경이 필요한 지표는 전부 `radius_proposed` 로 채운다(빠지면
                     자식이 [R] 대화형으로 내려가 stdin 없이 EOFError). 슬라이더는
                     **제안이 있는 것만** 넣는다 — 안 넣으면 자식이 자기 제안값을
                     쓰므로 같은 값이고, `null` 을 넣으면 검증에서 400 이다.
                     방향 충돌 지표에 제안이 없으면 여기서 메우지 않는다:
                     `_validate_weight` 의 `unresolved` 가 **시끄럽게** 막는다.
    """
    if gate_id == "audit":
        exclusions, intents, prefixes = [], [], []
        for q in questions:
            if q["kind"] == "exclusion":
                r = q.get("proposed_m")
                if r is None:
                    r = q.get("radius_m")
                if r is None and q.get("source_geometry") == "point":
                    exclusions.append({"dataset_id": q["dataset_id"],
                                       "role_index": q["role_index"],
                                       "drop": True})
                    continue
                exclusions.append({"dataset_id": q["dataset_id"],
                                   "role_index": q["role_index"],
                                   "radius_m": r})
            elif q["kind"] == "intent":
                intents.append({"dataset_id": q["dataset_id"], "choice": 4})
            elif q["kind"] == "code_prefix":
                prefixes.append({"dataset_id": q["dataset_id"],
                                 "op_index": q["op_index"],
                                 "prefix": q.get("suggestion") or q.get("prefix")})
        return {"exclusions": exclusions, "intents": intents,
                "code_prefixes": prefixes}

    radius, slider = {}, {}
    for q in questions:
        if q["kind"] != "weight":
            continue
        iid = q["indicator_id"]
        if q["radius_required"]:
            radius[iid] = q.get("radius_proposed")
        if q.get("slider_proposed") is not None:
            slider[iid] = q["slider_proposed"]
    return {"radius": radius, "slider": slider}


def _run_auto_gate(run_id: str, domain: str, gate_id: str, gate: dict) -> dict:
    """게이트를 사람 대신 AI 제안값으로 통과시킨다. 반환 = 실제로 적용한 답.

    🔴 검증·적용은 `submit_gate` 와 **같은 함수**를 부른다. 자동 경로만 따로 짜면
       사람 답이었으면 400 이었을 값이 조용히 통과한다.
    """
    questions = gate.get("questions") or []
    try:
        # 🔴 답을 **만드는 것**도 try 안이다. 「AI 제안값으로는 못 채운다」는
        #    검증 실패와 같은 뜻이고, 같은 문구로 알려야 한다.
        payload = _auto_answer(gate_id, questions)
        if gate_id == "audit":
            _apply_audit(run_id, domain, questions, payload, auto=True)
        else:
            _validate_weight(questions, payload)
    except RunRequestError as e:
        # 🔴 삼키지 않는다. AI 제안값으로 못 채우는 게이트는 **사람이 봐야 하는**
        #    게이트다 — 조용히 넘기면 그 자리를 아무도 안 본 채 run 이 완주한다.
        raise _StepFailed(
            f"자동승인: AI 제안값으로 게이트 '{gate_id}' 를 채울 수 없습니다 — {e}. "
            f"맞춤형 대화 분석 모드로 다시 돌리면 이 자리를 직접 확정할 수 있습니다."
        ) from e
    _save_answer(run_id, gate_id, payload, by=AUTO_APPROVE_SRC)
    return payload


def _stage_args(run_id: str, mode: str, stage: str) -> tuple[str | None, str | None]:
    """단계에 넘길 `--radius`·`--weight`. 게이트B 답이 여기서 CLI 인자로 바뀐다.

    🔴 사람 답을 코드로 다시 해석하지 않는다. 받은 값을 그대로 문자열로 옮긴다.
       (`slider` 는 `-1~+1` 그대로 — 분해는 `apply_weight_hitl` 이 경계에서 한다)
    """
    if mode not in (MODE_HITL, MODE_FULL) or stage != "3-2":
        return (None, None)
    ans = _read_answer(run_id, "weight")
    if ans is None:                       # 게이트를 안 거치고 3-2 에 온 것 = 러너 버그
        raise RuntimeError(f"게이트B 답변이 없습니다: {_answer_path(run_id, 'weight')}")
    radius = ",".join(f"{k}={int(v)}" for k, v in (ans.get("radius") or {}).items())
    weight = ",".join(f"{k}={v}" for k, v in (ans.get("slider") or {}).items())
    return (radius or None, weight or None)

