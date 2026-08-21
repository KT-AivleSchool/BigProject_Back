# -*- coding: utf-8 -*-
"""픽스처 재실행 러너 (STEP2~4) — 기존 CLI 를 subprocess 로 그대로 부른다.

계약
  `pipeline_run_contract.md` 가 유일한 기준이다. 엔드포인트·status.json 스키마·
  필드명·값이 전부 거기 있다. 여기서 임의로 바꾸지 않는다.

왜 subprocess 인가 (프로세스 안 import 가 아니라)
  `from app.config import STEP2_OUTPUT_DIR` 는 **import 시점 바인딩**이다. 같은
  프로세스 안에서 run 마다 출력 경로를 가르려면 호출부 30곳을 리팩터링해야 한다.
  CLI 를 그대로 부르면 그 리팩터링이 필요 없고, **CLI 와 API 가 같은 코드를 타므로
  두 경로가 갈릴 수 없다.**

왜 커맨드 조립이 한 곳뿐인가
  나중에 오케스트레이터로 교체할 때 라우터를 건드리지 않기 위해서다.
  지금 그 한 곳은 `commands.py` 다.

🔴 커맨드 값의 출처 — 하드코딩 금지(원칙 2)
  `--facility`·`--region`·`--radius`·`--spacing` 은 전부 **도메인 값**이다.
  여기에 박으면 도메인이 바뀔 때 조용히 틀린다. 그래서 전부 픽스처에서 읽는다:
    · facility·region  → `<도메인>_FIX/reviewed.json`  (make_parcel_candidates 가 스스로 읽음)
    · 반경             → `<도메인>_FIX/기준값.json` 의 `STEP3_가중치[*].radius_m`
    · decay·scale·spacing·alpha·candidates → 같은 파일의 `조건`
  즉 이 패키지에는 도메인 값이 하나도 없다. 픽스처가 곧 실행 조건이다.

────────────────────────────────────────────────────────────────────────────
패키지 구성 (2026-08-18 분할. 그 전에는 `pipeline_runner.py` 2,580행 한 파일)

  state       상수·잠금·전역 장부·경로 헬퍼·예외      ← 아무것도 import 안 한다
  steps       단계 목록·산출물 화이트리스트·실행 계획
  conditions  실행 조건의 출처(픽스처·full 선언값·params.json)
  status      status.json 쓰기/읽기/고아 정리 + 상태 문서 조립
  commands    커맨드 조립 — **여기 한 곳뿐이다**
  prepare     run 준비(폴더·번호 발급·자식 환경)
  gates       HITL 게이트 — 질문을 만드는 쪽
  artifacts   산출물 경로 해석
  answers     HITL 게이트 — 답을 받는 쪽
  runner      실행(접수·취소·스레드·단계 진행)
  logs        실행 로그 마스킹

import 순서는 위 그대로다. 유일한 역방향은 `answers.submit_gate → runner._spawn`
하나이고 **함수 안에서** 부른다(그 자리에 이유를 적어뒀다).

🔴 **분할해도 밖에서 보는 이름은 하나도 안 바뀐다.** `import app.services.pipeline_runner
   as R` 로 쓰던 대조기 9종·라우터가 그대로 돌아야 한다 — 그래서 아래에서 전부
   다시 내보내고, 맨 끝에 `_Proxy` 를 건다.
"""
from __future__ import annotations

import json  # noqa: F401
import logging  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import shutil  # noqa: F401
import subprocess  # noqa: F401
import sys
import threading  # noqa: F401
import time  # noqa: F401
import types
from datetime import datetime  # noqa: F401
from pathlib import Path  # noqa: F401

# 🔴 이 여섯도 **밖에서 보는 이름**이다. 한 파일이던 시절 `R.USER_INPUT_ROOT` 로
#    읽던 곳이 실제로 있다(`app/services/user_input_pruner.py:140`). 서브모듈로
#    흩어졌다고 패키지에서 사라지면 그 줄이 `AttributeError` 다.
from app.config import (  # noqa: F401
    BASE_DIR,
    DATA_ROOT,
    DOMAIN_ROOT,
    USER_INPUT_ROOT,
    domain_prefix,
    settings,
)

