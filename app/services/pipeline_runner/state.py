# -*- coding: utf-8 -*-
"""러너의 **바닥** — 상수·잠금·전역 장부·경로 헬퍼·예외.

여기 있는 것은 전부 **다른 모든 서브모듈이 읽는다.** 그래서 이 파일은
같은 패키지 안 어느 것도 import 하지 않는다(그래야 사이클이 안 생긴다).

🔴 `RUNS_ROOT` 는 대조기가 갈아끼우는 이름이다(`R.RUNS_ROOT = tmp`).
   패키지 `__init__` 의 프록시가 **이 모듈의 전역을 직접** 고쳐 준다 —
   `from .state import RUNS_ROOT` 로 값을 복사해 간 모듈들도 같이 고친다.
   자세한 것은 `__init__.py` 의 `_Proxy` 주석.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from app.config import BASE_DIR

_log = logging.getLogger(__name__)

RUNS_ROOT = Path(BASE_DIR) / "runs"
# 발급한 run 번호의 최고수위 원장. 이름이 `r_<날짜>_*` 와 안 겹쳐야 한다 —
# 겹치면 자기 자신을 run 폴더로 세게 된다.
_SEQ_PATH = RUNS_ROOT / "run_seq.json"
SERVICES_DIR = Path(BASE_DIR) / "app" / "services"
SCRIPTS_DIR = Path(BASE_DIR) / "scripts"

MODE_FIXTURE = "fixture"
# 게이트 모드. 파이프라인이 **원래 갖고 있던** 사람 확정 지점에서 멈춘다.
#   fixture 는 무입력 완주(회귀 검증용)라 게이트가 없어야 한다 — 사람 입력이 끼는 순간
#   check_fixture 57/57 이 재현 불가가 된다. 두 모드를 섞지 않는 이유가 그것이다.
MODE_HITL = "hitl"
# 업로드한 도메인을 **STEP0(프로파일링)부터** 도는 모드 (2026-08-10 신설, 사람 결정).
#   fixture·hitl 은 둘 다 `<도메인>_FIX/` 가 있어야 시작한다 — 즉 **업로드로 만든
#   도메인은 API 로 한 단계도 못 돌았다.** 화면1(업로드)→화면2(감리확인)→화면3(가중치)
#   가 이어지려면 STEP0·STEP1 이 계획 안에 있어야 한다.
#   막혀 있던 건 코드가 아니라 **값의 출처**였다(`_FULL_COND` 주석 참조).
MODE_FULL = "full"
MODES = (MODE_FIXTURE, MODE_HITL, MODE_FULL)

# 서버가 뜬 시각. 이전 서버 프로세스가 남긴 'running' 을 구분하는 데 쓴다(reap_orphans).
#
# 🔴 **초 단위로 자른다.** `started_at` 이 `isoformat(timespec="seconds")` 로 기록되기
#    때문이다. 자르지 않으면 부팅과 같은 초에 시작된 run 은
#    `started_at`(초 절삭) < `_SERVER_BOOT`(마이크로초 포함) 이 되어 **방금 만든 run 을
#    '이전 서버가 남긴 고아'로 판정**한다. 그러면 돌고 있는 run 이 failed 로 닫히고,
#    그 상태 파일을 실행 스레드가 동시에 쓰다가 Windows 에서 os.replace 가 터진다.
#    (2026-08-05 실측 — 러너를 in-process 로 부르는 검증 스크립트에서 재현됐다.
#     uvicorn 은 기동과 첫 요청 사이가 벌어져 있어 지금까지 안 드러났을 뿐이다)
_SERVER_BOOT = datetime.now().replace(microsecond=0)

_LOCK = threading.Lock()
# status.json 쓰기 직렬화. 실행 스레드·부팅정리(reap_orphans)·게이트 답변이 동시에 쓴다.
# Windows 의 os.replace 는 대상이 열려 있으면 PermissionError 로 터진다.
_IO_LOCK = threading.Lock()
# 🔴 위 락으로 **부족하다.** 읽는 쪽은 락 밖이라 폴링이 status.json 을 열고 있으면
#    os.replace 가 거부된다(WinError 5). 읽기는 순간이니 짧게 재시도하면 넘어간다.
#    상한 7 × 25ms = 175ms — 그 안에 안 되면 일시적 경합이 아니므로 raise 한다.
_REPLACE_RETRIES = 8
_REPLACE_BACKOFF_S = 0.025
# 이 서버 프로세스가 **지금** 돌리고 있는 것. domain -> run_id
#   409 판정을 파일이 아니라 이걸로 한다. 서버가 죽으면 비므로,
#   죽은 서버가 남긴 status.json 이 새 실행을 영원히 막지 않는다.
_ACTIVE: dict[str, str] = {}

# 취소(계약 3-4). 장부가 **셋** 필요하다 — 하나로는 부족하다.
#   _CANCELLED : 취소가 접수된 run_id. 실행 스레드가 다음 갈림길에서 이걸 보고 멈춘다.
#   _CHILDREN  : 지금 돌고 있는 자식 프로세스(run_id → Popen).
#                🔴 이게 없으면 취소는 status.json 만 고쳐 쓰고 **자식은 계속 돈다** —
#                   화면은 멈췄다는데 정본 캐시·산출물 디렉터리는 계속 갈린다(원칙 4).
#                   자식 핸들은 여기 말고 어디에도 남지 않으므로 죽일 방법이 없어진다.
#   _WORKERS   : 실행 스레드(run_id → Thread). 취소가 **뒷정리를 직접 해야 하는지**
#                (게이트 대기·서버 재시작처럼 스레드가 이미 없는 경우) 아니면
#                **스레드에 맡기고 기다릴지**를 추측하지 않고 `is_alive()` 로 정한다.
_CANCELLED: set[str] = set()
_CHILDREN: dict[str, subprocess.Popen] = {}
_WORKERS: dict[str, threading.Thread] = {}

# 취소된 run 의 `error`. 실패 사유와 **같은 자리**에 적는다 — 취소도 종료 사유다.
_CANCEL_MSG = "사용자가 실행을 취소했습니다."
# 자식에게 terminate 를 주고 기다리는 유예. 지나면 kill 한다.
_TERM_GRACE_S = 5.0
# 취소 요청이 실행 스레드의 뒷정리(= `_ACTIVE` 반납)를 기다리는 상한.
# 🔴 여기서 안 기다리면 204 를 받은 프런트가 곧바로 같은 도메인을 다시 돌리려다
#    409 를 맞는다 — 「초기화」가 초기화가 아니게 된다.
_CANCEL_JOIN_S = 20.0
# 아직 끝나지 않은 run. 이 셋만 취소 대상이다.
_LIVE_STATUSES = ("queued", "running", "awaiting_hitl")


# ══════════════════════════════════════════════════════════════════
# 예외 — 라우터가 HTTP 코드로 옮긴다
# ══════════════════════════════════════════════════════════════════
class RunRequestError(Exception):
    """요청이 잘못됐다 → 400"""


class RunConflict(Exception):
    """같은 도메인이 이미 돌고 있다 → 409"""



# ══════════════════════════════════════════════════════════════════
# 3. 경로·상태 파일
# ══════════════════════════════════════════════════════════════════
def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_dir(run_id: str) -> Path:
    return RUNS_ROOT / run_id


def _status_path(run_id: str) -> Path:
    return run_dir(run_id) / "status.json"



# ══════════════════════════════════════════════════════════════════
# 실행 중 예외 — 러너 내부에서만 쓴다(라우터로 안 나간다)
# ══════════════════════════════════════════════════════════════════

class _StepFailed(Exception):
    pass


class _Cancelled(Exception):
    """사용자 취소. `_StepFailed` 와 나누는 이유는 **사유가 다르기 때문**이다 —
    파이프라인이 틀린 게 아니라 사람이 그만두게 한 것이고, 그 둘을 한 예외로 묶으면
    자식의 종료 코드(-15 등)가 실패 사유로 적힌다."""
