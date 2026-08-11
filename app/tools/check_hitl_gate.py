# -*- coding: utf-8 -*-
"""
HITL 게이트 로직 대조 (A2) — 파이프라인을 돌리지 않는다 (LLM 호출 0회)
=====================================================================
  python app/tools/check_hitl_gate.py 흡연

무엇을 보는가
  `app/services/pipeline_runner.py` 의 **게이트 부분만** 떼어 확인한다.
    1. 계획 배열과 재개 위치      — 재개 칸이 게이트면 무한 대기가 된다
    2. 게이트A 질문 생성          — 픽스처 `reviewed.json` 에서 뽑히는지
    3. 게이트A 답변 거부 규칙      — 확정분 수정·없는 대상·알 수 없는 필드
    4. 게이트A 답변 적용          — 합성한 **미확정** 항목에 실제로 값이 박히는지
    5. 게이트B 검증 규칙          — 반경 누락·범위·충돌 미확정·합 0
    6. 답변 → CLI 인자 왕복       — 정본 파서(`run_weight_model`)로 되읽어 대조

왜 임시 폴더인가
  `runs/` 의 실제 run 을 읽으면 **남의 세션이 지운 순간 테스트가 죽는다.**
  픽스처 `<도메인>_FIX/reviewed.json` 을 임시 run 폴더로 복사해서 본다.
  기대값도 그 파일에서 **파생**한다 — 숫자를 여기 적으면 픽스처와 갈린다.

🔴 게이트B 는 여기서 **검증 함수만** 본다. 질문 생성(`_questions_weight`)은 제안
   패스가 만든 산출물이 필요하고, 그건 파이프라인 실행이라 이 스크립트 범위 밖이다.
   완주 확인은 `app/tools/check_hitl_e2e.py` 의 몫이다.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 이 스크립트는 `app/tools/` 안에 있다 — 저장소 루트는 두 단계 위다.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.config import DOMAIN_ROOT, domain_prefix  # noqa: E402
from app.services import pipeline_runner as R  # noqa: E402

DOMAIN = sys.argv[1] if len(sys.argv) > 1 else "흡연"
PRE = domain_prefix(DOMAIN)
FIX = Path(DOMAIN_ROOT) / f"{DOMAIN}_FIX" / "reviewed.json"

ok = fail = 0


def chk(name: str, cond: bool, extra: object = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  OK   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}  {extra}")


def err(name: str, fn, frag: str) -> None:
    """`RunRequestError`(=400) 가 나야 하고, 문구에 `frag` 가 있어야 한다."""
    try:
        fn()
        chk(name, False, "예외가 안 났다")
    except R.RunRequestError as e:
        chk(name, frag in str(e), f"문구: {e}")
    except Exception as e:  # 다른 예외 = 400 이 아니다
        chk(name, False, f"{type(e).__name__}: {e}")


def make_run(doc: dict, tmp: str, rid: str = "r_chk") -> None:
    d = Path(tmp) / rid / "step1"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{PRE}_audit_result_reviewed.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8"
    )


if not FIX.is_file():
    sys.exit(f"🔴 픽스처가 없습니다: {FIX}")
print(f"기준 픽스처 {FIX}")
BASE_DOC = json.loads(FIX.read_text(encoding="utf-8"))

_orig_run_dir = R.run_dir
tmp = tempfile.mkdtemp(prefix="hitl_chk_")
R.run_dir = lambda rid: Path(tmp) / rid  # type: ignore[assignment]
try:
    # ── [1] 계획·재개 위치 ─────────────────────────────────────────
    print("\n[1] 계획·재개 위치")
    chk(
        "fixture 계획",
        R._PLAN["fixture"] == ("2", "3-1", "3-2", "4"),
        R._PLAN["fixture"],
    )
    chk(
        "hitl 계획",
        R._PLAN["hitl"]
        == ("gate:audit", "2", "3-1", "propose", "gate:weight", "3-2", "4"),
        R._PLAN["hitl"],
    )
    chk("audit 재개=1", R._resume_index("hitl", "audit") == 1)
    chk("weight 재개=5", R._resume_index("hitl", "weight") == 5)
    # 재개 칸이 게이트면 답을 받자마자 같은 게이트로 다시 멈춘다 = 무한 대기.
    chk(
        "재개칸이 게이트가 아니다",
        all(
            not R._PLAN["hitl"][R._resume_index("hitl", g)].startswith("gate:")
            for g in R.GATE_IDS
        ),
    )

    # ── [2] 게이트A 질문 — 기대값은 픽스처에서 파생한다 ──────────────
    print("\n[2] 게이트A 질문 (픽스처에서 파생한 기대값)")
    make_run(BASE_DOC, tmp)
    qs = R._questions_audit("r_chk", DOMAIN)

    # 🔴 배제 질문의 기대값은 **flag 가 아니라 `hard_exclusion` role 수**다
    #    (2026-08-10). flag 없는 배제도 묻는다 — 안 물으면 STEP2 가 미확정으로
    #    멈췄을 때 답할 자리가 없다. 픽스처 실측: flag 3 · role 5(06·07 은 flag 없음).
    want_ex = want_it = want_cp = 0
    want_radii: list[int] = []
    for r in BASE_DOC.get("results", []):
        for role in r.get("roles") or []:
            if role.get("role") == "hard_exclusion":
                want_ex += 1
                want_radii.append(role.get("배제반경_m"))
        for f in r.get("hitl_flags") or []:
            if f.get("type") == "data_intent_unclear":
                want_it += 1
        want_cp += sum(
            1
            for o in r.get("cleaning_ops") or []
            if o.get("op_id") == "filter_by_code_prefix"
        )
    kinds = [q["kind"] for q in qs]
    chk(f"배제 {want_ex}건", kinds.count("exclusion") == want_ex, kinds)
    chk(f"의도 {want_it}건", kinds.count("intent") == want_it, kinds)
    chk(f"지역코드 {want_cp}건", kinds.count("code_prefix") == want_cp, kinds)
    got_radii = sorted(q["radius_m"] for q in qs if q["kind"] == "exclusion")
    chk(
        "배제반경 값이 role 에서 실린다",
        got_radii == sorted(x for x in want_radii if x is not None)
        or got_radii == sorted(want_radii, key=lambda v: (v is None, v)),
        (got_radii, want_radii),
    )
    # 픽스처는 사람이 이미 확정한 결과다 → 전부 읽기 전용이어야 한다.
    chk(
        "픽스처 질문은 전부 읽기 전용",
        all(not q["editable"] for q in qs),
        [(q["kind"], q["dataset_id"]) for q in qs if q["editable"]],
    )
    for cp in [q for q in qs if q["kind"] == "code_prefix"]:
        src = next(
            r for r in BASE_DOC["results"] if r["dataset_id"] == cp["dataset_id"]
        )
        chk(
            f"op_index 로 실제 op 를 찾는다 ({cp['dataset_id']})",
            src["cleaning_ops"][cp["op_index"]]["op_id"] == "filter_by_code_prefix",
            cp["op_index"],
        )

    # ── [3] 게이트A 답변 — 읽기 전용은 수정 못 한다 ──────────────────
    print("\n[3] 게이트A 답변 거부 규칙")
    ex0 = next((q for q in qs if q["kind"] == "exclusion"), None)
    if ex0:
        err(
            "확정분 수정 400",
            lambda: R._apply_audit(
                "r_chk",
                DOMAIN,
                qs,
                {
                    "exclusions": [
                        {
                            "dataset_id": ex0["dataset_id"],
                            "role_index": ex0["role_index"],
                            "radius_m": 999,
                        }
                    ]
                },
            ),
            "이미 확정된 항목",
        )
    err(
        "없는 대상 400",
        lambda: R._apply_audit(
            "r_chk",
            DOMAIN,
            qs,
            {
                "exclusions": [
                    {"dataset_id": "__없음__", "role_index": 0, "radius_m": 10}
                ]
            },
        ),
        "게이트에 없는 대상",
    )
    err(
        "알 수 없는 필드 400",
        lambda: R._apply_audit("r_chk", DOMAIN, qs, {"foo": []}),
        "알 수 없는 필드",
    )
    chk(
        "빈 답은 통과 (고칠 게 없다)",
        R._apply_audit("r_chk", DOMAIN, qs, {"run_id": "r_chk"}) is None,
    )

    # ── [4] 게이트A 답변 — 미확정 항목을 합성해서 적용 확인 ──────────
    #    픽스처에는 미확정이 없다. "편집 가능할 때 제대로 박히는지" 는
    #    그래서 합성으로만 볼 수 있다.
    print("\n[4] 게이트A 답변 적용 (합성한 미확정 항목)")
    doc2 = copy.deepcopy(BASE_DOC)
    ex_did = it_did = cp_did = None
    for r in doc2.get("results", []):
        for f in r.get("hitl_flags") or []:
            if f.get("type") == "exclusion_radius_missing" and ex_did is None:
                ex_did = r["dataset_id"]
                f["confirmed"] = False
                f.pop("confirmed_by_human", None)
                role = r["roles"][f.get("role_index", 0)]
                role["confirmed"] = False
                role["need_review"] = True
        for o in r.get("cleaning_ops") or []:
            if o.get("op_id") == "filter_by_code_prefix" and cp_did is None:
                cp_did = r["dataset_id"]
                o["params"]["prefix_confirmed"] = False
    # 의도 미확정은 픽스처에 없다 → 배제와 겹치지 않는 데이터셋에 하나 붙인다.
    for r in doc2.get("results", []):
        if r["dataset_id"] not in (ex_did, cp_did):
            it_did = r["dataset_id"]
            r.setdefault("hitl_flags", []).append(
                {"type": "data_intent_unclear", "message": "합성(검증용)"}
            )
            break
    make_run(doc2, tmp, "r_syn")
    q2 = R._questions_audit("r_syn", DOMAIN)
    editable = [(q["kind"], q["dataset_id"]) for q in q2 if q["editable"]]
    chk("합성한 3건이 편집 가능", len(editable) == 3, editable)

    if it_did:
        err(
            "가점인데 weight 없음 400",
            lambda: R._apply_audit(
                "r_syn", DOMAIN, q2, {"intents": [{"dataset_id": it_did, "choice": 1}]}
            ),
            "숫자여야",
        )
        err(
            "가점 weight 0 400",
            lambda: R._apply_audit(
                "r_syn",
                DOMAIN,
                q2,
                {"intents": [{"dataset_id": it_did, "choice": 1, "weight": 0}]},
            ),
            "크기가 0",
        )
    if ex_did:
        ex_q = next(q for q in q2 if q["kind"] == "exclusion" and q["editable"])
        err(
            "반경 범위 400",
            lambda: R._apply_audit(
                "r_syn",
                DOMAIN,
                q2,
                {
                    "exclusions": [
                        {
                            "dataset_id": ex_did,
                            "role_index": ex_q["role_index"],
                            "radius_m": 9999,
                        }
                    ]
                },
            ),
            "범위는 1~5000",
        )
    if cp_did:
        cp_q = next(q for q in q2 if q["kind"] == "code_prefix" and q["editable"])
        err(
            "prefix 빈값 400",
            lambda: R._apply_audit(
                "r_syn",
                DOMAIN,
                q2,
                {
                    "code_prefixes": [
                        {
                            "dataset_id": cp_did,
                            "op_index": cp_q["op_index"],
                            "prefix": "  ",
                        }
                    ]
                },
            ),
            "prefix 가 비어",
        )

    answer = {"run_id": "r_syn"}
    if ex_did:
        answer["exclusions"] = [
            {"dataset_id": ex_did, "role_index": ex_q["role_index"], "radius_m": 25}
        ]
    if it_did:
        answer["intents"] = [{"dataset_id": it_did, "choice": 2, "weight": 0.4}]
    if cp_did:
        answer["code_prefixes"] = [
            {
                "dataset_id": cp_did,
                "op_index": cp_q["op_index"],
                "prefix": cp_q["prefix"],
            }
        ]
    R._apply_audit("r_syn", DOMAIN, q2, answer)
    after = json.loads(
        (Path(tmp) / "r_syn" / "step1" / f"{PRE}_audit_result_reviewed.json").read_text(
            encoding="utf-8"
        )
    )
    by = {r["dataset_id"]: r for r in after["results"]}
    if ex_did:
        role = by[ex_did]["roles"][ex_q["role_index"]]
        chk(
            "배제반경 25 로 확정 + 출처 human_confirmed",
            role.get("배제반경_m") == 25
            and role.get("confirmed") is True
            and role.get("source") == "human_confirmed",
            role,
        )
    if it_did:
        chk(
            "의도 = 감점 -0.4",
            by[it_did]["roles"]
            == [
                {
                    "role": "negative_factor",
                    "weight": -0.4,
                    "rationale": "HITL 확정",
                    "confirmed": True,
                }
            ],
            by[it_did]["roles"],
        )
    if cp_did:
        chk(
            "prefix 확정 출처 human",
            by[cp_did]["cleaning_ops"][cp_q["op_index"]]["params"].get(
                "prefix_confirmed_by"
            )
            == "human",
        )

    # ── [5] 게이트B 검증 ───────────────────────────────────────────
    #    질문 모양은 계약 7-5 에 고정돼 있다. 실제 제안 산출물이 없어도
    #    **검증 규칙**은 이 모양만으로 전부 확인된다.
    print("\n[5] 게이트B 검증 규칙")
    Q = [
        {
            "kind": "weight",
            "indicator_id": "07+02",
            "radius_required": True,
            "slider_proposed": 0.75,
            "conflict": None,
        },
        {
            "kind": "weight",
            "indicator_id": "04",
            "radius_required": False,
            "slider_proposed": 0.7,
            "conflict": {
                "geo_dataset": "07",
                "geo_direction": "benefit",
                "val_dataset": "02",
                "val_direction": "cost",
            },
        },
        {
            "kind": "weight",
            "indicator_id": "09",
            "radius_required": True,
            "slider_proposed": -0.4,
            "conflict": None,
        },
    ]
    good = {"radius": {"07+02": 150, "09": 250}, "slider": {"04": 0.5}}
    R._validate_weight(Q, good)
    chk("정상 통과", True)
    err(
        "반경 누락 400",
        lambda: R._validate_weight(
            Q, {"radius": {"07+02": 150}, "slider": {"04": 0.5}}
        ),
        "집계반경이 빠진 지표",
    )
    err(
        "admin 에 반경 400",
        lambda: R._validate_weight(
            Q, {"radius": {"07+02": 150, "09": 250, "04": 300}, "slider": {"04": 0.5}}
        ),
        "행정동 단위 지표에는",
    )
    err(
        "충돌 미확정 400",
        lambda: R._validate_weight(
            Q, {"radius": {"07+02": 150, "09": 250}, "slider": {}}
        ),
        "슬라이더 부호로 확정",
    )
    err(
        "없는 지표 400",
        lambda: R._validate_weight(
            Q, {"radius": {"07+02": 150, "09": 250}, "slider": {"04": 0.5, "zz": 0.1}}
        ),
        "게이트에 없는 지표ID",
    )
    err(
        "반경 범위 400",
        lambda: R._validate_weight(
            Q, {"radius": {"07+02": 0, "09": 250}, "slider": {"04": 0.5}}
        ),
        "범위는 1~5000",
    )
    err(
        "슬라이더 범위 400",
        lambda: R._validate_weight(
            Q, {"radius": {"07+02": 150, "09": 250}, "slider": {"04": 1.5}}
        ),
        "범위는 -1.0~1.0",
    )
    err(
        "절대값 합 0 400",
        lambda: R._validate_weight(
            Q,
            {
                "radius": {"07+02": 150, "09": 250},
                "slider": {"07+02": 0, "04": 0, "09": 0},
            },
        ),
        "절대값 합이 0",
    )
    err(
        "알 수 없는 필드 400",
        lambda: R._validate_weight(Q, {"radius": {}, "slider": {}, "weights": {}}),
        "알 수 없는 필드",
    )

    # ── [6] 답변 → CLI 인자 왕복 ──────────────────────────────────
    print("\n[6] 답변 → CLI 인자 (정본 파서로 되읽기)")
    R._save_answer("r_arg", "weight", good)
    chk(
        "fixture 모드는 인자 없음",
        R._stage_args("r_arg", "fixture", "3-2") == (None, None),
    )
    chk("3-2 가 아니면 인자 없음", R._stage_args("r_arg", "hitl", "2") == (None, None))
    ra, wa = R._stage_args("r_arg", "hitl", "3-2")
    chk("--radius 조립", ra == "07+02=150,09=250", ra)
    chk("--weight 조립", wa == "04=0.5", wa)
    from app.services.run_weight_model import _parse_radius_arg, _parse_weight_arg

    chk(
        "CLI 파서로 되읽으면 같다",
        _parse_radius_arg(ra) == {"07+02": 150, "09": 250}
        and _parse_weight_arg(wa) == {"04": 0.5},
    )

    # ── [7] 배제는 전부 사람이 확인한다 (2026-08-10) ────────────────
    #   캐시·조례 자동 확정을 없앤 뒤의 성질을 고정한다. 하나라도 되살아나면
    #   게이트 화면에서 항목이 조용히 사라진다 — 그게 원래 결함이었다.
    print("\n[7] 배제 전부 재확인 · 캐시 제거")
    from app.services import gam2_audit_judgment_test as A  # noqa: E402

    chk(
        "배제반경 캐시 함수·상수가 없다",
        not hasattr(A, "save_to_exclusion_cache")
        and not hasattr(A, "load_exclusion_cache")
        and "cache_path" not in A._DOMAIN
        and not hasattr(__import__("app.config", fromlist=["x"]), "EXCLUSION_CACHE_PATH"),
    )

    n_hard = sum(
        1
        for r in BASE_DOC.get("results", [])
        for x in r.get("roles") or []
        if x.get("role") == "hard_exclusion"
    )
    doc3 = copy.deepcopy(BASE_DOC)
    n_reset = A.reset_exclusion_confirmations(doc3)
    chk(f"되돌린 배제 {n_hard}건", n_reset == n_hard, n_reset)
    chk(
        "되돌려도 값은 남는다(제안값)",
        all(
            f.get("제안값") is not None
            for r in doc3["results"]
            for f in r.get("hitl_flags", [])
            if f.get("type") == "exclusion_radius_missing"
        ),
    )
    make_run(doc3, tmp, "r_reset")
    q3 = [q for q in R._questions_audit("r_reset", DOMAIN) if q["kind"] == "exclusion"]
    chk(f"되돌린 뒤 배제 {n_hard}건 전부 편집 가능",
        len(q3) == n_hard and all(q["editable"] for q in q3),
        [(q["dataset_id"], q["editable"]) for q in q3])

    # 픽스처(확정 상태)는 통과, 되돌린 것은 STEP2 진입 차단.
    try:
        A.assert_exclusions_confirmed(BASE_DOC, src="픽스처")
        chk("확정된 감리는 STEP2 통과", True)
    except SystemExit as e:
        chk("확정된 감리는 STEP2 통과", False, e)
    try:
        A.assert_exclusions_confirmed(doc3, src="되돌린 사본")
        chk("미확정이면 STEP2 차단", False, "안 멈췄다")
    except SystemExit as e:
        chk("미확정이면 STEP2 차단", "미확정" in str(e), str(e).splitlines()[0])

    # flag 가 없던 배제(06·07)에 답하면 flag 를 만들어 확정한다.
    fl_did = next(
        (
            r["dataset_id"]
            for r in doc3["results"]
            if any(x.get("role") == "hard_exclusion" for x in r.get("roles") or [])
        ),
        None,
    )
    if fl_did:
        fq = next(q for q in q3 if q["dataset_id"] == fl_did)
        R._apply_audit("r_reset", DOMAIN, q3 + [], {
            "exclusions": [{"dataset_id": fl_did, "role_index": fq["role_index"],
                            "radius_m": 40}]})
        saved = json.loads(
            (Path(tmp) / "r_reset" / "step1" / f"{PRE}_audit_result_reviewed.json")
            .read_text(encoding="utf-8")
        )
        role = next(
            r for r in saved["results"] if r["dataset_id"] == fl_did
        )["roles"][fq["role_index"]]
        chk(
            "답변이 role 에 확정으로 박힌다",
            role.get("배제반경_m") == 40
            and role.get("confirmed") is True
            and role.get("source") == "human_confirmed",
            role,
        )

    # `_prepare_dirs` — hitl 만 되돌린다. fixture 를 되돌리면 게이트가 없어 STEP2 가 멈춘다.
    def _rev_conf(rid: str) -> list[bool]:
        p = Path(tmp) / rid / "step1" / f"{PRE}_audit_result_reviewed.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        return [
            x.get("confirmed") is True
            for r in d["results"]
            for x in r.get("roles") or []
            if x.get("role") == "hard_exclusion"
        ]

    R._prepare_dirs("r_pd_fix", DOMAIN, R.MODE_FIXTURE)
    R._prepare_dirs("r_pd_hitl", DOMAIN, R.MODE_HITL)
    chk("fixture 사본은 확정 그대로", all(_rev_conf("r_pd_fix")), _rev_conf("r_pd_fix"))
    chk(
        "hitl 사본은 전부 미확정",
        _rev_conf("r_pd_hitl") and not any(_rev_conf("r_pd_hitl")),
        _rev_conf("r_pd_hitl"),
    )
    chk(
        "원본 픽스처는 안 건드린다",
        json.loads(FIX.read_text(encoding="utf-8")) == BASE_DOC,
    )

    # ── [8] 배제 승격(choice=3) 은 그 자리에서 반경을 받는다 ────────────
    #    예전엔 `intents` 에 실은 `radius_m` 이 **200 인데 조용히 버려졌고**,
    #    `exclusions` 로 우회하면 400 이었다(질문 목록은 답변 전에 고정된다).
    #    그 run 은 답할 자리가 없는 채 STEP2 에서 죽었다.
    print("\n[8] 배제 승격(choice=3) — 반경 입력 자리")

    def _mk_intent(rid: str) -> str:
        """비-배제 데이터셋에 의도 미상 flag 를 심어 intent 질문을 만든다."""
        d = json.loads(json.dumps(BASE_DOC))
        t = next(
            r for r in d["results"]
            if not any(x.get("role") == "hard_exclusion" for x in r.get("roles") or [])
        )
        t.setdefault("hitl_flags", []).append({"type": "data_intent_unclear", "message": "합성"})
        make_run(d, tmp, rid)
        return t["dataset_id"]

    i_did = _mk_intent("r_ch3")
    q8 = R._questions_audit("r_ch3", DOMAIN)
    iq = next(q for q in q8 if q["kind"] == "intent")
    chk(
        "choice=3 만 needs_radius=True",
        [c["needs_radius"] for c in iq["choices"]] == [False, False, True, False, False],
        [(c["value"], c.get("needs_radius")) for c in iq["choices"]],
    )
    chk(
        "승격 대상엔 exclusion 질문이 없다(그래서 여기서 받아야 한다)",
        not [q for q in q8 if q["kind"] == "exclusion" and q["dataset_id"] == i_did],
    )

    def _promote(rid: str, item: dict) -> dict:
        did = _mk_intent(rid)
        R._apply_audit(rid, DOMAIN, R._questions_audit(rid, DOMAIN),
                       {"intents": [dict(item, dataset_id=did)]})
        d = json.loads(
            (Path(tmp) / rid / "step1" / f"{PRE}_audit_result_reviewed.json")
            .read_text(encoding="utf-8")
        )
        return d

    d8 = _promote("r_ch3a", {"choice": 3, "radius_m": 30})
    role8 = next(r for r in d8["results"] if r["dataset_id"] == i_did)["roles"][0]
    chk(
        "radius_m 이 실제로 적용된다",
        role8.get("배제반경_m") == 30
        and role8.get("confirmed") is True
        and role8.get("source") == "human_confirmed",
        role8,
    )
    try:
        A.assert_exclusions_confirmed(d8, src="chk")
        chk("확정했으니 STEP2 통과", True)
    except SystemExit as e:
        chk("확정했으니 STEP2 통과", False, str(e).splitlines()[0])

    d8b = _promote("r_ch3b", {"choice": 3, "radius_m": None})
    role8b = next(r for r in d8b["results"] if r["dataset_id"] == i_did)["roles"][0]
    chk(
        "radius_m=null 도 확정이다(면으로 배제)",
        role8b.get("배제반경_m") is None and role8b.get("confirmed") is True,
        role8b,
    )

    d8c = _promote("r_ch3c", {"choice": 3})
    try:
        A.assert_exclusions_confirmed(d8c, src="chk")
        chk("키 생략은 미확정 유지 → STEP2 차단", False, "안 멈췄다")
    except SystemExit as e:
        chk("키 생략은 미확정 유지 → STEP2 차단", "미확정" in str(e),
            str(e).splitlines()[0])

    _mk_intent("r_ch3d")
    q8d = R._questions_audit("r_ch3d", DOMAIN)
    err(
        "choice 1 에 radius_m 은 400",
        lambda: R._apply_audit("r_ch3d", DOMAIN, q8d, {
            "intents": [{"dataset_id": i_did, "choice": 1, "weight": 0.5, "radius_m": 30}]}),
        "choice 3",
    )
    err(
        "intents 오타 필드는 400",
        lambda: R._apply_audit("r_ch3d", DOMAIN, q8d, {
            "intents": [{"dataset_id": i_did, "choice": 3, "radius_mm": 30}]}),
        "알 수 없는 필드",
    )
    ex_q = next(q for q in q8d if q["kind"] == "exclusion")
    err(
        "exclusions 오타 필드는 400(예전엔 '건너뜀' 으로 읽혔다)",
        lambda: R._apply_audit("r_ch3d", DOMAIN, q8d, {
            "exclusions": [{"dataset_id": ex_q["dataset_id"],
                            "role_index": ex_q["role_index"], "radius_mm": 30}]}),
        "알 수 없는 필드",
    )
    cp_q = next((q for q in q8d if q["kind"] == "code_prefix"), None)
    if cp_q:
        err(
            "code_prefixes 오타 필드는 400",
            lambda: R._apply_audit("r_ch3d", DOMAIN, q8d, {
                "code_prefixes": [{"dataset_id": cp_q["dataset_id"],
                                   "op_index": cp_q["op_index"], "prefixx": "111"}]}),
            "알 수 없는 필드",
        )
finally:
    R.run_dir = _orig_run_dir  # type: ignore[assignment]
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n== {ok}/{ok + fail} 통과 ==")
sys.exit(1 if fail else 0)