from . import (
    answers,
    artifacts,
    commands,
    conditions,
    gates,
    logs,
    prepare,
    runner,
    state,
    status,
    steps,
)

# ── state ───────────────────────────────────────────────────────────────
from .state import (  # noqa: F401
    MODE_FIXTURE,
    MODE_FULL,
    MODE_HITL,
    MODES,
    RUNS_ROOT,
    SCRIPTS_DIR,
    SERVICES_DIR,
    _ACTIVE,
    _CANCEL_JOIN_S,
    _CANCEL_MSG,
    _CANCELLED,
    _CHILDREN,
    _Cancelled,
    _IO_LOCK,
    _LIVE_STATUSES,
    _LOCK,
    _REPLACE_BACKOFF_S,
    _REPLACE_RETRIES,
    _SEQ_PATH,
    _SERVER_BOOT,
    _StepFailed,
    _TERM_GRACE_S,
    _WORKERS,
    _log,
    _now_iso,
    _status_path,
    RunConflict,
    RunRequestError,
    run_dir,
)

# ── steps ───────────────────────────────────────────────────────────────
from .steps import (  # noqa: F401
    ARTIFACTS,
    AUTO_APPROVE_SRC,
    GATE_IDS,
    STEP_LABELS,
    _CLEAN_NAME_RE,
    _GAM4_MARKERS,
    _LOAD_LABELS,
    _PLAN,
    _RUNPIPE_MARKERS,
    _STEP_LABELS_FULL,
    _STEP_LABELS_WITH_LOAD,
    _resume_index,
    step_labels,
)

# ── conditions ──────────────────────────────────────────────────────────
from .conditions import (  # noqa: F401
    TOPN_DEFAULT,
    TOPN_MAX,
    _FULL_COND,
    _fixture_dir,
    _full_conditions,
    _load_conditions,
    _load_fixture,
    _params_path,
    _radius_arg,
    _read_params,
    _write_params,
)

# ── status ──────────────────────────────────────────────────────────────
from .status import (  # noqa: F401
    _artifact_url,
    _new_status,
    _refresh_artifacts,
    _step,
    _write_status,
    loaded_record,
    note_run_record_error,
    read_status,
    reap_orphans,
)

# ── commands ────────────────────────────────────────────────────────────
from .commands import (  # noqa: F401
    _CASCADED_RE,
    _LOADED_RE,
    _Proc,
    _proc_load_audit,
    _proc_load_topn,
    _proc_of,
    _proc_propose,
    _proc_runpipe,
    _python_exe,
    _svc,
    _weight_args,
    build_commands,
    fixture_blocker,
)

# ── prepare ─────────────────────────────────────────────────────────────
from .prepare import (  # noqa: F401
    _child_env,
    _domain_root,
    _new_run_id,
    _prepare_dirs,
    _read_seq_ledger,
    _validate_domain,
    _write_seq_ledger,
)

# ── gates ───────────────────────────────────────────────────────────────
from .gates import (  # noqa: F401
    _answer_path,
    _hitl_dir,
    _proposal_path,
    _questions_audit,
    _questions_weight,
    _read_answer,
    _reviewed_path,
    _save_answer,
    _seed_reviewed,
    _source_geometry,
    build_gate,
)

# ── artifacts ───────────────────────────────────────────────────────────
from .artifacts import artifact_path  # noqa: F401

# ── answers ─────────────────────────────────────────────────────────────
from .answers import (  # noqa: F401
    _apply_audit,
    _auto_answer,
    _drop_exclusion,
    _exclusion_flag,
    _int_in,
    _num_in,
    _only_keys,
    _q,
    _run_auto_gate,
    _stage_args,
    _validate_weight,
    submit_gate,
)

# ── runner ──────────────────────────────────────────────────────────────
from .runner import (  # noqa: F401
    _assert_provenance,
    _close_cancelled,
    _execute,
    _fail_reason,
    _is_cancelled,
    _run_one,
    _spawn,
    _terminate,
    _validate_full_params,
    cancel_run,
    start_run,
)

