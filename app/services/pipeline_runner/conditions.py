# -*- coding: utf-8 -*-
"""**실행 조건의 출처** — 픽스처·`full` 선언값·`params.json`.

🔴 이 파일에는 도메인 값이 하나도 없다. `_FULL_COND` 는 도메인 값이 아니라
   **계산 방식**이다(alpha·decay·scale·spacing) — 그래서 원칙 2 에 안 걸린다.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import DOMAIN_ROOT, domain_prefix

from .state import MODE_FULL, RunRequestError, run_dir


# ══════════════════════════════════════════════════════════════════
# 4. 픽스처 — 실행 조건의 출처
# ══════════════════════════════════════════════════════════════════
def _fixture_dir(domain: str) -> Path:
    # 🔴 여기는 **항상 프리셋 루트**다(`_domain_root` 를 쓰지 않는다). 기준선은 사용자
    #    업로드물이 아니라 저장소가 들고 있는 회귀 기준이고, full 은 픽스처를 안 읽는다.
    return Path(str(DOMAIN_ROOT)) / f"{domain}_FIX"


def _load_fixture(domain: str) -> tuple[dict, Path]:
    """기준값.json 과 reviewed.json 을 확인하고 돌려준다.

    없으면 여기서 멈춘다. 픽스처 없이 'fixture 모드'를 도는 건 이름이 거짓말이다.
    """
    fd = _fixture_dir(domain)
    base = fd / "기준값.json"
    rev = fd / "reviewed.json"
    for p in (base, rev):
        if not p.is_file():
            raise RunRequestError(f"픽스처가 없습니다: {p}")
    return json.loads(base.read_text(encoding="utf-8")), rev


# ── full 모드의 실행 조건 ─────────────────────────────────────────────
# 🔴 이 값들이 **하드코딩 금지(원칙 2)에 걸리지 않는 이유**를 여기 남긴다.
#    원칙 2 가 막는 것은 **도메인 값**(시설명·지목·배제반경·지역코드)이다.
#    아래 넷은 도메인이 아니라 **계산 방식**이다 — 거리감쇠 함수·정규화 스케일·
#    후보점 격자 간격. 용산 흡연부스든 성동 재활용정거장이든 같은 값을 쓴다.
#    도메인마다 갈려야 하는 값(반경·가중치)은 여기 없다. 그건 게이트에서 사람이 준다.
#
#    그래도 **기본값을 조용히 쓰지는 않는다.** CLI 기본값은 `scale=minmax`·
#    `decay=null` 이라 아래와 다르고, 그 차이 하나만으로 Top-N 이 통째로 갈린다
#    (2026-08-10 실측). 즉 "안 주면 알아서 되겠지" 가 성립하지 않는 자리다.
#    그래서 러너가 **명시적으로 선언하고**, 그 선언을 `runs/<id>/params.json` 에
#    적어 산출물에서 되짚을 수 있게 한다(원칙 4).
#    출처: `datasets/흡연_FIX/기준값.json` 의 `조건` (2026-08-03 고정 기준선).
_FULL_COND: dict = {
    "alpha": 0.3,
    "decay": {"func": "gaussian", "sigma_ratio": 1 / 3},
    "scale": "log",
    "spacing": 20,
}

# STEP4 Top-N 기본 개수. `gam4_site_select.py --topn` 의 기본값과 같다.
TOPN_DEFAULT = 20
TOPN_MAX = 200


def _full_conditions(domain: str, topn: int) -> dict:
    """full 모드의 `base` — 픽스처 대신 **선언된 조건**을 쓴다.

    `STEP3_가중치` 는 **일부러 비운다.** full 모드의 반경은 게이트B 에서만 온다 —
    빈 dict 를 두면 `_radius_arg` 가 멈추므로, 게이트를 안 거치고 3-2 에 닿는
    경로가 생기면 조용히 도는 대신 터진다(원칙 1).
    """
    return {
        "조건": dict(
            _FULL_COND,
            candidates=f"{domain_prefix(domain)}_후보_지적도필지.gpkg",
            topn=topn,
        ),
        "STEP3_가중치": {},
    }


def _params_path(run_id: str) -> Path:
    return run_dir(run_id) / "params.json"


def _write_params(run_id: str, params: dict) -> None:
    """이 run 의 요청 파라미터. status.json 스키마(계약 3절)를 늘리지 않는다.

    게이트에서 스레드가 끝났다가 답변 POST 로 **새 스레드가 이어받으므로**
    `user_input`·`topn` 은 메모리에 둘 수 없다. 상태는 전부 디스크에 있다.
    """
    _params_path(run_id).write_text(
        json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_params(run_id: str) -> dict:
    p = _params_path(run_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def _load_conditions(domain: str, mode: str, run_id: str | None = None) -> dict:
    """실행 조건(`base`)을 모드에 맞는 출처에서 가져온다.

    fixture·hitl → `<도메인>_FIX/기준값.json`  (없으면 400)
    full         → `_full_conditions`          (픽스처를 요구하지 않는다)
    """
    if mode != MODE_FULL:
        return _load_fixture(domain)[0]
    topn = TOPN_DEFAULT
    if run_id:
        topn = _read_params(run_id).get("topn") or TOPN_DEFAULT
    return _full_conditions(domain, topn)


def _radius_arg(base: dict) -> str:
    """`--radius` 문자열을 픽스처의 지표별 radius_m 에서 조립한다.

    radius_m 이 null 인 지표(= admin, 반경 개념이 없다)는 뺀다.
    비-admin 지표가 빠지면 run_weight_model 이 [R] HITL 로 내려가 stdin 이 없어
    EOFError 로 죽는다 — 조용히 넘어가지 않으므로 그대로 둔다.
    """
    parts = [f"{iid}={v['radius_m']}"
             for iid, v in (base.get("STEP3_가중치") or {}).items()
             if v.get("radius_m") is not None]
    if not parts:
        raise RunRequestError(
            "실행 조건에 radius_m 이 하나도 없습니다. "
            "(fixture·hitl 이면 기준값.json 의 STEP3_가중치, "
            "full 이면 게이트B 답변이 반경의 유일한 출처다)")
    return ",".join(parts)

