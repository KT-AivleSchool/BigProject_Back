# -*- coding: utf-8 -*-
"""`runs/` 무거운 산출물 정리 (2026-08-11, 사람 결정: 최근 5개 보관 · 부팅 시 자동).

무엇을 지우나 — **폴더를 통째로 지우지 않는다.** `.gpkg`·`.parquet` 만 지운다.
실측(2026-08-11, run 8개 309.2 MB):

    .gpkg     72개  275.7 MB     ← 지운다
    .parquet  24개   23.7 MB     ← 지운다
    .json     87개    3.7 MB  ┐
    .html      8개    3.6 MB  │  남긴다 (합 9.9 MB · 전체의 3.2%)
    .geojson  16개    2.3 MB  │  status·run.log·params·topN·exclusion·clean_report
    .log       8개    0.3 MB  │  — run 이 **무엇을 했는지**는 여기 다 있다
    .csv       8개    0.0 MB  ┘

즉 96.8% 를 회수하면서 「그 run 이 무슨 값을 냈나」는 그대로 읽힌다. 폴더째 지우면
`status.json` 이 사라져 프런트 폴링이 404 가 되고, 계약 3절이 옛 run 에 대해 거짓이 된다.

무엇을 안 지우나 — **셋 다 만족해야 지운다.**
  ① 최근 N개(기본 5)에 안 든다
  ② 상태가 `queued`·`running`·`awaiting_hitl` 이 아니다 — 돌고 있거나 사람을 기다리는
     run 을 건드리면 그 run 이 죽는다
  ③ `booth_candidates.run_id` 가 참조하지 않는다 — DB 에 살아 있는 후보점의 **출처**다.
     지금은 토론이 DB 만 읽지만, POI 라벨을 `runs/<id>/step2/clean_report.json` 에서
     읽게 되면(계획된 (나)안) 이 폴더가 곧 토론의 근거가 된다. 그때 이 조건이 없으면
     **이미 한 토론의 근거가 조용히 사라진다.**

🔴 ③ 을 못 확인하면(DB 접속 실패 등) **아무것도 안 지운다.** 보호 목록 없이 지우는 건
   보호가 없는 것과 같다. 안 지우는 쪽은 디스크만 쓰지만, 잘못 지우는 쪽은 되돌릴 수 없다.

지운 사실은 `status.json` 의 `pruned` 에 남긴다 — 산출물이 없어졌는데 왜 없어졌는지가
어디에도 없으면 「파이프라인이 안 만들었다」로 읽힌다(원칙 4).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from app.services import pipeline_runner as R

logger = logging.getLogger("uvicorn.error")

# 지우는 확장자. 「크고 다시 만들 수 있는 것」만이다 — 파이프라인을 다시 돌리면 나온다.
PRUNE_SUFFIXES = (".gpkg", ".parquet")

# 정리에서 제외할 상태. 돌고 있거나 사람을 기다리는 run 이다.
LIVE_STATUSES = ("queued", "running", "awaiting_hitl")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError as e:
        # 조용히 기본값으로 넘어가면 "5개 보관인 줄 알았는데 아니었다" 가 된다(원칙 1).
        raise RuntimeError(f"{name} 이 정수가 아니다: {raw!r}") from e
    if v < 1:
        raise RuntimeError(f"{name} 은 1 이상이어야 한다: {v} (0 이면 전부 지운다는 뜻이 된다)")
    return v


def keep_count() -> int:
    return _env_int("OMNISITE_RUNS_KEEP", 5)


def prune_on_boot() -> bool:
    """부팅 시 자동 정리 여부. 기본 켜짐(사람 결정 2026-08-11)."""
    return os.environ.get("OMNISITE_RUNS_PRUNE_ON_BOOT", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _all_runs() -> list[str]:
    """run_id 오름차순. `r_YYYYMMDD_NNN` 은 자릿수가 고정이라 사전순 = 시간순이다."""
    if not R.RUNS_ROOT.is_dir():
        return []
    return sorted(p.name for p in R.RUNS_ROOT.glob("r_*") if p.is_dir())


async def referenced_run_ids() -> set[str]:
    """DB 에 살아 있는 후보점이 출처로 가리키는 run_id.

    🔴 실패하면 `raise` 한다. 호출자가 「빈 집합 = 보호할 게 없다」로 읽으면
       DB 가 잠깐 안 뜬 순간에 전부 지워진다.
    """
    from sqlalchemy import select

    from app.db.models.simulation import Parcel
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(Parcel.run_id).distinct())
        return {r for (r,) in rows if r}


def plan(keep: int, protected: set[str]) -> list[dict]:
    """지울 계획. **아무것도 안 지운다.**

    돌려주는 항목: `{run_id, action, reason, files, bytes}`.
    `action` 은 `prune` 또는 `keep` 이고, `keep` 이면 `reason` 이 왜 남기는지다 —
    계획이 「지울 것」만 말하면 왜 안 지우는지를 사람이 다시 캐야 한다.
    """
    runs = _all_runs()
    recent = set(runs[-keep:]) if keep else set()
    out: list[dict] = []

    for run_id in runs:
        d = R.run_dir(run_id)
        targets = [f for f in d.rglob("*")
                   if f.is_file() and f.suffix.lower() in PRUNE_SUFFIXES]
        total = sum(f.stat().st_size for f in targets)

        if run_id in recent:
            reason = f"최근 {keep}개"
        elif run_id in protected:
            reason = "booth_candidates 가 출처로 참조 중"
        else:
            doc = R.read_status(run_id) or {}
            st = doc.get("status")
            if st in LIVE_STATUSES:
                reason = f"진행 중({st})"
            elif not targets:
                reason = "지울 파일 없음"
            else:
                out.append({"run_id": run_id, "action": "prune", "reason": "",
                            "files": targets, "bytes": total})
                continue
        out.append({"run_id": run_id, "action": "keep", "reason": reason,
                    "files": targets, "bytes": total})
    return out


def apply(items: list[dict]) -> dict:
    """계획 중 `prune` 항목을 실제로 지우고 `status.json` 에 남긴다."""
    removed_files = removed_bytes = 0
    for it in (i for i in items if i["action"] == "prune"):
        run_id = it["run_id"]
        base = R.run_dir(run_id)
        gone: list[dict] = []
        for f in it["files"]:
            size = f.stat().st_size
            f.unlink()
            gone.append({"path": f.relative_to(base).as_posix(), "bytes": size})
            removed_files += 1
            removed_bytes += size

        doc = R.read_status(run_id)
        if doc is None:
            # status 가 없으면 지운 사실을 적을 데가 없다. 파일은 이미 지웠으므로
            # **로그로라도** 남긴다 — 조용히 넘어가면 아무 데도 안 남는다.
            logger.warning("[prune] %s 에 status.json 이 없어 기록을 남기지 못했다 "
                           "(파일 %d개 삭제됨)", run_id, len(gone))
            continue
        prev = doc.get("pruned") or {}
        doc["pruned"] = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "policy": f"keep_recent:{keep_count()}",
            "removed": (prev.get("removed") or []) + gone,
        }
        # 지운 파일을 가리키던 산출물 URL 을 null 로 되돌린다. 안 하면 status 는
        # URL 을 주는데 엔드포인트는 404 다 — status 가 거짓말을 한다(원칙 4).
        R._refresh_artifacts(doc)
        R._write_status(run_id, doc)

    return {"runs": sum(1 for i in items if i["action"] == "prune"),
            "files": removed_files, "bytes": removed_bytes}


async def prune_now(*, keep: int | None = None, dry_run: bool = True) -> dict:
    """계획 + (선택) 실행. 부팅 훅과 CLI 가 **같은 함수**를 쓴다."""
    keep = keep_count() if keep is None else keep
    protected = await referenced_run_ids()
    items = plan(keep, protected)
    result = {"keep": keep, "protected": sorted(protected), "items": items,
              "applied": None}
    if not dry_run:
        result["applied"] = apply(items)
    return result


async def prune_on_boot_hook() -> None:
    """부팅 1회. 🔴 여기서 나는 어떤 실패도 서버 기동을 막지 않는다.

    안 지우는 건 디스크만 쓰지만, 기동이 막히면 프런트가 통째로 멈춘다.
    대신 **왜 못 했는지를 로그에 남긴다** — 조용히 넘어가지 않는다(원칙 1).
    """
    if not prune_on_boot():
        logger.info("[prune] OMNISITE_RUNS_PRUNE_ON_BOOT 가 꺼져 있어 건너뛴다.")
        return
    try:
        res = await prune_now(dry_run=False)
    except Exception:
        logger.exception("[prune] runs 정리 실패 — 아무것도 지우지 않았다.")
        return
    a = res["applied"] or {}
    logger.info("[prune] runs 정리: %d개 run · 파일 %d개 · %.1f MB 회수 "
                "(보관 최근 %d개 · 참조 보호 %d개)",
                a.get("runs", 0), a.get("files", 0), a.get("bytes", 0) / 2**20,
                res["keep"], len(res["protected"]))
