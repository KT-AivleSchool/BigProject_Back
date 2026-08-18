# -*- coding: utf-8 -*-
"""`status.json` — 쓰기·읽기·고아 정리, 그리고 상태 문서 조립.

🔴 `_write_status` 는 쓰기만 `_IO_LOCK` 이고 `os.replace` 를 8회 재시도한다.
   Windows `open()` 은 `FILE_SHARE_DELETE` 를 안 줘서 **읽는 중에도** `os.replace`
   가 `WinError 5` 로 터진다. 끝내 안 되면 `raise` 한다 — 조용히 넘기지 않는다.
🔴 `read_status` 는 **순수 읽기**다(2026-08-09). 전수 스캔은 `reap_orphans()` 로
   떼어내 부팅 1회로 옮겼다 — 판정식이 `started_at < _SERVER_BOOT` 라 답은 부팅
   시점에 이미 고정이다.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime

from app.config import domain_prefix, settings
from app.services import run_records

from .conditions import _read_params
from .state import (
    MODE_FIXTURE,
    RUNS_ROOT,
    _IO_LOCK,
    _REPLACE_BACKOFF_S,
    _REPLACE_RETRIES,
    _SERVER_BOOT,
    _log,
    _now_iso,
    _status_path,
    run_dir,
)
from .steps import ARTIFACTS, step_labels


def _write_status(run_id: str, doc: dict) -> None:
    """원자적 기록. 폴링과 겹쳐도 반쯤 쓰인 JSON 을 읽지 않게 한다.

    🔴 임시 파일 이름에 스레드 id 를 넣고 락으로 감싼다. 두 스레드가 같은 `.tmp` 를
       쓰면 한쪽이 아직 쥐고 있는 파일을 다른 쪽이 replace 하려다 Windows 에서
       PermissionError 로 터진다(WinError 32). 실행 스레드와 폴링이 겹치는 건
       예외가 아니라 **정상 동작**이다.

    🔴 그래도 os.replace 는 터진다 — 락은 **쓰는 쪽끼리만** 직렬화한다(2026-08-08 실측).
       읽는 쪽은 이 락 밖이고, 파이썬 `open()` 은 `FILE_SHARE_DELETE` 를 안 준다 →
       **누가 읽고 있는 동안엔 replace 가 거부된다.** 예상했던 WinError 32(사용 중)가
       아니라 **WinError 5**(액세스 거부)로 온다 — 번호가 달라 안 걸렸다.
       (2026-08-09 — 경합의 주범이던 `read_status` 안의 전수 스캔은 제거했다. 폴러가
        자기 run 하나만 읽으므로 겹칠 확률이 크게 준다. 재시도는 그대로 남긴다 —
        확률이 준 것이지 0이 된 게 아니다.)
       `r_20260808_001` 이 이걸로 시작 같은 초에 죽었다. 읽기는 순간이므로 짧게
       재시도하면 넘어간다. 끝내 안 되면 **raise 한다** — 조용히 넘기면 status 가
       옛 값인 채로 남아 거짓말을 한다(원칙 1·4).
    """
    p = _status_path(run_id)
    tmp = p.with_suffix(f".json.{threading.get_ident()}.tmp")
    with _IO_LOCK:
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(_REPLACE_RETRIES):
            try:
                os.replace(tmp, p)
                return
            except PermissionError:
                if attempt == _REPLACE_RETRIES - 1:
                    raise
                time.sleep(_REPLACE_BACKOFF_S)


def read_status(run_id: str) -> dict | None:
    """계약 3절의 status.json 을 돌려준다. 없으면 None.

    🔴 딱 하나만 가공한다 — **화이트리스트에 나중에 추가된 산출물 키를 채운다.**
       `status.json` 은 run 생성 시점의 `ARTIFACTS` 로 굳는다. 그래서 `exclusion` 을
       추가한 뒤 **이전 run 들은 그 키가 없는 채로 남는다.** 그런데 엔드포인트는
       200 을 준다 — 계약("키는 항상 전부 있다")이 옛 run 에 대해서만 거짓이 되고,
       프런트는 `artifacts.exclusion` 이 `undefined` 인지 `null` 인지로 run 나이를
       구분해야 한다. 그건 계약이 아니라 함정이다(원칙 4).

       **있는 값은 건드리지 않는다.** 빠진 키만 디스크를 보고 채운다 — 기록을 고쳐
       쓰는 게 아니라 빠진 칸을 사실로 메우는 것이다. 파일에도 쓰지 않는다.

    🔴 이 함수는 **읽기만 한다**(2026-08-09). 예전엔 첫 줄에서 `_reap_orphans()` 를 불러
       `runs/*/status.json` 을 전수 스캔하며 **남의 파일에 썼다.** 함수명이 `read_` 인데
       부작용이 있었고, 그래서 out-of-process 검증이 남이 돌리던 run 을 닫는 사고가
       났다(CLAUDE.md 「읽기인 줄 알았는데 쓰기」). 고아 정리는 `reap_orphans()` 로
       분리해 **부팅 때 한 번만** 부른다(`app/main.py` lifespan).
    """
    p = _status_path(run_id)
    if not p.is_file():
        return None
    doc = json.loads(p.read_text(encoding="utf-8"))

    arts = doc.get("artifacts")
    if isinstance(arts, dict):
        missing = [k for k in ARTIFACTS if k not in arts]
        if missing:
            pre = domain_prefix(doc["domain"])
            for name in missing:
                sub, suffix = ARTIFACTS[name]
                f = run_dir(run_id) / sub / f"{pre}{suffix}"
                arts[name] = _artifact_url(run_id, name) if f.is_file() else None

    # `loaded` 도 나중에 생긴 필드다(2026-08-10). 옛 run 에는 키가 없다.
    # 🔴 `null` 로 채우는 건 "적재 안 함"이 아니라 **"기록이 없다"** 까지 포함한다.
    #    산출물 키처럼 디스크를 보고 사실을 복원할 수가 없다 — 행 수를 아는 건
    #    그때 돌았던 적재기뿐이고, 지금 DB 에 물어보면 그 뒤 실행이 바꾼 값이 섞인다.
    #    그래서 지어내지 않고 null 로 두되, **구분이 필요하면 볼 곳을 계약에 적어뒀다**:
    #    `steps` 의 `적재-감리`·`적재-후보` 칸 상태가 그 run 의 사실이다(계약 3절).
    doc.setdefault("loaded", None)
    # `user_id` 도 나중에 생긴 필드다(2026-08-11, run_records). 옛 run 에는 키가 없다.
    # 🔴 여기서 `null` 은 **「주인이 없다」와 「그 시절엔 안 적었다」를 둘 다** 포함한다.
    #    구분이 필요하면 `run_records` 행의 유무를 본다 — 그 표는 이 기능 이후의
    #    run 만 갖고 있다. 지어내지 않고 null 로 둔다(원칙 4).
    #    ⚠ 익명 실행은 **정상 상태**다. null 을 "로그인 배선 전"으로 읽지 말 것.
    doc.setdefault("user_id", None)
    return doc


def loaded_record(run_id: str) -> dict:
    """이 run 이 **DB 에 무엇을 넣었다고 기록했는지**. 지금 DB 에 있는지는 안 본다.

    두 사실을 대조하려고 만들었다 — 「그때 넣었다」(여기)와 「지금 있다」(DB 조회).
    둘 다 참이고 **다른 질문**이라 한 필드에 접으면 안 된다. 적재기가 지운 것만
    기록할 수 있는 `invalidated_by` 같은 표시를 `status.json` 에 되쓰지 않는 이유가
    이것이다: 손으로 지운 것·정리 도구·DB 재생성은 코드 밖이라 쓸 자리가 없고,
    「20행을 적재했다」는 그 뒤에 무슨 일이 있어도 **여전히 참**이다(원칙 4).

    `state` 는 셋이다. 🔴 **「폴더가 없다」와 「물을 수 없다」를 같은 값으로 접지 않는다** —
    접으면 호출자가 "그런 run 은 없다"고 말하는데 사실은 "확인하지 못했다"가 된다.

      ``unknown_run``        `runs/<id>` 폴더 자체가 없다
      ``status_unreadable``  폴더는 있는데 `status.json` 을 못 읽는다
                             (파일 없음 · JSON 깨짐 · 권한). `reason` 에 사유가 있다
      ``known``              읽었다. `loaded` 는 그 run 의 기록(적재 칸이 없으면 `None`)

    읽기 전용이다. `read_status` 와 같은 이유로 부작용을 두지 않는다.
    """
    d = run_dir(run_id)
    if not d.is_dir():
        return {"state": "unknown_run", "loaded": None, "reason": None}
    try:
        doc = read_status(run_id)
    except Exception as ex:  # noqa: BLE001 — 종류를 안 가린다는 것 자체가 요점이다
        return {
            "state": "status_unreadable",
            "loaded": None,
            "reason": f"{type(ex).__name__}: {ex}",
        }
    if doc is None:
        return {
            "state": "status_unreadable",
            "loaded": None,
            "reason": "status.json 이 없다",
        }
    return {"state": "known", "loaded": doc.get("loaded"), "reason": None}


def note_run_record_error(doc: dict, at: str, reason: str) -> None:
    """`run_records`(DB 사본) 기록 실패를 **산출물에 남긴다.**

    🔴 「catch 한다」와 「조용히 삼킨다」는 다르다(원칙 1·4). DB 기록이 실패해도
       run 은 안 죽이기로 했는데, 그 실패가 아무 데도 안 남으면 마이페이지에서
       **없는 run** 이 되고 왜 없는지 알 방법이 사라진다.

    키는 실패했을 때만 생긴다. 항상 두고 `[]` 를 넣으면 이 기능 이전의 옛 run 까지
    "시도했고 다 성공" 으로 읽힌다 — `gate` 키를 항상 두지 않는 것과 같은 이유다.
    """
    doc.setdefault("run_record_errors", []).append(
        {"at": at, "time": _now_iso(), "reason": reason})
    _log.warning("run_records 기록 실패 [%s] run_id=%s: %s",
                 at, doc.get("run_id"), reason)


def reap_orphans() -> None:
    """서버가 죽어 중단된 run 을 failed 로 닫는다. **부팅 때 한 번만 부른다.**

    안 하면 status 가 'running' 인 채로 남아 프런트가 **영원히 폴링한다.**
    계약 4절('succeeded 또는 failed 가 되면 멈춘다')이 지켜지지 않는다.
    판정 근거: 이 서버 부팅 시각보다 먼저 시작됐는데 아직 진행 중으로 적혀 있다
    = 이전 프로세스의 것이다.

    🔴 왜 부팅 1회인가(2026-08-09). **답이 부팅 시점에 이미 고정돼 있다.** 판정식이
       `started_at < _SERVER_BOOT` 이므로, 이 서버가 시작한 run 은 영원히 대상이 아니고
       이전 서버의 run 은 부팅 순간 전부 확정돼 있다. 그런데 예전엔 `read_status` 마다
       불려서 **폴러 2개 × 초당 ~1.2회 × runs 39개**를 스캔했다. 그 읽기가 `_IO_LOCK`
       밖이라 `_write_status` 의 `os.replace` 가 WinError 5 로 터졌다
       (`배포후_작업일지\\20260808_파이프라인_실행중단_WinError5.md`). 스캔 자체가
       필요 없던 게 아니라 **횟수가 필요 없었다.**
    """
    if not RUNS_ROOT.is_dir():
        return
    reaped: list[dict] = []
    for sp in RUNS_ROOT.glob("*/status.json"):
        try:
            doc = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            continue  # 쓰는 중이거나 깨진 파일 — 다음 폴링에서 다시 본다
        if doc.get("status") not in ("queued", "running"):
            continue
        started = doc.get("started_at") or ""
        try:
            if datetime.fromisoformat(started) >= _SERVER_BOOT:
                continue  # 이 서버가 돌리는 중이다
        except ValueError:
            continue
        doc["status"] = "failed"
        doc["error"] = "서버가 재시작되어 실행이 중단됐습니다. 다시 실행하세요."
        doc["finished_at"] = _now_iso()
        for s in doc.get("steps", []):
            if s.get("status") == "running":
                s["status"] = "failed"
        reaped.append(doc)

    # 🔴 DB 사본 갱신을 **파일 쓰기 전에, 한 번에** 한다(계층 ③).
    #    ⓐ 한 번에 — 고아가 N개일 때 연결도 N번이면 DB 가 죽어 있을 때
    #      `10초 × N` 만큼 lifespan 이 멈춘다(그동안 서버가 안 뜬다).
    #    ⓑ 파일 쓰기 전에 — 실패 사유를 같은 문서에 담아 **한 번만** 쓰기 위해서다.
    #      `_write_status` 는 터질 수 있으므로(WinError 5) 횟수를 늘리지 않는다.
    if reaped:
        reason = run_records.record_runs_end(
            [(d, _read_params(d["run_id"]).get("user_input")) for d in reaped])
        if reason:
            for d in reaped:
                note_run_record_error(d, "reap", reason)
    for d in reaped:
        _write_status(d["run_id"], d)



# ══════════════════════════════════════════════════════════════════
# 7. 상태 문서
# ══════════════════════════════════════════════════════════════════
def _artifact_url(run_id: str, name: str) -> str:
    return f"{settings.API_V1_STR}/pipeline/runs/{run_id}/artifacts/{name}"


def _refresh_artifacts(doc: dict) -> None:
    """생긴 산출물만 URL 로 바꾼다. 키는 항상 전부 있고 값만 null ↔ URL."""
    run_id, domain = doc["run_id"], doc["domain"]
    pre = domain_prefix(domain)
    for name, (sub, suffix) in ARTIFACTS.items():
        p = run_dir(run_id) / sub / f"{pre}{suffix}"
        doc["artifacts"][name] = _artifact_url(run_id, name) if p.is_file() else None


def _new_status(run_id: str, domain: str, mode: str = MODE_FIXTURE,
                user_id: int | None = None, auto_approve: bool = False) -> dict:
    # 🔴 `gate` 키는 여기 없다. 계약 7-3 — `awaiting_hitl` 일 때만 **키가 생긴다**.
    #    항상 두고 null 을 넣으면 "게이트가 있는데 질문이 없다"로 읽힌다.
    #    같은 이유로 `run_record_errors` 도 여기 없다 — DB 사본 기록에 **실패했을 때만**
    #    생긴다(`note_run_record_error`). 항상 두고 `[]` 를 넣으면 옛 run 까지
    #    "시도했고 다 성공"으로 읽힌다.
    return {
        "run_id": run_id,
        "domain": domain,
        "mode": mode,
        # 이 run 을 돌린 사람. **null 이 정상 상태다** — 로그인 없이 실행하는 경로가
        # 설계상 살아 있다(2026-08-11 사람 결정, 4계층 문서). `run_records.user_id`
        # 가 영구 nullable 인 것과 같은 이유다.
        "user_id": user_id,
        # 「고속 자동 분석」으로 돌렸는가. 🔴 **`mode` 와 다른 축이다** — `full` 이라고
        # 자동이 아니고 `hitl` 이라고 대화형이 아니다. 이 값이 없으면 화면은 둘을
        # 구분할 방법이 없어 `mode` 로 유추하게 되고, 그러면 게이트에 멈춰 선
        # 맞춤형 full run 이 "자동"으로 표시된다(실제로 그렇게 표시됐다).
        # 값은 `params.json` 에도 있지만 그건 프런트가 못 읽는다.
        # ⚠ 옛 run 은 **키 자체가 없다**(그때는 이 기능이 없었으므로 없는 게 맞다).
        "auto_approve": auto_approve,
        "status": "queued",
        "steps": [{"id": i, "label": lb, "status": "idle", "sec": None}
                  for i, lb in step_labels(mode)],
        "artifacts": {k: None for k in ARTIFACTS},
        # 이 run 이 **DB 에 넣은 것**. 계약 3절.
        #   null            = 아무것도 안 넣었다 (세 모드 다 적재 칸이 있으므로,
        #                     이제 null 은 "아직 그 칸에 닿지 않았다" 는 뜻이다.
        #                     옛 run 은 키 자체가 없어 null 로 채워진다 — 계약 3-1)
        #   {run_id, …}     = 넣었다. `run_id` 는 프런트가 `/simulations/candidates`
        #                     의 `run_id` 파라미터에 그대로 넣을 값이다.
        # 🔴 프런트가 규칙("full 이면 run_id 와 같다")을 따로 들고 있지 않게 **값으로**
        #    준다. 규칙을 양쪽이 각자 구현하면 언젠가 갈린다.
        "loaded": None,
        "error": None,
        "started_at": _now_iso(),
        "finished_at": None,
    }


def _step(doc: dict, step_id: str) -> dict:
    return next(s for s in doc["steps"] if s["id"] == step_id)

