# -*- coding: utf-8 -*-
"""감리 하네스의 **바닥** — 도메인 컨텍스트와 산출물 경로.

패키지 안 어느 것도 import 하지 않는다(그래야 사이클이 안 생긴다).
`_DOMAIN` 은 dict 를 **제자리에서 갱신**한다(`update`) — 이름을 다시 묶지 않으므로
`from .state import _DOMAIN` 로 들고 간 서브모듈도 같은 객체를 본다.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

# 프로젝트 루트를 sys.path 에 추가 → `python app\services\...` 로 직접 실행해도
#   `app.xxx` 절대 임포트가 된다. (`python -m app.services.…` 는 원래 되지만
#   실행 방식마다 다르게 동작하면 매번 걸린다 — STEP3·4 스크립트와 동일한 보정)
import os as _os
import sys as _sys

_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from app import config
from app.services.gam2_audit_ops_catalog import describe_all
from app.config import STEP1_OUTPUT_DIR

# 🔴 배제반경 캐시는 2026-08-10 제거했다(사람 지시). 예전엔 사람이 한 번 확정한
#    시설유형→반경을 `<prefix>_exclusion_radius_cache.json` 에 적어두고 다음 실행에서
#    **묻지 않고 채웠다.** 자동 진행이 필요한 경우는 이제 `mode:"full"` 이 맡는다 —
#    HITL 은 "사람이 전부 본다"가 뜻의 전부여야 한다. 캐시가 남아 있으면 화면에
#    안 뜨는 항목이 생기고, 그건 사람이 확인한 것처럼 기록된다(원칙 4).


# ── 도메인 컨텍스트 (실행 시 set_domain 으로 채움; 전까지는 기존 기본값) ──
_DOMAIN = {
    "prefix": "",
    "data": None,
    "law": None,
    "fixture": None,
    "profiles": None,
}


def set_domain(domain_dir: str) -> None:
    """도메인 루트 폴더로 경로·프리픽스를 확정. 모든 모드 시작 시 1회 호출."""
    p = config.domain_paths(domain_dir)
    _DOMAIN.update(
        prefix=p["prefix"],
        data=p["data"],
        law=p["law"],
        fixture=p["fixture"],
        profiles=p["profiles"],
    )


def _out_path(name: str) -> str:
    """산출물 경로에 도메인 프리픽스. 예: name='audit_result.json' → EV_audit_result.json"""
    pre = f"{_DOMAIN['prefix']}_" if _DOMAIN["prefix"] else ""
    return os.path.join(STEP1_OUTPUT_DIR, f"{pre}{name}")
