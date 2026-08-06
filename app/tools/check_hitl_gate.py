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

    want_ex = want_it = want_cp = 0
    want_radii: list[int] = []
    for r in BASE_DOC.get("results", []):
        for f in r.get("hitl_flags") or []:
            if f.get("type") == "exclusion_radius_missing":
                want_ex += 1
                role = (r.get("roles") or [])[f.get("role_index", 0)]
                want_radii.append(role.get("배제반경_m"))
            elif f.get("type") == "data_intent_unclear":
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
finally:
    R.run_dir = _orig_run_dir  # type: ignore[assignment]
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n== {ok}/{ok + fail} 통과 ==")
sys.exit(1 if fail else 0)
