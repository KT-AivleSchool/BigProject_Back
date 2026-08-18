# -*- coding: utf-8 -*-
"""실행 — 접수·취소·스레드·단계 진행.

🔴 취소는 셋을 같이 한다: `_CANCELLED` 표시 → 자식 종료 → 스레드 join.
   `status.json` 만 고치면 실행 스레드가 자기 메모리의 `doc` 으로 **덮어써서**
   `running` 으로 되돌아간다(PR #257).
🔴 `_ACTIVE` 도메인 자물쇠는 **스레드가 끝난 뒤에야** 반납한다. 먼저 놓으면
   두 run 이 같은 정본 데이터를 동시에 건드린다.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time

from app.config import BASE_DIR
from app.services import run_records

from .answers import _run_auto_gate, _stage_args
from .artifacts import artifact_path
from .commands import (
    _CASCADED_RE,
    _LOADED_RE,
    _Proc,
    _proc_load_audit,
    _proc_load_topn,
    _proc_of,
    _proc_propose,
    _proc_runpipe,
    build_commands,
)
from .conditions import (
    TOPN_DEFAULT,
    TOPN_MAX,
    _full_conditions,
    _load_conditions,
    _load_fixture,
    _read_params,
    _write_params,
)
from .gates import _seed_reviewed, build_gate
from .prepare import (
    _child_env,
    _domain_root,
    _new_run_id,
    _prepare_dirs,
    _validate_domain,
)
from .state import (
    MODES,
    MODE_FIXTURE,
    MODE_FULL,
    _ACTIVE,
    _CANCEL_JOIN_S,
    _CANCEL_MSG,
    _CANCELLED,
    _CHILDREN,
    _Cancelled,
    _LIVE_STATUSES,
    _LOCK,
    _TERM_GRACE_S,
    _WORKERS,
    RunConflict,
    RunRequestError,
    _StepFailed,
    _now_iso,
    run_dir,
)
from .status import (
    _new_status,
    _refresh_artifacts,
    _step,
    _write_status,
    note_run_record_error,
    read_status,
)
from .steps import _PLAN


def _validate_full_params(domain: str, user_input: str | None,
                          topn: int | None) -> dict:
    """full 모드 요청 파라미터 검증. 값이 이상하면 **실행 전에** 400 을 낸다."""
    if not isinstance(user_input, str) or not user_input.strip():
        raise RunRequestError(
            "full 모드는 사용자 의도(user_input)가 필수입니다. "
            '예: "용산구 흡연부스 부지 선정" — 시설·지역을 여기서 확정합니다.')
    ui = user_input.strip()
    if ui.startswith("--"):
        # gam2_run_pipeline 의 CLI 는 `--` 로 시작하는 토큰을 전부 플래그로 본다.
        # 그대로 넘기면 위치인자가 하나 모자라 usage 만 찍고 죽는다(조용하진 않지만
        # 사유가 엉뚱하게 보인다).
        raise RunRequestError("user_input 은 '--' 로 시작할 수 없습니다.")
    if len(ui) > 200:
        raise RunRequestError(f"user_input 이 너무 깁니다({len(ui)}자, 상한 200).")

    n = TOPN_DEFAULT if topn is None else topn
    if isinstance(n, bool) or not isinstance(n, int):
        raise RunRequestError(f"topn 은 정수여야 합니다: {topn!r}")
    if not (1 <= n <= TOPN_MAX):
        raise RunRequestError(f"topn 범위는 1~{TOPN_MAX} 입니다: {n}")

    # 프로파일링 대상이 없으면 STEP0 이 빈 fixture 로 진행한다 — 여기서 멈춘다(원칙 1).
    # 🔴 **자식이 볼 루트와 같은 곳을 본다.** 여기서 `DOMAIN_ROOT` 를 보면 서버는
    #    프리셋을 보고 자식은 빈 `user_input` 을 보는 「가짜 초록불」이 된다.
    data_dir = _domain_root(MODE_FULL) / domain / "data"
    if not data_dir.is_dir() or not any(p.is_file() for p in data_dir.iterdir()):
        raise RunRequestError(
            f"원본 데이터가 없습니다: {data_dir} — 화면1(업로드)로 먼저 올리세요.")
    return {"user_input": ui, "topn": n}


def start_run(domain: str, mode: str, user_input: str | None = None,
              topn: int | None = None, user_id: int | None = None,
              auto_approve: bool = False) -> str:
    """검증 → run 폴더 준비 → 백그라운드 실행. run_id 를 돌려준다.

    `auto_approve` 는 「고속 자동 분석」이다 — 게이트를 **없애는 게 아니라** 그 자리에
    AI 제안값을 그대로 넣고 지나간다. 그래서 계획(`_PLAN`)은 그대로이고 산출물에는
    `value_source: "llm"` 이 남는다(사람이 확정한 run 과 구분된다 — 원칙 4).
    """
    if mode not in MODES:
        raise RunRequestError(
            f"지원하지 않는 mode 입니다: {mode!r} (가능: {', '.join(MODES)})")
    _validate_domain(domain, mode)

    # 🔴 fixture 는 계획에 게이트가 없다. 받아놓고 안 쓰면 호출자는 「자동승인으로
    #    돌았다」고 읽는데 실제로는 승인할 게 없었다 — 뜻이 다른 두 실행이 같은
    #    응답을 준다(원칙 4). fixture 자체가 이미 사람 개입 0회다.
    if auto_approve and mode == MODE_FIXTURE:
        raise RunRequestError(
            "auto_approve 는 게이트가 있는 mode(hitl·full)에서만 씁니다 — "
            "fixture 는 계획에 게이트가 없어 승인할 대상이 없습니다.")

    if mode == MODE_FULL:
        params = _validate_full_params(domain, user_input, topn)
        # 조립을 미리 해본다(실패를 실행 전에 낸다). full 은 3-2 의 반경이 게이트B 에서
        # 오므로 그 단계만 빼고 확인한다 — 지금 조립하면 `_radius_arg` 가 정상적으로 막는다.
        base = _full_conditions(domain, params["topn"])
        _proc_runpipe(domain, params["user_input"])
        for s in ("2", "3-1", "4"):
            _proc_of(s, domain, base)
    else:
        # 🔴 fixture·hitl 은 실행 조건이 픽스처에서 온다. 이 두 값을 받으면
        #    "받아놓고 안 쓰는 인자"가 되고, 호출자는 반영됐다고 읽는다(원칙 4).
        for k, v in (("user_input", user_input), ("topn", topn)):
            if v is not None:
                raise RunRequestError(
                    f"{k} 는 mode=full 에서만 씁니다 "
                    f"(fixture·hitl 의 실행 조건은 <도메인>_FIX/기준값.json 에서 온다).")
        params = {}
        _load_fixture(domain)      # 픽스처가 없으면 여기서 400
        build_commands(domain)     # 커맨드 조립도 미리 해본다(실패를 실행 전에 낸다)

    if auto_approve:
        # 🔴 게이트에서 스레드가 끝났다가 새 스레드가 이어받으므로 메모리에 둘 수 없다.
        #    hitl 은 여기까지 `params` 가 `{}` 라 `_write_params` 가 아예 안 불렸다.
        params["auto_approve"] = True
    # 여기서 `reap_orphans()` 를 부르지 않는다(2026-08-09). 부팅 때 이미 돌았고,
    # 그 뒤 생긴 run 은 전부 `started_at >= _SERVER_BOOT` 라 **판정 대상이 아니다** —
    # 무조건 no-op 인 전수 스캔을 요청 경로에 두면 os.replace 경합만 늘린다.

    with _LOCK:
        if domain in _ACTIVE:
            raise RunConflict(f"'{domain}' 은 이미 실행 중입니다 (run_id={_ACTIVE[domain]})")
        run_id = _new_run_id()
        _ACTIVE[domain] = run_id

    try:
        _prepare_dirs(run_id, domain, mode)
        if params:
            # status 보다 **먼저** 쓴다. 실행 스레드가 곧바로 읽는다.
            _write_params(run_id, params)
        doc = _new_status(run_id, domain, mode, user_id, auto_approve)
        # fixture·hitl 은 `reviewed` 를 방금 _prepare_dirs 가 넣어서 **이미 있다.**
        # 여기서 안 갱신하면 첫 단계 전이까지 status 는 null 인데 엔드포인트는 200 을
        # 준다 — status 가 거짓말을 한다(원칙 4). 나머지 6개는 아직 없으므로 null 이다.
        # full 은 8개 전부 null 로 시작한다(감리를 이 run 이 지금부터 돈다) — 맞는 값이다.
        _refresh_artifacts(doc)
        # 🔴 DB 사본(계층 ③)에 **발급 사실**을 먼저 적고, 그 결과까지 담아 파일을
        #    한 번만 쓴다. 순서를 뒤집으면 실패 사유를 적으려고 `_write_status` 를
        #    두 번 부르게 되는데, 그 함수는 터질 수 있다(WinError 5).
        #    ⚠ 여기서 실패해도 **run 은 그대로 시작한다.** 정본은 `status.json` 이고
        #      DB 는 사본이다. 대신 사유를 남긴다(원칙 1·4) — 종료 때 UPSERT 가
        #      같은 행을 다시 만들 기회를 갖는다.
        #    ⚠ DB 가 죽어 있으면 이 한 줄이 `DB_CONNECT_TIMEOUT`(기본 10초)만큼
        #      run 시작을 **늦춘다.** 느려지는 것이지 죽지 않는다.
        reason = run_records.record_run_start(doc, params.get("user_input"))
        if reason:
            note_run_record_error(doc, "start", reason)
        _write_status(run_id, doc)
    except Exception:
        with _LOCK:
            _ACTIVE.pop(domain, None)
        raise

    _spawn(run_id, domain, mode, 0)
    return run_id


def _is_cancelled(run_id: str) -> bool:
    with _LOCK:
        return run_id in _CANCELLED


def _terminate(child: subprocess.Popen) -> None:
    """자식을 끝낸다 — terminate → 유예 → kill.

    🔴 `child.wait()` 를 쓰지 않는다. 실행 스레드가 같은 객체로 `wait()` 중이라
       두 스레드가 같은 자식을 기다리게 된다. `poll()` 은 이미 반납된 뒤에도
       종료 코드를 그대로 돌려주므로 누가 먼저 거둬가든 판정이 안 갈린다.
    """
    if child.poll() is not None:
        return
    try:
        child.terminate()
    except OSError:
        return                      # 그 사이에 스스로 끝났다
    deadline = time.monotonic() + _TERM_GRACE_S
    while child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if child.poll() is None:
        child.kill()


def _close_cancelled(run_id: str, doc: dict) -> dict:
    """실행 스레드가 **없는** 취소를 여기서 닫는다(게이트 대기·서버 재시작 뒤).

    스레드가 살아 있으면 이 함수를 부르면 안 된다 — 스레드는 자기 메모리의 `doc` 을
    들고 있어서 단계 전이마다 `_write_status` 를 다시 쓴다. 밖에서 쓴 상태는
    **다음 전이에 조용히 덮인다**(그게 원래 PR 이 `running` run 을 못 멈춘 이유다).
    """
    # 🔴 여기서 한 번 더 읽는다. `cancel_run` 이 상태를 본 뒤 스레드가 마지막
    #    한 칸을 끝내고 `succeeded` 로 닫았을 수 있다 — 그 좁은 틈에서 이 함수가
    #    돌면 **완주한 run 을 취소로 적는다**(원칙 4). 그건 취소가 아니라 위조다.
    fresh = read_status(run_id)
    if fresh is not None:
        doc = fresh
        if doc.get("status") not in _LIVE_STATUSES:
            with _LOCK:
                _CANCELLED.discard(run_id)
            raise RunConflict(
                f"취소를 접수하는 사이에 run 이 끝났습니다 "
                f"(status={doc.get('status')!r}) — 상태를 덮어쓰지 않았습니다.")
    domain = doc.get("domain")
    doc["status"] = "failed"
    doc["error"] = _CANCEL_MSG
    doc["finished_at"] = _now_iso()
    doc.pop("gate", None)           # 계약 7-3 — 끝난 run 에 gate 키는 없다
    for s in doc.get("steps", []):
        if s.get("status") == "running":
            s["status"] = "failed"
    _refresh_artifacts(doc)
    # 자원 반납이 상태 기록보다 **먼저**다 — `_execute` 의 finally 와 같은 이유다
    # (2026-08-08 WinError 5: `_write_status` 가 터지면 도메인이 409 로 잠긴다).
    with _LOCK:
        if domain and _ACTIVE.get(domain) == run_id:
            _ACTIVE.pop(domain, None)
        _CANCELLED.discard(run_id)
    reason = run_records.record_run_end(doc,
                                        _read_params(run_id).get("user_input"))
    if reason:
        note_run_record_error(doc, "end(취소)", reason)
    _write_status(run_id, doc)
    return doc


def cancel_run(run_id: str) -> dict:
    """실행을 취소한다 — **자식 프로세스를 실제로 죽인다**(계약 3-4).

    🔴 status.json 만 고쳐 쓰는 것은 취소가 아니다. 두 가지가 같이 안 되면
       「멈췄다고 말하는 화면」과 「계속 도는 파이프라인」이 동시에 존재한다:
         ① 자식 프로세스를 죽인다(`_CHILDREN`). 안 죽이면 정본 캐시·산출물
            디렉터리를 계속 갈아엎고, 실행 스레드가 다음 단계 전이에서
            `_write_status` 로 취소 상태를 **덮어쓴다**.
         ② `_ACTIVE` 를 반납한다. 안 하면 그 도메인은 재시작 전까지 409 다.
            반납은 **스레드가 실제로 끝난 뒤**여야 한다 — 스레드가 살아 있는데
            먼저 풀면 같은 도메인으로 새 run 이 시작돼 두 실행이 같은 정본
            데이터를 동시에 건드린다.

    끝난 뒤 `status` 는 `failed` 이고 `error` 가 취소 사유다. 취소용 상태값을
    새로 만들지 않는다 — 계약 3절의 상태 어휘가 늘면 프런트의 폴링 종료 조건이
    모든 화면에서 갈린다. **왜 끝났는지는 `error` 가 말한다.**

    없는 run_id 는 `KeyError`(→404), 이미 끝난 run 은 `RunConflict`(→409)다.
    조용히 204 로 답하면 「취소했다」가 되는데 실제로는 아무 일도 안 일어났다(원칙 4).
    """
    doc = read_status(run_id)
    if doc is None:
        raise KeyError(run_id)
    status = doc.get("status")
    if status not in _LIVE_STATUSES:
        raise RunConflict(
            f"이미 끝난 run 입니다 (status={status!r}) — 취소할 것이 없습니다.")

    with _LOCK:
        _CANCELLED.add(run_id)
        child = _CHILDREN.get(run_id)
        worker = _WORKERS.get(run_id)

    if child is not None:
        _terminate(child)

    # 🔴 `is_alive()` 를 여기 조건에 넣지 않는다(2026-08-12 실측으로 고쳤다).
    #    방금 `_terminate` 로 자식을 죽였으므로 실행 스레드는 **그 자리에서 끝난다** —
    #    운이 나쁘면 이 줄에 닿기 전에 이미 죽어 있다. 그때 `is_alive()` 로 갈라
    #    아래 `_close_cancelled` 로 보내면, 그 함수가 다시 읽은 status 는 스레드가
    #    이미 `failed` 로 닫아둔 값이라 **정상 취소가 409 로 나간다.** 타이밍에 따라
    #    갈리는 자리라 한 번 돌려보고 "된다"고 말할 수 없다.
    #    장부에 스레드가 있다 = 이 run 은 스레드가 돌린 것 = **뒷정리 주체는 그쪽**이다
    #    (`_execute` 의 finally 하나뿐). 살아 있으면 기다리고, 끝났으면 이미 끝났다.
    if worker is not None:
        if worker.is_alive():
            worker.join(_CANCEL_JOIN_S)
            if worker.is_alive():
                # 🔴 끝난 척하지 않는다. 스레드가 살아 있다 = `_ACTIVE` 가 아직 안
                #    풀렸다 = 같은 도메인 재실행은 여전히 409 다.
                raise RunConflict(
                    f"취소 요청은 접수했고 자식 프로세스는 정리했지만, 실행 스레드가 "
                    f"{_CANCEL_JOIN_S:.0f}초 안에 끝나지 않았습니다. "
                    f"GET /runs/{run_id} 로 상태를 확인하고 다시 시도하세요.")
        with _LOCK:
            _CANCELLED.discard(run_id)   # 스레드가 이미 지웠으면 no-op
        return read_status(run_id) or doc

    # 스레드가 없다 — 게이트 대기 중이거나(스레드는 게이트에서 끝난다) 서버가
    # 재시작된 뒤다. 그러면 아무도 뒷정리를 안 하므로 여기서 닫는다.
    return _close_cancelled(run_id, doc)


def _spawn(run_id: str, domain: str, mode: str, start: int) -> None:
    t = threading.Thread(target=_execute, args=(run_id, domain, mode, start),
                         daemon=True)
    # 등록은 `start()` **앞**이다. 뒤에 두면 그 사이에 들어온 취소가 스레드를
    # 못 보고 `_close_cancelled` 로 가서, 스레드와 취소가 같은 문서를 같이 쓴다.
    with _LOCK:
        _WORKERS[run_id] = t
    t.start()


def _execute(run_id: str, domain: str, mode: str, start: int = 0) -> None:
    """계획을 `start` 칸부터 돌린다. 게이트를 만나면 **멈추고 스레드가 끝난다.**"""
    doc = read_status(run_id) or _new_status(run_id, domain, mode)
    doc["status"] = "running"
    doc.pop("gate", None)          # 계약 7-3 — running 에는 gate 키가 없다
    _write_status(run_id, doc)

    base = _load_conditions(domain, mode, run_id)
    params = _read_params(run_id)
    # 🔴 게이트를 사람이 답하면 **그 스레드는 끝난다** — 재개는 다른 스레드다.
    #    그래서 이 값은 인자로 못 받는다. `params.json` 이 유일한 운반 수단이다.
    auto_approve = bool(params.get("auto_approve"))
    plan = _PLAN[mode]
    log_path = run_dir(run_id) / "run.log"
    paused = False
    try:
        # 이어가는 실행은 append 다. "w" 로 열면 게이트 전 로그가 사라진다 —
        # 프런트가 게이트 화면에서 보던 로그가 답변 순간 증발한다(원칙 4).
        with open(log_path, "a" if start else "w", encoding="utf-8") as log:
            for i in range(start, len(plan)):
                # 🔴 칸과 칸 **사이**에서도 본다. 자식이 없는 칸(`seed`)이나 자식이
                #    막 끝난 순간에 들어온 취소는 `_run_one` 이 못 잡는다 — 여기서
                #    안 보면 그 run 은 취소를 접수하고도 다음 칸을 시작한다.
                if _is_cancelled(run_id):
                    raise _Cancelled()
                stage = plan[i]
                if stage.startswith("gate:"):
                    gate_id = stage.split(":", 1)[1]
                    gate = build_gate(gate_id, run_id, domain)
                    if auto_approve:
                        # 🔴 게이트를 **건너뛰는 게 아니라** 그 자리에서 답한다.
                        #    질문은 똑같이 만들고(위 `build_gate`) 검증기도 똑같이
                        #    탄다 — 계획에서 게이트를 빼버리면 「자동 모드에서만
                        #    통과하는 값」이 생기고, 그때 무엇을 승인했는지가
                        #    산출물 어디에도 안 남는다(원칙 4).
                        #    멈추지 않으므로 스레드도 안 갈아탄다 — `continue` 다.
                        ans = _run_auto_gate(run_id, domain, gate_id, gate)
                        log.write(f"\n[게이트 {gate_id}] AI 제안값 자동승인 — "
                                  f"질문 {len(gate.get('questions') or [])}건 · "
                                  f"{json.dumps(ans, ensure_ascii=False)}\n")
                        log.flush()
                        _refresh_artifacts(doc)
                        _write_status(run_id, doc)
                        continue
                    log.write(f"\n[게이트 {gate_id}] 사람 확정 대기\n")
                    log.flush()
                    doc["status"] = "awaiting_hitl"
                    doc["gate"] = gate
                    _refresh_artifacts(doc)
                    _write_status(run_id, doc)
                    paused = True
                    break
                if stage == "seed":
                    # 프로세스가 아니라 파일 하나를 정하는 일이다. 자식을 띄우지 않는다.
                    src = _seed_reviewed(run_id, domain)
                    log.write(f"\n[감리 입력 확정] reviewed ← {src}\n")
                    log.flush()
                    _refresh_artifacts(doc)
                    _write_status(run_id, doc)
                    continue
                proc = (_proc_runpipe(domain, params["user_input"])
                        if stage == "0-1"
                        else _proc_load_audit(domain, run_id) if stage == "load-audit"
                        else _proc_load_topn(domain, run_id) if stage == "load"
                        else _proc_propose(domain, base, run_id) if stage == "propose"
                        else _proc_of(stage, domain, base,
                                      *_stage_args(run_id, mode, stage),
                                      value_source="llm" if auto_approve else None))
                _run_one(run_id, doc, proc, log)
                if proc.loaded or proc.cascaded:
                    # 적재 칸이 둘이라 두 번 합류한다. `run_id` 는 _run_one 이
                    # 자식이 찍은 값과 대조해 통과시킨 것이다.
                    doc["loaded"] = {"run_id": run_id,
                                     **(doc.get("loaded") or {}), **proc.loaded}
                    if proc.cascaded:
                        # 🔴 덮어쓰면서 지워진 것. 러너 경로에서는 **항상 0 이어야**
                        #    한다 — `_new_run_id` 가 최고수위 원장을 쓰므로 이 run_id 로
                        #    적재된 게 있을 수 없다. 0 이 아니면 원장이 지워졌거나
                        #    누가 같은 run_id 로 손수 적재한 것이다.
                        #    그 run 의 공청회 결과가 사라졌다는 사실이 여기 말고는
                        #    남는 곳이 없다 — 자식 콘솔은 run.log 로만 흘러간다.
                        doc["loaded"]["cascaded"] = {
                            **(doc["loaded"].get("cascaded") or {}), **proc.cascaded}
                    _write_status(run_id, doc)
                if stage == "3-2":
                    _assert_provenance(run_id, mode, auto_approve)
        if not paused:
            doc["status"] = "succeeded"
    except _Cancelled:
        # 취소도 **종료 사유**다 — 실패와 같은 자리에 적는다. 취소 전용 상태값을
        # 새로 만들지 않는 이유는 `cancel_run` 독스트링에 있다(폴링 종료 조건이 갈린다).
        doc["status"] = "failed"
        doc["error"] = _CANCEL_MSG
        for s in doc.get("steps", []):
            if s.get("status") == "running":
                # 🔴 done 으로 적지 않는다. 중간에 끊긴 칸이다 — done 이면
                #    산출물이 다 나온 것처럼 읽힌다(원칙 4).
                s["status"] = "failed"
    except _StepFailed as e:
        doc["status"] = "failed"
        doc["error"] = str(e)
    except Exception as e:  # 러너 자신의 버그도 숨기지 않는다
        doc["status"] = "failed"
        doc["error"] = f"{type(e).__name__}: {e}"
    finally:
        # 🔴 취소 장부 정리는 게이트 정지·완주·실패를 **가리지 않는다.** 스레드가
        #    끝났다는 사실 자체가 여기서 참이 되므로, 게이트에서 멈춘 run 도
        #    `_WORKERS` 에서 빠져야 한다 — 안 빼면 나중 취소가 죽은 Thread 객체를
        #    보고 `join()` 으로 가서, 실제로는 아무도 뒷정리를 하지 않는다.
        #    (`is_alive()` 가 False 라 통과는 하지만 그때 상태를 닫는 코드가 없다.)
        with _LOCK:
            _WORKERS.pop(run_id, None)
            _CANCELLED.discard(run_id)
        if doc["status"] != "awaiting_hitl":
            # 🔴 게이트에서 멈춘 run 은 **끝난 게 아니다.** finished_at 을 찍지 않고
            #    _ACTIVE 에서 빼지도 않는다 — 빼면 같은 도메인으로 새 run 을 시작할 수
            #    있게 되고, 두 run 이 같은 정본 캐시·데이터를 동시에 건드린다.
            doc["finished_at"] = _now_iso()
            _refresh_artifacts(doc)
            # 🔴 자원 반납을 상태 기록보다 **먼저** 한다(2026-08-08).
            #    _write_status 는 터질 수 있다(위 WinError 5). 뒤에 두면 기록이
            #    실패한 순간 예외가 나서 여기까지 못 오고, 그 도메인은 서버를
            #    재시작할 때까지 409 로 잠긴다 — 409 는 파일이 아니라 _ACTIVE 로
            #    판정하므로 status.json 을 손으로 고쳐도 안 풀린다.
            #    기록 실패와 자원 반납이 같이 묶일 이유가 없다.
            #    (자식 프로세스는 이미 끝났고 새 run 은 다른 run_id·다른 폴더를
            #     쓰므로, 여기서 먼저 풀어도 두 run 이 겹치지 않는다)
            with _LOCK:
                if _ACTIVE.get(domain) == run_id:
                    _ACTIVE.pop(domain, None)
            # 🔴 DB 사본(계층 ③) 갱신도 `_write_status` **앞**이다. 위와 같은 이유로
            #    자원 반납보다는 뒤, 파일 쓰기보다는 앞에 둔다 — 실패 사유를 같은
            #    문서에 담아 한 번만 쓴다.
            #    ⚠ 이 호출은 **UPSERT** 다. 발급 INSERT 가 실패했으면 여기서 행이
            #      생긴다 — 「행이 없는 상태」가 정상 경로에 있으므로 단순 UPDATE 면
            #      완주한 run 이 이력에서 통째로 사라진다(원칙 4).
            #    ⚠ 게이트에서 멈춘 run 은 여기 안 온다. `awaiting_hitl`·`running` 은
            #      DB 에 안 적는다(진행률은 status.json 담당) — 계약 §3-2.
            reason = run_records.record_run_end(doc, params.get("user_input"))
            if reason:
                note_run_record_error(doc, "end", reason)
            _write_status(run_id, doc)




def _assert_provenance(run_id: str, mode: str, auto_approve: bool = False) -> None:
    """STEP3-2 산출물의 `hitl` 블록이 **이 run 에 실제로 있었던 사람 개입**과 맞는지 본다.

    자식 프로세스는 자기가 받은 값이 어디서 왔는지 모른다 — `--radius 07+02=150` 만
    봐서는 픽스처인지 사람 답인지 구분이 안 된다. 그래서 러너가 `--value-source` 로
    알려주는데, **인자가 새면 산출물이 조용히 거짓말한다.** 2026-08-05 `r_20260805_017`
    이 그랬다: fixture 재생인데 `value_source:"cli"` · `*_confirmed:true` 로 찍혔다.
    값은 맞고 설명만 틀려서 아무 데서도 안 터졌다(원칙 4 위반).

    🔴 이 필드들은 **대조기에 하나도 안 들어 있고**(S16) 코드 소비자도 0곳이다.
       아무도 안 보는 값은 틀려도 안 걸린다. 그래서 러너가 **자기만 아는 사실로**
       직접 대조한다 — 자식은 이 사실에 접근할 수 없다:
         · fixture 모드 = 사람 개입 0회 (`stdin=DEVNULL` · 값은 전부 픽스처)
         · hitl 모드    = 게이트B 에서 사람이 답했다
                          (답이 없으면 `_stage_args` 가 이미 RuntimeError 다)
         · 자동승인     = 게이트B 를 **띄우긴 했고** 그 답을 AI 제안값으로 채웠다.
                          사람이 아니므로 `llm` 이고 `*_confirmed` 는 전부 False 여야
                          한다 — 여기가 초록불이면 「고속 모드로 돌렸는데 사람이
                          확정했다고 적힌」 산출물을 잡을 자리가 사라진다.

    어긋나면 run 을 `failed` 로 닫는다. 숫자는 맞을 수 있지만 **그 숫자를 누가 정했는지가
    틀린 산출물**이고, 그건 뒤따르는 모든 판단의 근거가 된다.
    """
    p = artifact_path(run_id, "weight_set")
    if p is None:
        raise _StepFailed("STEP3-2 가 끝났는데 weight_set.json 이 없습니다.")
    rec = json.loads(p.read_text(encoding="utf-8")).get("hitl")
    if not isinstance(rec, dict):
        raise _StepFailed(
            "weight_set.json 에 hitl 블록이 없습니다 — 이 실행의 값 출처를 "
            "설명할 수 없습니다. run_weight_model.build_hitl_record 확인.")

    vs = rec.get("value_source")
    confirmed = [k for k in ("radius_confirmed", "weight_confirmed") if rec.get(k)]
    if mode == MODE_FIXTURE:
        # 픽스처 재생은 정의상 사람이 한 번도 안 끼어든다. 여기서 "확정" 이 찍히면
        # 사람이 안 한 일을 했다고 적은 것이다 — 설명책임 필드에서 가장 나쁜 방향이다.
        if confirmed or vs != "fixture":
            raise _StepFailed(
                f"fixture 재생인데 산출물이 사람 확정을 주장합니다: "
                f"value_source={vs!r} · {confirmed or '확정없음'}. "
                f"러너가 --value-source 를 제대로 넘겼는지 확인하세요.")
    elif auto_approve:
        if vs != "llm" or confirmed:
            raise _StepFailed(
                f"자동승인 run 인데 산출물이 다른 출처를 주장합니다: "
                f"value_source={vs!r} · {confirmed or '확정없음'}. "
                f"사람이 본 적 없는 값이 사람 확정으로 남습니다.")
    elif vs != "human":
        # 게이트B 를 거쳐 왔는데 사람 출처가 아니다. 반대 방향(과소기록)이지만
        # 역시 사실과 다르다. 실측된 경로 하나 — 답변의 `radius` 가 비면
        # `_proc_of` 가 `"human" if radius else "fixture"` 로 fixture 를 넘긴다
        # (전 지표가 admin 이면 `_validate_weight` 가 radius 를 금지하므로 도달 가능).
        raise _StepFailed(
            f"게이트B 를 거친 run 인데 값 출처가 사람이 아닙니다: value_source={vs!r}. "
            f"사람이 답했다는 사실이 산출물에서 사라집니다.")


def _fail_reason(tail: list[str], rc: int) -> str:
    """자식이 남긴 마지막 줄들에서 **실패 사유**를 고른다.

    🔴 예전엔 `tail[-1]` 하나였다. 파이프라인의 중단 메시지는 여러 줄이고
       **마지막 줄이 대개 「이렇게 고치세요」 안내**라서, 실제로 프런트에 나간
       error 가 이랬다 —

           python app\\services\\gam2_audit_judgment_test.py hitl <도메인>

       사유가 아니라 **명령어**다. 화면은 「무엇이 왜 실패했는지」를 못 말하고,
       읽는 사람은 그 명령을 치라는 뜻으로 읽는다(원칙 4).

       파이프라인은 중단을 전부 `[중단]` 으로 시작하는 블록으로 찍는다
       (`gam4_site_select` · `make_parcel_candidates` · `gam2_clean_data` ·
       `gam2_run_pipeline`). 그 마커부터 끝까지를 사유로 삼는다 — 안내 줄까지
       같이 나가는 건 손해가 아니다. 잘라내면 남는 게 진단뿐이라 좋아 보이지만,
       그 안내가 사람이 다음에 할 일이다.

    마커가 없으면 마지막 줄로 되돌아간다. **지어내지 않는다** — 못 찾았을 때
    그럴듯한 문장을 합성하면 없는 사유가 기록된다.
    """
    for i in range(len(tail) - 1, -1, -1):
        if tail[i].startswith("[중단]"):
            block = tail[i:]
            if len(block) > 12:            # 트레이스백이 통째로 붙는 경우
                block = block[:12] + [f"… (이하 {len(tail) - i - 12}줄은 run.log)"]
            return "\n".join(block)
    return tail[-1] if tail else f"종료 코드 {rc}"


def _run_one(run_id: str, doc: dict, proc: _Proc, log) -> None:
    log.write(f"\n$ {' '.join(proc.argv)}\n")
    log.flush()

    # 🔴 `step_ids` 가 빈 프로세스가 있다 — 게이트B 제안 패스(`_proc_propose`).
    #    계약 2절의 6단계 중 어느 것도 아니므로 **진행률을 건드리지 않는다.**
    #    없는 단계를 만들어 붙이면 프런트 진행률이 실제와 어긋난다(원칙 4).
    cur = proc.step_ids[0] if proc.step_ids else None
    started = time.perf_counter()
    if cur:
        _step(doc, cur)["status"] = "running"
        _write_status(run_id, doc)

    tail: list[str] = []          # 실패 시 error 로 내보낼 마지막 줄들
    mismatch: str | None = None   # 적재 run_id 어긋남 (자식이 끝난 뒤에 던진다)
    child = subprocess.Popen(
        proc.argv,
        cwd=str(BASE_DIR),
        env=_child_env(run_id, doc["mode"]),
        stdin=subprocess.DEVNULL,   # 🔴 HITL 이 새로 생기면 EOFError 로 즉시 터진다.
        stdout=subprocess.PIPE,     #    조용히 멈추는 것보다 시끄럽게 죽는 게 낫다.
        stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    # 🔴 핸들을 장부에 남긴다. 여기 말고는 자식에 닿을 방법이 없다 — 없으면 취소는
    #    status.json 만 고쳐 쓰고 자식은 끝까지 돈다(원칙 4).
    #    등록은 `Popen` **직후**다. 뒤로 미루면 그 사이에 들어온 취소가 자식을 못 본다.
    with _LOCK:
        _CHILDREN[run_id] = child
    try:
        assert child.stdout is not None
        for line in child.stdout:
            log.write(line)
            s = line.strip()
            if s:
                tail.append(s)
                del tail[:-40]
            if (m := _LOADED_RE.match(s)):
                # run_id 도 같이 본다. 적재기가 `--run` 을 무시하고 정본에 넣었다면
                # 여기서 드러나야 한다 — 이 run 의 성과로 status 에 적히면 프런트가
                # `/candidates?run_id=` 로 조회했을 때 0건이 나온다(원칙 4).
                # 🔴 여기서 바로 raise 하지 않는다. 파이프를 읽다 말고 나가면 자식이
                #    write 에서 막힌 채 남는다. 아래 wait() 뒤에 던진다.
                if m["run_id"] != run_id:
                    mismatch = (f"적재기가 다른 run_id 로 넣었습니다: "
                                f"기대 {run_id!r} ≠ 실제 {m['run_id']!r} "
                                f"(table={m['table']})")
                else:
                    proc.loaded[m["table"]] = int(m["rows"])
            elif (m := _CASCADED_RE.match(s)) and m["run_id"] == run_id:
                # run_id 가 어긋나면 위 `[LOADED]` 대조가 어차피 잡는다.
                # 여기서 또 던지면 같은 사실을 두 곳에서 판정하게 된다.
                for kv in m["pairs"].split():
                    k, _, v = kv.partition("=")
                    if v.isdigit():
                        proc.cascaded[k] = int(v)
            for sid, marker in proc.markers.items():
                if sid != cur and s.startswith(marker):
                    _step(doc, cur).update(
                        status="done", sec=round(time.perf_counter() - started, 2))
                    cur = sid
                    _step(doc, cur)["status"] = "running"
                    started = time.perf_counter()
                    _refresh_artifacts(doc)
                    _write_status(run_id, doc)
                    break
        log.flush()
        rc = child.wait()
    finally:
        # 자식이 어떻게 끝났든(정상·실패·취소·러너 예외) 장부에서 뺀다.
        # 안 빼면 죽은 프로세스 핸들이 남아, 나중 취소가 이미 없는 자식을
        # 죽이려 든다 — 그때 `terminate()` 가 남의 PID 를 칠 수 있다.
        with _LOCK:
            _CHILDREN.pop(run_id, None)

    # 🔴 종료 코드보다 **취소 여부를 먼저** 본다. 취소로 죽은 자식은 rc≠0 이라
    #    여기를 안 지나면 「종료 코드 -15」가 실패 사유로 적힌다 — 사용자가 누른
    #    취소가 파이프라인 오류로 기록되는 것이다(원칙 4).
    if _is_cancelled(run_id):
        raise _Cancelled()

    if rc != 0 or mismatch:
        if cur:
            _step(doc, cur)["status"] = "failed"
        _refresh_artifacts(doc)
        _write_status(run_id, doc)
        raise _StepFailed(mismatch or _fail_reason(tail, rc))

    if cur:
        _step(doc, cur).update(status="done",
                               sec=round(time.perf_counter() - started, 2))
    # 마커를 못 본 나머지 단계 — 프로세스는 정상 종료했으니 done 이다.
    # 다만 **소요 시간은 지어내지 않는다**(sec=null). 원칙 4.
    for sid in proc.step_ids:
        if _step(doc, sid)["status"] == "idle":
            _step(doc, sid)["status"] = "done"
    _refresh_artifacts(doc)
    _write_status(run_id, doc)

