# -*- coding: utf-8 -*-
"""사용자 업로드 도메인(`datasets/user_input/<도메인>`) 자동 정리.

2026-08-14, 사람 결정 — 「업로드 → 사용 → 삭제」. 원래 설계 의도는 임시 저장이었는데
실제 구현은 디스크에 **영구히** 쌓고 있었다. 매체를 Redis 로 되돌리는 대신
(무TTL 대용량 키는 로그인·SSE 를 OOM 으로 죽인다 — CLAUDE.md 함정표) **자리를 가르고
시한을 준다.**

무엇을 지우나 — **도메인 폴더를 통째로** 지운다(사람 결정 「폴더째 지우는게 맞지」).
`runs/` 정리기가 확장자만 고르는 것과 다르다. 이유가 다르기 때문이다:

  · `runs/` 는 **우리 산출물**이라 「무슨 값이 나왔나」가 남아야 한다 → 무거운 것만 지운다.
  · 여기는 **사용자가 올린 입력**이다. 반쯤 남기면 다음 실행이 **일부 데이터셋으로 완주**
    한다 — 지표가 0 이 아니라 작아질 뿐이라 안 걸린다(원칙 4). 원장(`.upload_ledger.json`)
    만 남아도 「올린 적 있다」가 참인 채 파일은 없는 상태가 된다.

그 run 이 남긴 것은 안 지운다 — 산출물은 `runs/<run_id>/` 에, 후보점·감리는 DB 에 있다.
여기서 사라지는 건 **원본 입력**뿐이고, 같은 분석을 또 하려면 다시 올린다.

시계 — `max(마지막 업로드, 마지막 run 종료)` 로부터 24시간(사람 결정 「너가 권장하는대로」).
  🔴 업로드 시각만 보면 **어제 올려 오늘 게이트에서 답하고 있는 도메인**이 발밑에서
     지워진다. run 종료만 보면 **올리기만 하고 아직 안 돌린** 폴더가 영원히 산다.
     둘 중 늦은 쪽을 쓴다.
  🔴 그리고 진행 중인 run(`queued`·`running`·`awaiting_hitl`)이 하나라도 있으면
     시각과 무관하게 **건너뛴다.** `awaiting_hitl` 은 며칠도 서 있을 수 있다.

⚠ 이 정리기는 **프리셋을 볼 수 없다.** 루트가 `USER_INPUT_ROOT` 라 `datasets/흡연` 은
  경로상 닿지 않는다. 「지우면 안 되는 것을 안 지운다」를 조건이 아니라 **자리**로 보장한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from app.config import USER_INPUT_ROOT
from app.services import pipeline_runner as R

# 진행 중으로 보는 상태. 사본을 두지 않고 `run_pruner` 에서 가져온다 — 한쪽만 늘어나면
# 다른 쪽이 돌고 있는 run 을 건드린다.
from app.services.run_pruner import LIVE_STATUSES

logger = logging.getLogger("uvicorn.error")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError as e:
        raise RuntimeError(f"{name} 이 정수가 아니다: {raw!r}") from e
    if v < 1:
        raise RuntimeError(f"{name} 은 1 이상이어야 한다: {v}")
    return v


def ttl_hours() -> int:
    return _env_int("OMNISITE_USER_INPUT_TTL_HOURS", 24)


def sweep_interval_sec() -> int:
    return _env_int("OMNISITE_USER_INPUT_SWEEP_SEC", 3600)


def sweep_enabled() -> bool:
    return os.environ.get("OMNISITE_USER_INPUT_SWEEP", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _parse_ts(v) -> datetime | None:
    """`status.json` 의 ISO 문자열 → naive 로컬 datetime. 못 읽으면 `None`.

    ⚠ 러너는 `datetime.now().isoformat(timespec="seconds")`(naive 로컬)로 적는다.
       오프셋이 붙은 값이 섞여 들어오면 `tzinfo` 를 떼고 비교한다 — 여기 판정은
       「24시간 지났나」이지 절대시각 정합이 아니다.
    """
    if not isinstance(v, str) or not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt.replace(tzinfo=None)


def _domains() -> list[str]:
    root = Path(str(USER_INPUT_ROOT))
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _last_upload(folder: Path) -> datetime | None:
    """폴더 안 **가장 최근 파일 mtime**. 원장을 안 읽는 이유는 원장이 깨져도
    「최근에 올렸다」는 사실이 사라지면 안 되기 때문이다 — 사라지면 지우는 쪽으로 기운다.
    """
    newest: float | None = None
    for f in folder.rglob("*"):
        try:
            if f.is_file():
                m = f.stat().st_mtime
                newest = m if newest is None else max(newest, m)
        except OSError:
            continue
    if newest is None:
        # 파일이 하나도 없으면 폴더 자체의 시각을 쓴다(빈 폴더도 언젠간 걷어야 한다).
        try:
            newest = folder.stat().st_mtime
        except OSError:
            return None
    return datetime.fromtimestamp(newest)


def _reads_user_input(doc: dict) -> bool:
    """이 run 이 **업로드 루트**를 읽는가. 판별자는 러너 정본(`_domain_root`)을 쓴다.

    🔴 여기서 `mode == "full"` 이라고 직접 적지 않는다. 그 규칙은 러너가 갖고 있고
       (`pipeline_runner._domain_root`), 사본을 두면 한쪽만 바뀌었을 때 **삭제가
       조용히 잘못된 run 을 보호한다.** 비교는 문자열이 아니라 **경로**다.

    ⚠ `mode` 가 **없거나 모르는 값**이면 읽는 것으로 친다. 「모른다」를 「안 읽는다」로
       바꾸면 돌고 있는 run 의 입력을 발밑에서 지운다(원칙 1). 없는 키는 옛 run 의
       모양이고, 모르는 값은 **모드가 늘었는데 여기를 안 고친** 모양이다 —
       `_domain_root` 는 `full` 이 아닌 것을 전부 프리셋으로 돌리므로, 그 기본값에
       기대면 새 업로드 모드가 생기는 순간 조용히 보호가 풀린다.
    """
    mode = doc.get("mode")
    if not isinstance(mode, str) or mode not in R.MODES:
        return True
    # 양쪽 다 **러너의** 루트로 잰다. 한쪽만 이 모듈 것을 쓰면 대조기가 루트를
    # 갈아끼울 때 비교가 조용히 어긋난다 — 그럼 전 항목이 「프리셋」이 된다.
    return R._domain_root(mode) == Path(str(R.USER_INPUT_ROOT))


def _run_facts(domain: str) -> tuple[bool, datetime | None]:
    """`(진행 중인 run 이 있나, 마지막 run 종료 시각)`.

    🔴 `R.read_status()` 를 부르지 않는다. 그건 순수 읽기이긴 하지만 `runs/` 전수를
       도는 자리에서 부르면 산출물 URL 보정까지 매번 돈다 — 여기 필요한 건 세 필드뿐이다.

    🔴 **이름이 같다고 같은 폴더가 아니다**(2026-08-16 실측으로 드러남). 프리셋
       (`fixture`·`hitl`)은 `datasets/<도메인>` 을 읽고 업로드(`full`)만
       `datasets/user_input/<도메인>` 을 읽는다 — `_domain_root` 가 그렇게 가른다.
       예전엔 `domain` 이름만 맞춰서, **닿지도 못하는 프리셋 run 이 업로드 도메인을
       보호**했다. 하필 그 run 이 `awaiting_hitl` 이면 스레드가 없어 `reap_orphans`
       도 못 닫으므로 **영구히** 서 있고, 삭제 API 는 영원히 409, 자동 정리기는
       영원히 `keep` 이다. 안 터지고 **지워지지 않을 뿐**이라 안 걸린다.
       (실제 사건: `r_20260814_008` 재활용 `hitl` 하나가 업로드 `재활용` 을 막았다.)
    """
    live = False
    last: datetime | None = None
    if not R.RUNS_ROOT.is_dir():
        return live, last
    for d in R.RUNS_ROOT.glob("r_*"):
        p = d / "status.json"
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 🔴 못 읽은 run 은 **진행 중으로 친다.** 「모른다」를 「끝났다」로 바꾸면
            #    돌고 있는 run 의 입력을 발밑에서 지운다(원칙 1).
            live = True
            continue
        if doc.get("domain") != domain:
            continue
        if not _reads_user_input(doc):
            # 이 run 의 입력은 프리셋 루트에 있다. 여기서 지우는 폴더와 무관하다.
            continue
        if doc.get("status") in LIVE_STATUSES:
            live = True
            continue
        ts = _parse_ts(doc.get("finished_at")) or _parse_ts(doc.get("started_at"))
        if ts and (last is None or ts > last):
            last = ts
    return live, last


def plan(ttl_h: int, now: datetime | None = None) -> list[dict]:
    """지울 계획. **아무것도 안 지운다.**

    항목: `{domain, action, reason, clock, age_hours, bytes, files}`.
    `action` 이 `keep` 일 때도 `reason` 을 채운다 — 계획이 「지울 것」만 말하면
    왜 안 지우는지를 사람이 다시 캐야 한다.
    """
    now = now or datetime.now()
    cutoff = timedelta(hours=ttl_h)
    root = Path(str(USER_INPUT_ROOT))
    out: list[dict] = []

    for domain in _domains():
        folder = root / domain
        files = [f for f in folder.rglob("*") if f.is_file()]
        total = 0
        for f in files:
            try:
                total += f.stat().st_size
            except OSError:
                pass
        item = {"domain": domain, "action": "keep", "reason": "",
                "clock": None, "age_hours": None,
                "files": len(files), "bytes": total}

        live, last_run = _run_facts(domain)
        if live:
            item["reason"] = "진행 중인 run 이 있다(queued·running·awaiting_hitl)"
            out.append(item)
            continue

        up = _last_upload(folder)
        clock = max([t for t in (up, last_run) if t is not None], default=None)
        if clock is None:
            item["reason"] = "시각을 읽을 수 없다 — 판단하지 않는다"
            out.append(item)
            continue

        item["clock"] = clock.isoformat(timespec="seconds")
        age = now - clock
        item["age_hours"] = round(age.total_seconds() / 3600, 2)
        if age < cutoff:
            item["reason"] = f"마지막 사용 후 {item['age_hours']}시간 (기준 {ttl_h})"
        else:
            item["action"] = "prune"
            item["reason"] = (
                f"마지막 사용 후 {item['age_hours']}시간 ≥ {ttl_h} "
                f"(기준 시각 출처: "
                f"{'run 종료' if clock is last_run else '업로드'})"
            )
        out.append(item)
    return out


def drop_vector_chunks(domain: str) -> int:
    """그 도메인 조례에서 나온 벡터 청크를 지운다. 지운 행 수.

    🔴 파일만 지우고 이걸 안 지우면 **근거만 남는다.** 폴더는 없는데 그 조문이
       계속 검색돼 토론에 인용된다 — 안 터지고 값만 틀린다.

    ⚠ 2026-08-24 이전엔 위험이 하나 더 있었다: 콜렉션이 `statutes_collection`
      하나뿐이고 검색 필터가 `facility_type`(도메인이 아니다)이라, 지워진 도메인의
      조문이 **같은 시설을 쓰는 남의 실행**에 섞였다. 콜렉션을 도메인마다 나눈
      뒤로 그 갈래는 사라졌다 — 남은 것은 「그 도메인 자신의 유령 근거」뿐이다.
      지우는 범위도 이제 콜렉션 하나로 **구조적으로** 묶인다.
    """
    from app.core.sim_ai.vector_db import get_vector_db

    return get_vector_db().delete_statute_chunks(domain=domain)


def apply(items: list[dict]) -> dict:
    """계획대로 **폴더째** 지운다. 실패한 도메인은 건너뛰고 사유를 로그에 남긴다.

    ⚠ Redis 업로드 색인(`omnisite:upload:<도메인>:data`)은 **여기서 안 지운다.**
      그 키에는 30일 TTL 이 있어 `volatile-lru` 가 걷어간다 — 그러라고 준 TTL 이다.
      여기에 async redis 의존을 끌어오면 이 정리기가 Redis 없이는 못 돌게 된다.
      즉시 지우는 경로는 `DELETE /upload/domains/{domain}`(초기화 버튼) 쪽에 있다.
    """
    root = Path(str(USER_INPUT_ROOT))
    removed, removed_bytes, failed = [], 0, []
    chunks = 0
    for it in items:
        if it["action"] != "prune":
            continue
        folder = root / it["domain"]
        # 🔴 청크를 **먼저** 지운다. 폴더를 먼저 지우고 여기서 터지면 「파일은 없는데
        #    검색에는 잡히는」 상태가 남고, 다음 회차엔 폴더가 없어 계획에도 안 뜬다.
        try:
            chunks += drop_vector_chunks(it["domain"])
        except Exception as e:
            failed.append({"domain": it["domain"], "error": f"벡터 청크 삭제 실패: {e}"})
            logger.warning("[user_input] %s 청크 삭제 실패 — 폴더도 남긴다: %s",
                           it["domain"], e)
            continue
        try:
            shutil.rmtree(folder)
        except OSError as e:
            failed.append({"domain": it["domain"], "error": str(e)})
            logger.warning("[user_input] %s 삭제 실패: %s", folder, e)
            continue
        removed.append(it["domain"])
        removed_bytes += it["bytes"]
        logger.info("[user_input] 삭제: %s (%.1f MB · %d파일 · %s)",
                    folder, it["bytes"] / 2**20, it["files"], it["reason"])
    return {"domains": removed, "bytes": removed_bytes,
            "vector_chunks": chunks, "failed": failed}


def sweep(*, ttl_h: int | None = None, dry_run: bool = True) -> dict:
    """계획 + (선택) 실행. 주기 훅과 CLI·대조기가 **같은 함수**를 쓴다."""
    ttl_h = ttl_hours() if ttl_h is None else ttl_h
    items = plan(ttl_h)
    result = {"ttl_hours": ttl_h, "items": items, "applied": None}
    if not dry_run:
        result["applied"] = apply(items)
    return result


async def sweep_loop() -> None:
    """서버가 사는 동안 주기적으로 돈다. 🔴 여기서 나는 어떤 실패도 서버를 안 죽인다.

    ⚠ `run_pruner` 는 **부팅 1회**인데 이건 주기다. 판정식이 다르기 때문이다 —
      저쪽 답(`started_at < _SERVER_BOOT`)은 부팅 시점에 이미 고정이지만, 여기 답은
      시간이 지나면 바뀐다. 부팅 때만 돌면 오래 떠 있는 서버에서 영원히 안 지워진다.
    """
    interval = sweep_interval_sec()
    while True:
        try:
            res = await asyncio.to_thread(sweep, dry_run=False)
            a = res["applied"] or {}
            if a.get("domains"):
                logger.info("[user_input] 정리: %s · %.1f MB 회수 (TTL %d시간)",
                            ", ".join(a["domains"]), a["bytes"] / 2**20,
                            res["ttl_hours"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[user_input] 정리 실패 — 아무것도 지우지 않았다.")
        await asyncio.sleep(interval)