# ── logs ────────────────────────────────────────────────────────────────
from .logs import (  # noqa: F401
    _QUERY_SECRET_RE,
    _SECRET_NAME_RE,
    _path_re,
    _scrub,
    read_log,
)

# `app.services.run_records` 를 이름으로 들고 있는다 — 대조기가 `R.run_records = 가짜`
# 로 갈아끼운다(`check_run_records.py:252`). 아래 프록시가 이 이름을 갖고 있는
# 서브모듈(`status`·`runner`) 전부에 전파한다.
from app.services import run_records  # noqa: F401,E402

_SUBMODULES: tuple[types.ModuleType, ...] = (
    state,
    steps,
    conditions,
    status,
    commands,
    prepare,
    gates,
    artifacts,
    answers,
    runner,
    logs,
)


class _Proxy(types.ModuleType):
    """`R.<이름> = 값` 을 **소유 서브모듈의 전역까지** 밀어 넣는다.

    왜 필요한가 — 파이썬에서 `from .state import RUNS_ROOT` 는 **값을 복사**한다.
    한 파일이던 시절 `R.RUNS_ROOT = tmp` 는 그 파일의 전역 하나를 고쳤고 모든
    함수가 즉시 새 값을 봤다. 패키지로 가르면 `R.RUNS_ROOT = tmp` 는 **패키지
    객체의 attr 하나**만 고치고 서브모듈들은 옛 값을 계속 본다.

    🔴 이 어긋남은 **예외를 안 낸다.** 실측(2026-08-18):
           패키지 attr : /tmp/fake
           run_dir()   : \\real\\runs\\r_1      ← 진짜 폴더
       대조기가 초록불인 채로 진짜 `runs/` 를 건드린다 —
         · `check_prune_runs`      진짜 `runs/*/*.gpkg` 를 지운다
         · `check_run_records_e2e` `reap_orphans()` 가 **남이 돌리는 run** 을 닫는다
         · `check_cancel_run`      진짜 run 폴더에 status 를 쓴다
       「대조기가 없는 키를 읽음」의 사촌이다. 가짜 초록불은 회귀보다 나쁘다.

    왜 대조기를 고치지 않고 프록시를 두는가 (사람 결정 2026-08-18)
      대조기 9종은 **회귀망 그 자체**다. 분할하면서 회귀망을 같이 고치면
      「분할이 안전한가」를 재는 자와 재어지는 자가 같이 움직인다.
      수정 0건으로 두고, 그 대신 이 마법을 지키는 대조기를 따로 둔다
      (`app/tools/check_runner_pkg_proxy.py`).

    🔴 모호하면 **터진다**(원칙 1). 같은 이름을 두 서브모듈이 **서로 다른 것**으로
       들고 있으면 어느 쪽을 고쳐야 하는지 알 수 없다 — 추측해서 하나만 고치면
       나머지 하나가 조용히 옛 값으로 돈다. 그래서 `raise` 한다.
       (같은 객체를 여러 서브모듈이 import 해 갖고 있는 것은 모호가 아니다 —
        그게 정상이고, 그때는 **전부** 고친다.)
    """

    def __setattr__(self, name: str, value) -> None:
        owners = [m for m in _SUBMODULES if name in m.__dict__]
        if len(owners) > 1:
            ids = {id(m.__dict__[name]) for m in owners}
            if len(ids) > 1:
                raise RuntimeError(
                    f"'{name}' 을 여러 서브모듈이 **서로 다른 것**으로 들고 있습니다: "
                    f"{[m.__name__.rsplit('.', 1)[-1] for m in owners]}. "
                    f"어느 쪽을 갈아끼워야 하는지 알 수 없어 멈춥니다 — "
                    f"이름을 가르거나 소유 서브모듈을 직접 지정하세요."
                )
        for m in owners:
            m.__dict__[name] = value
        super().__setattr__(name, value)


# 🔴 **맨 끝이어야 한다.** 이 줄 위쪽의 `from … import` 들도 결국 이 모듈에 대한
#    attr 대입이라, 프록시를 먼저 걸면 import 하는 내내 전파가 돈다(느리고, 아직
#    `_SUBMODULES` 가 없어 `NameError` 다).
sys.modules[__name__].__class__ = _Proxy
