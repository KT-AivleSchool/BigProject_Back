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

무엇을 안 지우나 — **둘 다 만족해야 지운다.**
  ① 최근 N개(기본 100)에 안 든다
  ② 상태가 `queued`·`running`·`awaiting_hitl` 이 아니다 — 돌고 있거나 사람을 기다리는
     run 을 건드리면 그 run 이 죽는다

🔴 **조건 ③ 을 없앴다 (2026-08-11, 사람 결정). 왜 없앴는지를 적어둔다 — 안 적으면
   다음 사람이 「빠진 보호」로 읽고 되살린다.**

   있던 조건은 「`booth_candidates.run_id` 가 참조하면 안 지운다」였고, 근거는
   "DB 에 살아 있는 후보점의 출처이므로 그 폴더가 곧 토론의 근거다" 였다.
   근거 자체는 지금도 참이다 — `poi_context` 는 `runs/<id>/step2/*.gpkg` 를 읽는다.
   틀린 건 **이 조건이 상한을 안 갖는다**는 점이다:

     · 같은 날 fixture 도 적재하게 됐다(`_proc_load_topn` 주석). 적재한 run 은 전부
       참조되므로 ③ 이 살아 있으면 **적재한 run 은 하나도 안 지워진다** — 정리기가
       사실상 꺼진다.
     · 반대로 **적재도 토론도 안 한 버려진 run**(가치가 가장 낮다)은 ③ 에 안 걸려
       먼저 지워진다. 실패 방향이 뒤집혀 있다.
     · 「토론을 한 번 했으니 이 폴더는 계속 필요하다」도 성립하지 않는다. 실측하면
       `run_id='정본'` 의 후보점 20개 중 실제로 토론된 건 3개(rank 6·2·1)이고,
       그 셋은 **하루 넘게 벌어진 다른 시점**에 각각 골라졌다(08-10 05:56 · 07:17 ·
       08-11 02:41). 「토론했다」와 「더 안 쓴다」는 서로 다른 문장이다.

   그래서 상한이 있는 조건 하나(`keep`)만 남긴다. 디스크는 `run 1개 39MB × keep`
   으로 묶이고, 값이 필요하면 `keep` 을 올린다 — 조절 지점이 하나다.
   토론이 이미 쓴 근거는 폴더가 아니라 `result_json.basis` 에 **본문으로** 박혀 있다
   (`candidate_context.basis_snapshot`). 참조는 대상이 사라지면 같이 죽지만 본문은 남는다.
   ⚠ 아직 안 한 토론의 POI 는 gpkg 가 지워지면 빈다 — `poi_context` 가 그 자리에
   「정리(prune)됐거나 만들어지지 않았다」를 `skipped` 로 남긴다. 조용히 빠지지 않는다.

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
    # 🔴 5 → 100 (2026-08-11, 사람 결정). 조건 ③ 을 뺀 대신 여기 하나로 받는다.
    #    상한은 `run 1개 39MB × 100 ≒ 3.9GB` 다 — 작지 않다. 줄이려면 이 값을
    #    내리는 게 유일한 손잡이다(`OMNISITE_RUNS_KEEP`).
    return _env_int("OMNISITE_RUNS_KEEP", 100)


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


def plan(keep: int) -> list[dict]:
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


def apply(items: list[dict], *, keep: int) -> dict:
    """계획 중 `prune` 항목을 실제로 지우고 `status.json` 에 남긴다.

    🔴 `keep` 은 **계획을 만든 그 값**을 받는다. 예전엔 여기서 `keep_count()` 를 다시
       읽었는데, `--keep 5` 로 계획을 세워도 기록엔 환경 기본값(100)이 적혔다 —
       무엇을 기준으로 지웠는지가 산출물에서 거짓이 된다(원칙 4).
    """
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
            "policy": f"keep_recent:{keep}",
            "removed": (prev.get("removed") or []) + gone,
        }
        # 지운 파일을 가리키던 산출물 URL 을 null 로 되돌린다. 안 하면 status 는
        # URL 을 주는데 엔드포인트는 404 다 — status 가 거짓말을 한다(원칙 4).
        R._refresh_artifacts(doc)
        R._write_status(run_id, doc)

    return {"runs": sum(1 for i in items if i["action"] == "prune"),
            "files": removed_files, "bytes": removed_bytes}


async def prune_now(*, keep: int | None = None, dry_run: bool = True) -> dict:
    """계획 + (선택) 실행. 부팅 훅과 CLI 가 **같은 함수**를 쓴다.

    🔴 DB 를 안 본다 (2026-08-11). 예전엔 `booth_candidates.run_id` 를 조회해
       보호 목록을 만들었다 — 그 조건을 없앤 경위는 모듈 첫머리에 있다.
       부작용으로 **DB 가 안 떠 있어도 정리가 돈다.** async 는 호출부(lifespan)와
       모양을 맞추려 남긴다.
    """
    keep = keep_count() if keep is None else keep
    items = plan(keep)
    result = {"keep": keep, "items": items, "applied": None}
    if not dry_run:
        result["applied"] = apply(items, keep=keep)
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
                "(보관 최근 %d개)",
                a.get("runs", 0), a.get("files", 0), a.get("bytes", 0) / 2**20,
                res["keep"])
