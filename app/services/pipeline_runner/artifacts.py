# -*- coding: utf-8 -*-
"""산출물 경로 해석 — 화이트리스트에 있는 것만 내보낸다."""

from __future__ import annotations

import json
from pathlib import Path

from app.config import domain_prefix

from .state import run_dir
from .status import read_status
from .steps import ARTIFACTS, _CLEAN_NAME_RE


# ══════════════════════════════════════════════════════════════════
# 9. 산출물 경로 해석 — 화이트리스트 밖으로 나가지 않는다
# ══════════════════════════════════════════════════════════════════
def artifact_path(run_id: str, name: str) -> Path | None:
    """허용된 이름만 실제 경로로 바꾼다. 없으면 None (라우터가 404).

    `name` 은 **경로로 쓰이지 않는다.** 매핑에 있는 이름이거나 `clean_NN` 형식이며,
    후자는 clean_report.json 에 실제로 있는 dataset_id 만 통과한다.
    """
    doc = read_status(run_id)
    if doc is None:
        return None
    pre = domain_prefix(doc["domain"])
    d = run_dir(run_id)

    if name in ARTIFACTS:
        sub, suffix = ARTIFACTS[name]
        p = d / sub / f"{pre}{suffix}"
        return p if p.is_file() else None

    m = _CLEAN_NAME_RE.match(name)
    if not m:
        return None
    report = d / "step2" / f"{pre}_clean_report.json"
    if not report.is_file():
        return None
    for row in json.loads(report.read_text(encoding="utf-8")).get("results", []):
        if row.get("dataset_id") == m.group(1) and row.get("output"):
            # 파일명만 취해 run 폴더 안에서 다시 만든다 — 기록된 절대경로를 그대로
            # 믿지 않는다(run 폴더를 옮겼거나 다른 run 의 경로일 수 있다).
            p = d / "step2" / Path(str(row["output"]).replace("\\", "/")).name
            return p if p.is_file() else None
    return None

