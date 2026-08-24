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

왜 커맨드 조립이 이 파일 한 곳뿐인가
  나중에 오케스트레이터로 교체할 때 라우터를 건드리지 않기 위해서다.

🔴 커맨드 값의 출처 — 하드코딩 금지(원칙 2)
  `--facility`·`--region`·`--radius`·`--spacing` 은 전부 **도메인 값**이다.
  여기에 박으면 도메인이 바뀔 때 조용히 틀린다. 그래서 전부 픽스처에서 읽는다:
    · facility·region  → `<도메인>_FIX/reviewed.json`  (make_parcel_candidates 가 스스로 읽음)
    · 반경             → `<도메인>_FIX/기준값.json` 의 `STEP3_가중치[*].radius_m`
    · decay·scale·spacing·alpha·candidates → 같은 파일의 `조건`
  즉 이 파일에는 도메인 값이 하나도 없다. 픽스처가 곧 실행 조건이다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.config import (
    BASE_DIR,
    DATA_ROOT,
    DOMAIN_ROOT,
    USER_INPUT_ROOT,
    domain_prefix,
    settings,
)
from app.services import run_records

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

# ── 게이트 만료 (awaiting_hitl 자동 종료) ─────────────────────────────
# 🔴 `awaiting_hitl` 은 **어떤 정리기도 못 닫는다.** `reap_orphans` 는
#    `("queued","running")` 만 보고, 게이트 대기 중에는 실행 스레드가 이미
#    끝나 있어 닫아줄 주체도 없다. 그래서 사람이 답을 안 하고 떠난 run 하나가
#    ⓐ 그 도메인을 **영구히 409** 로 만들고 ⓑ `user_input_pruner` 의
#    `LIVE_STATUSES` 에 걸려 업로드 폴더 삭제를 **영구히 409** 로 막는다
#    (2026-08-16 실측: `r_20260814_008`). 재시작해도 안 풀린다.
# 사유 문구는 취소와 **다르다** — 사람이 취소한 게 아니다(원칙 4).
_HITL_TIMEOUT_MSG = (
    "게이트 대기 제한시간({hours}시간)이 지나 자동 종료했습니다"
    "(게이트: {gate}, 대기 시작: {since})."
)
_OFF_WORDS = ("0", "false", "no", "off")


def _env_int_pos(name: str, default: int) -> int:
    """양의 정수 환경변수. 못 읽으면 raise — 조용히 기본값으로 넘어가지 않는다.

    ⚠ `user_input_pruner._env_int` 와 같은 모양이지만 **가져다 쓰지 않는다** —
       그쪽이 이 모듈을 import 하므로 순환이 된다.
    """
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


def hitl_timeout_hours() -> int:
    return _env_int_pos("OMNISITE_HITL_TIMEOUT_HOURS", 1)


def hitl_sweep_interval_sec() -> int:
    return _env_int_pos("OMNISITE_HITL_SWEEP_SEC", 300)


def hitl_sweep_enabled() -> bool:
    return os.environ.get("OMNISITE_HITL_SWEEP", "1").strip().lower() not in _OFF_WORDS


# ══════════════════════════════════════════════════════════════════
# 예외 — 라우터가 HTTP 코드로 옮긴다
# ══════════════════════════════════════════════════════════════════
class RunRequestError(Exception):
    """요청이 잘못됐다 → 400"""


class RunConflict(Exception):
    """같은 도메인이 이미 돌고 있다 → 409"""


# ══════════════════════════════════════════════════════════════════
# 1. 단계 정의  (계약 2절 — id·개수 고정, label 은 2026-08-04 실측으로 확정)
# ══════════════════════════════════════════════════════════════════
# 실행 단위는 **프로세스 4개**다. 6단계와 1:1 이 아니다:
#   · `2`·`3-1`·`3-2` 는 각각 프로세스 하나 = 경계가 확실하다.
#   · `4-1`·`4-2`·`4-3` 은 gam4_site_select.py **한 프로세스 안**이라
#     stdout 마커로만 나뉜다. 마커는 계약이 아니다 — 문구가 바뀌면 못 본다.
#     그래서 못 봐도 프로세스가 정상 종료하면 done 으로 닫되,
#     **소요 시간은 지어내지 않고 null 로 둔다**(원칙 4).
STEP_LABELS: list[tuple[str, str]] = [
    ("2", "정제"),
    ("3-1", "후보 필지 생성"),
    ("3-2", "가중치 산정"),
    ("4-1", "후보점 생성"),
    ("4-2", "점수화·배제 적용"),
    ("4-3", "위치 선정"),
]

# gam4 내부 단계의 시작을 알리는 stdout 마커 (gam4_site_select.py 의 print 문구)
_GAM4_MARKERS: dict[str, str] = {
    "4-1": "[B] 후보점 생성",
    "4-2": "[C] 지표 정의·부착",
    "4-3": "[H] 선정",
}

# 🔴 적재가 **두 칸**인 이유 — 화면5 로 넘어가려면 다리가 둘 다 있어야 한다.
#    후보점(`booth_candidates`)만 넣으면 목록은 뜨는데 토론이 첫 줄에서 죽는다:
#    `_select_audit_rules` 가 읽을 `audit_rules` 가 그 도메인에 없기 때문이다
#    (2026-08-10 실측 — `r_20260810_001` 이 여기서 막혔다. 다행히 조용히 죽지 않고
#    "적재된 (도메인, 시설)" 을 세어 알려줬다).
#    한 칸에 두 프로세스를 넣지 않는다 — 어느 쪽이 실패했는지 진행 표시에서 사라진다.
_LOAD_LABELS: list[tuple[str, str]] = [
    ("적재-감리", "감리 규칙 DB 적재 (토론 근거)"),
    ("적재-후보", "후보점 DB 적재 (화면5 목록)"),
]

# full 모드는 앞에 STEP0·STEP1 두 칸이 더 붙는다.
#   🔴 이 둘을 **모든 모드에 같이 두지 않는다.** fixture 는 STEP1 을 안 돌리므로
#      영원히 idle 인 단계가 화면에 남는다 — 진행률이 거짓말을 한다(원칙 4).
#      그래서 단계 목록은 mode 에서 유도한다(`step_labels`).
_STEP_LABELS_FULL: list[tuple[str, str]] = [
    ("0", "프로파일링 · 시설/지역 확정"),
    ("1", "감리 판정 · 상위법 검색"),
] + STEP_LABELS + _LOAD_LABELS

# 🔴 fixture 도 적재한다 (2026-08-11, 사람 결정). 예전엔 full 에만 있었고 사유는
#    "fixture 는 정본 산출물의 재생이고 그 Top-N 은 이미 `run_id='정본'` 으로 DB 에
#    있다" 였다 — 맞는 말이지만, 그래서 **fixture run 의 결과는 화면5 에서 볼 수가
#    없었다.** 시연에서 업로드를 건너뛰고 화면5까지 가려면 이 두 칸이 있어야 한다.
#    누적 우려(그때의 반대 근거)는 `runs/` 정리 정책 쪽에서 받는다 — `run_pruner`.
#    🔴 hitl 도 같은 날 붙였다(사람 지시). 처음엔 뺐고 이유는 "게이트에서 사람을
#       기다리므로 시연 프리셋이 아니다" 였는데, 그건 **왜 fixture 에 넣는가**의
#       답이지 **왜 hitl 에서 빼는가**의 답이 아니다. 게이트를 지나 완주한 run 은
#       사람이 값을 확정한 run 이다 — 그 결과를 화면5 에서 못 보는 건 똑같은 구멍이다.
_STEP_LABELS_WITH_LOAD: list[tuple[str, str]] = STEP_LABELS + _LOAD_LABELS

# gam2_run_pipeline.py 의 `_step()` 이 찍는 구분선 머리글.
#   "▶ STEP 0.5 시설·지역 확정" 은 마커에 **일부러 없다** — 뒤의 공백 하나로
#   "▶ STEP 0 " 과 갈린다. 0.5 를 단계로 세면 계약의 단계 수가 또 늘어난다.
_RUNPIPE_MARKERS: dict[str, str] = {
    "0": "▶ STEP 0 ",
    "1": "▶ STEP 1 ",
}


def step_labels(mode: str) -> list[tuple[str, str]]:
    # 지금은 세 모드 다 적재 칸을 갖는다. 그래도 mode 로 유도하는 구조는 유지한다 —
    # 모드가 늘거나 한 모드에서 칸이 빠질 때 볼 곳이 여기 하나여야 한다.
    if mode == MODE_FULL:
        return _STEP_LABELS_FULL
    return _STEP_LABELS_WITH_LOAD


# ══════════════════════════════════════════════════════════════════
# 2. 산출물 화이트리스트 (계약 1절)
#    🔴 `name` 을 경로로 쓰지 않는다. 여기 매핑을 통해서만 파일에 닿는다.
# ══════════════════════════════════════════════════════════════════
#   (step 폴더, 프리픽스 뒤에 붙는 파일명)
ARTIFACTS: dict[str, tuple[str, str]] = {
    # 이것만 단계가 만드는 게 아니라 `_prepare_dirs` 가 픽스처에서 복사해 넣는다.
    # 그래서 run 생성 직후부터 200 이다. 정본 step1_output/ 이 아니라 **run 안의
    # 사본**을 가리켜야 한다 — 정본을 가리키면 run 격리가 깨진다.
    "reviewed": ("step1", "_audit_result_reviewed.json"),
    # 화면2 STEP1「선정 대상」. `reviewed.facility_inference` 와 **같은 값**인데
    # 나오는 시점이 다르다 — 이건 STEP 0.5 직후(칸 "0", 실측 ~15초)이고 저건 감리
    # (실측 238초) 뒤다. 화면이 시설·지역 한 줄 때문에 감리를 기다릴 이유가 없다.
    # 🔴 `full` 에만 생긴다. fixture·hitl 은 STEP0-1 을 안 돌아 **항상 null** 이므로
    #    프런트는 반드시 `reviewed.facility_inference` 로 되짚을 것.
    "facility": ("step1", "_facility_inference.json"),
    "clean_report": ("step2", "_clean_report.json"),
    "candidates": ("step3", "_후보_지적도필지.gpkg"),
    "weight_set": ("step3", "_weight_set.json"),
    "report": ("step4", "_report.json"),
    "topN": ("step4", "_topN_min.csv"),
    "score_grid": ("step4", "_score_grid.json"),
    # 화면2b「최종 판정」. S9 점/면 판정 결과가 레이어(dataset_id)별로 들어 있고
    # `type`(최종) · `type_llm`(LLM 제안) · `type_source` 를 **같이** 실어 보낸다 —
    # 규약("값마다 누가 정했는지 남긴다")이 산출물에 그대로 드러나는 유일한 파일이다.
    # 이게 없으면 프런트는 최종 판정을 report.json 에서 **유추**해야 한다(원칙 5 위반).
    "exclusion": ("step4", "_exclusion.geojson"),
}

# 정제 산출물은 데이터셋마다 확장자가 다르다(gpkg / parquet). 이름으로 추측하지 않고
# clean_report.json 의 `output` 을 읽어 확정한다. 이름 형식: clean_01 … clean_11
_CLEAN_NAME_RE = re.compile(r"^clean_(\d{2})$")


# ══════════════════════════════════════════════════════════════════
# 3. 경로·상태 파일
# ══════════════════════════════════════════════════════════════════
def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_dir(run_id: str) -> Path:
    return RUNS_ROOT / run_id


def _status_path(run_id: str) -> Path:
    return run_dir(run_id) / "status.json"


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


# ══════════════════════════════════════════════════════════════════
# 5. 커맨드 조립 — **여기 한 곳뿐이다**
# ══════════════════════════════════════════════════════════════════
class _Proc:
    """프로세스 하나와 그것이 담당하는 단계들."""

    def __init__(self, step_ids: tuple[str, ...], argv: list[str],
                 markers: dict[str, str] | None = None):
        self.step_ids = step_ids
        self.argv = argv
        self.markers = markers or {}
        # 적재 프로세스가 `[LOADED] …` 로 알려준 결과. 안 알려주면 빈 dict 다.
        self.loaded: dict[str, int] = {}
        # `[CASCADED] …` — 덮어쓰면서 **딸려 나간 것**. 0 도 기록한다(빈 dict 와 다르다).
        self.cascaded: dict[str, int] = {}


# DB 적재 스크립트가 마지막에 찍는 **약속된 줄**.
#   `[LOADED] table=audit_rules run_id=r_20260810_006 rows=13`
#
# 🔴 왜 자식 stdout 을 읽나 — 행 수를 아는 건 적재기뿐이다. 러너가 DB 에 다시
#    물어보면 **적재 이후에 다른 실행이 건드렸을 수도 있는 값**을 이 run 의
#    성과로 기록하게 된다. 그건 사실이 아니다(원칙 4·5).
#    사람용 로그를 긁는 게 아니라 소비자가 선언한 한 줄을 읽는다 —
#    `scripts/load_{audit_data,topn_candidates}.py` 의 마지막 print 와 짝이다.
_LOADED_RE = re.compile(
    r"^\[LOADED\]\s+table=(?P<table>\w+)\s+run_id=(?P<run_id>\S+)\s+rows=(?P<rows>\d+)\s*$"
)

# 덮어쓰기로 **딸려 나간 것**을 알리는 줄. 같은 이유로 자식이 선언한다 —
# 지운 뒤엔 셀 방법이 없고, 콘솔 출력은 사라진다.
#   `[CASCADED] table=booth_candidates run_id=… hearing_result_a=1 debate_logs=14 …`
# 🔴 값이 0 이어도 자식이 찍는다. 줄이 **없는** 것은 「딸려 나간 게 없다」가 아니라
#    「그 적재기가 세지 않았다」다 — status 에서도 둘을 섞지 않는다(원칙 4).
_CASCADED_RE = re.compile(
    r"^\[CASCADED\]\s+table=(?P<table>\w+)\s+run_id=(?P<run_id>\S+)\s+(?P<pairs>.+?)\s*$"
)


def _python_exe() -> str:
    """파이프라인을 돌릴 인터프리터.

    🔴 `sys.executable` 은 **API 서버의** 파이썬이다. 파이프라인이 요구하는
      geopandas·shapely·pyarrow 가 그 환경에 없을 수 있다(2026-08-04 실측:
      이 저장소의 파이프라인 환경 Python314 에는 fastapi 가 없고, fastapi 가 있는
      환경에는 geopandas 가 없다). 서버와 파이프라인이 다른 환경이면
      `OMNISITE_PYTHON` 으로 지정한다. 지정한 경로가 없으면 즉시 400 —
      실행을 시작해 놓고 ModuleNotFoundError 로 죽게 두지 않는다.
    """
    exe = os.environ.get("OMNISITE_PYTHON")
    if not exe:
        return sys.executable
    if not Path(exe).is_file():
        raise RunRequestError(f"OMNISITE_PYTHON 경로에 파일이 없습니다: {exe}")
    return exe


def _svc(name: str) -> str:
    return str(SERVICES_DIR / name)


def _proc_load_topn(domain: str, run_id: str) -> _Proc:
    """STEP4 산출물 → `booth_candidates` 적재. **화면4 와 화면5 사이의 유일한 다리다.**

    🔴 왜 러너가 부르나 — 이걸 사람이 손으로 돌리게 두면 full 모드는 화면4 에서
       끝난다. 화면5 는 `GET /simulations/candidates` 로 목록을 받아 그중 하나를
       고르는데, 그 목록의 출처가 이 테이블이기 때문이다. 다리가 CLI 한 줄이면
       프런트는 건널 방법이 없다.

    🔴 왜 세 모드 다 붙나 — 2026-08-11 까지는 **full 에만** 있었다. 그때 사유는
       "fixture 는 정본 산출물의 재생이고 그 Top-N 은 이미 `run_id='정본'` 으로 DB 에
       있다(2026-08-10 어휘 통일 전에는 STEP 폴더 이름 `'step4_output'` 이었다).
       값이 같은 행을 run 마다 복제하는 건 적재가 아니라 누적이다" 였다.
       그 말은 맞았지만 **결론이 틀렸다** — 그래서 fixture run 의 결과는 화면5 에서
       볼 수가 없었다. `/candidates` 가 읽는 건 파일이 아니라 이 테이블이고,
       run 폴더에 topN.geojson 이 있어도 프런트는 닿지 못한다.
       시연 프리셋(업로드 건너뛰고 화면5까지)이 필요해져 fixture 에도 붙였고,
       같은 날 hitl 에도 붙였다(사람 지시). hitl 을 뺐던 이유는 "게이트에서 사람을
       기다리므로 프리셋이 아니다" 였는데 그건 **왜 fixture 에 넣는가**의 답이지
       **왜 hitl 에서 빼는가**의 답이 아니다 — 게이트를 지나 완주한 run 은 사람이
       값을 확정한 run 이고, 그 결과를 화면5 에서 못 보는 건 똑같은 구멍이다.
       누적은 여기서 막는 게 아니라 `runs/` 정리 정책과 `/candidates` 의
       "최신 적재분만" 규칙이 받는다.

    적재기는 같은 `(domain, run_id)` 만 지우고 다시 넣는다 — 새 run_id 라 지울 게
    없고, 다른 도메인·정본 행은 안 건드린다. `--run` 을 주므로 시설명도 **이 run 의**
    reviewed.json 에서 읽는다(정본이 아니라).

    🔴 `_python_exe()` 가 **아니라** `sys.executable` 이다. 이건 파이프라인 스크립트가
       아니라 DB 스크립트다 — geopandas·shapely 를 안 쓰고 `psycopg`·`app.config` 만
       쓴다. 그 둘이 있는 건 서버 환경이지 `OMNISITE_PYTHON` 이 아니다(이 저장소는
       두 환경이 갈려 있다). 여기서 파이프라인 쪽을 부르면 마지막 칸에서
       ModuleNotFoundError 로 죽는다 — 다 돌린 뒤에.
    """
    return _Proc(("적재-후보",),
                 [sys.executable, str(SCRIPTS_DIR / "load_topn_candidates.py"),
                  domain, "--run", run_id, "--yes"])


def _proc_load_audit(domain: str, run_id: str) -> _Proc:
    """STEP1 확정본(reviewed) → `audit_rules` 적재. **화면5 토론의 근거다.**

    🔴 왜 후보점 적재만으로는 모자라나 — 화면5 는 두 테이블을 읽는다.
       `booth_candidates` 는 **어디를** 논의할지, `audit_rules` 는 **무엇을 근거로**
       논의할지다. 앞만 넣으면 `/candidates` 목록은 정상으로 보이는데 `/stream` 이
       첫 줄에서 멈춘다 — 프런트에는 "후보는 있는데 토론이 안 된다" 로 보인다.

    🔴 `reviewed` 를 쓴다. `audit_result.json` 은 LLM 제안값이고 게이트A 에서 사람이
       고친 값은 reviewed 에만 있다. `--run` 을 주므로 **이 run 의** reviewed 다 —
       정본(`step1_output/`)을 읽으면 남의 실행 결과를 근거로 토론하게 된다.

    적재기는 `(domain, run_id)` 단위로 교체하고, 읽는 쪽(`_select_audit_rules`)도
    `domain` 으로 거른다. 두 도메인이 같은 시설을 써도 근거가 섞이지 않는다.

    `sys.executable` 인 이유는 `_proc_load_topn` 과 같다 — DB 스크립트다.
    """
    return _Proc(("적재-감리",),
                 [sys.executable, str(SCRIPTS_DIR / "load_audit_data.py"),
                  domain, "--run", run_id])


def _weight_args(base: dict, radius: str, weight: str | None,
                 value_source: str) -> list[str]:
    """STEP3-2 공통 인자. 반경·가중치 **값만** 모드에 따라 갈린다.

    fixture 는 픽스처의 `radius_m` 을, hitl 은 사람이 게이트B 에서 준 값을 넣는다.
    나머지(alpha·decay·scale·candidates)는 두 모드가 같은 곳에서 읽는다 —
    갈라두면 "hitl 로 돌린 값이 픽스처와 왜 다른지"를 설명할 수 없게 된다.

    `value_source` 는 그 값을 **누가 정했는지**다. 자식 프로세스는 알 수 없다 —
    `--radius 07+02=150` 만 봐서는 픽스처 재생인지 사람 답인지 구분이 안 된다.
    """
    cond = base["조건"]
    argv = [
        "--candidates", cond["candidates"],
        "--alpha", str(cond["alpha"]),
        "--decay", cond["decay"]["func"],
        "--sigma-ratio", str(cond["decay"]["sigma_ratio"]),
        "--scale", cond["scale"],
        "--radius", radius,
        # --auto-weight 는 [W] 대화형 루프를 건너뛴다. 사람 답은 --weight 로 이미
        # 들어와 있다 — 게이트에서 받았지 자동으로 정한 게 아니다.
        # 그 사정을 산출물에 담는 건 --value-source 쪽이다.
        "--auto-weight",
        "--value-source", value_source,
    ]
    if weight:
        argv += ["--weight", weight]
    return argv


def build_commands(domain: str) -> list[_Proc]:
    """픽스처 재실행(STEP2~4) 커맨드. 값은 전부 픽스처에서 온다.

    CLAUDE.md 의 표준 CLI 와 다른 점 두 가지 — 둘 다 의도한 것이다:
      · `--auto-radius` 를 쓰지 않는다. 쓰면 `radius_conf["_confirmed"]` 가 안 찍힌다
        (run_weight_model.py:283). 픽스처는 `--radius` 로 고정한 실행이다.
      · `--no-diag --bootstrap 0` 을 쓰지 않는다. 픽스처가 기록한 실행 조건에 없다.
        (진단은 가중치와 무관하지만, 안 재본 것을 같다고 단정하지 않는다 — 원칙 5)
    """
    base, _ = _load_fixture(domain)
    return [_proc_of(s, domain, base) for s in ("2", "3-1", "3-2", "4")]


def fixture_blocker(domain: str) -> str | None:
    """`mode: "fixture"`·`"hitl"` 로 이 도메인을 돌릴 수 있는가.

    돌릴 수 있으면 `None`, 못 돌리면 **막는 이유**를 돌려준다.

    🔴 판정을 여기 두는 이유 — `start_run` 이 실제로 하는 사전검사(:820~821)와
       **같은 식**이어야 한다. 호출자가 "`<도메인>_FIX/` 폴더가 있는가" 로 따로
       판정하면 응답은 통과라 해놓고 실행이 400 으로 죽는다. 실제 조건은 폴더가
       아니라 **파일 둘**(`기준값.json`·`reviewed.json`)이고, 거기에 커맨드 조립까지
       성공해야 한다(`조건` 키가 없으면 `_proc_of` 에서 터진다).
       그래서 판정식을 복제하지 않고 **같은 함수를 부른다**.

    예외를 삼키지만 조용하지 않다 — 이유를 문자열로 **돌려준다**(원칙 1·4).
    실행 경로(`start_run`)는 여전히 raise 한다. 여기는 "물어보는" 자리다.
    """
    try:
        build_commands(domain)          # `_load_fixture` 포함
    except RunRequestError as e:
        return str(e)
    except Exception as e:              # 기준값.json 이 깨졌다 · `조건` 키가 없다 …
        return f"{type(e).__name__}: {e}"
    return None


def _proc_runpipe(domain: str, user_input: str) -> _Proc:
    """STEP0(프로파일링·시설/지역 확정) + STEP1(감리 판정·상위법 검색).

    `gam2_run_pipeline.py` 한 프로세스가 둘 다 담당한다 — CLI 와 같은 코드다.
    이 스크립트의 CLI 는 argparse 가 아니라 **위치인자 2개**(도메인, 사용자 입력)이고
    `--` 로 시작하는 토큰만 플래그로 본다. 그래서 사용자 입력이 `--` 로 시작하면
    조용히 플래그로 먹힌다 — `start_run` 이 미리 막는다.

    🔴 `--reprofile` 을 **항상** 넘긴다. 이 칸은 `full` 에만 있고, full 은 화면1 로
       올린 원본을 도는 모드다 — `fixture/profiles.json` 은 그 `data/` 의 사본이라
       원본이 바뀌면 같이 바뀌어야 한다. 없을 때만 만드는 기본 동작이면 낡은 사본이
       계속 이기고, 감리 AI 는 **지운 데이터셋을 보고 새로 올린 것을 못 본다**
       (2026-08-12 재활용 실측 — 예외가 안 나고 근거만 틀린다).
       조건부로 넘기지 않는다: "언제 다시 프로파일링하나" 를 러너가 판단하기
       시작하면 그 판단이 틀렸을 때 드러날 자리가 없다.
    """
    return _Proc(("0", "1"),
                 [_python_exe(), _svc("gam2_run_pipeline.py"), domain, user_input,
                  "--reprofile"],
                 markers=_RUNPIPE_MARKERS)


def _proc_of(stage: str, domain: str, base: dict,
             radius: str | None = None, weight: str | None = None,
             value_source: str | None = None) -> _Proc:
    """단계 하나의 커맨드. **조립은 여기 한 곳뿐이다.**

    fixture 와 hitl 이 같은 함수를 쓴다. 모드별로 따로 짜면 "픽스처는 되는데
    hitl 은 다른 값" 이 나오고, 그건 이 프로젝트가 반복해서 당한 유형이다.
    """
    py = _python_exe()
    cond = base["조건"]
    if stage == "2":
        # STEP2 정제. facility·region 은 reviewed.json 에서 스스로 읽는다.
        return _Proc(("2",), [py, _svc("gam2_clean_data.py"), domain])
    if stage == "3-1":
        # STEP3 후보 필지. --facility/--region 을 주지 않는다 —
        # _facility_of()/_region_of() 가 reviewed.json 에서 읽으므로 값이 같고,
        # 주면 그 순간 도메인 값이 러너에 박힌다.
        return _Proc(("3-1",), [py, _svc("make_parcel_candidates.py"), domain])
    if stage == "3-2":
        # 출처는 **모드 이름이 아니라 값을 어디서 가져왔는지**로 정한다.
        # `radius` 가 있다 = `_stage_args` 가 게이트B 답을 넘겼다(= 게이트를 거친 run).
        # 없으면 픽스처에서 조립한다 — 사람 개입 0회다.
        # 🔴 게이트를 거쳤다고 **사람이 답한 것은 아니다** — 자동승인(`llm`)이면 그 답을
        #    AI 제안값으로 채웠다. 그래서 호출자(`_execute`)가 라벨을 넘긴다.
        #    여기서 `"human" if radius else "fixture"` 로 단정하면 사람이 본 적 없는
        #    값이 `human_confirmed` 로 남는다(원칙 4).
        src = value_source or ("human" if radius else "fixture")
        return _Proc(("3-2",), [py, _svc("run_weight_model.py"), domain]
                     + _weight_args(base, radius or _radius_arg(base), weight, src))
    if stage == "4":
        # STEP4 위치 선정. 한 프로세스가 4-1·4-2·4-3 을 전부 담당한다.
        argv = [py, _svc("gam4_site_select.py"), domain,
                "--spacing", str(cond["spacing"])]
        # 🔴 `topn` 이 있을 때만 붙인다. fixture 의 기준값.json 에는 이 키가 없고,
        #    없던 인자를 "기본값과 같으니 붙여도 된다"고 넣는 순간 실행 조건이
        #    픽스처가 아니라 러너에서 온 것이 된다(원칙 4). 값이 같아도 출처가 다르다.
        if cond.get("topn") is not None:
            argv += ["--topn", str(cond["topn"])]
        return _Proc(("4-1", "4-2", "4-3"), argv, markers=_GAM4_MARKERS)
    raise ValueError(f"알 수 없는 단계: {stage!r}")


def _proc_propose(domain: str, base: dict, run_id: str) -> _Proc:
    """게이트B 제안 패스. `--propose-only` 로 [R]·[W] 제안까지만 만들고 끝낸다.

    `step_ids` 가 비어 있다 — 계약 2절의 6단계에 속하지 않기 때문이다.
    여기에 7번째 단계를 만들면 프런트 진행률 UI 가 같이 바뀌어야 한다.
    이 패스는 **사람에게 보여줄 제안을 뽑는 준비 작업**이지 파이프라인 단계가 아니다.
    """
    return _Proc((), [_python_exe(), _svc("run_weight_model.py"), domain,
                      "--candidates", base["조건"]["candidates"],
                      "--propose-only", "--run-id", run_id])


# ══════════════════════════════════════════════════════════════════
# 6. run 준비 — 격리 (계약 5절)
# ══════════════════════════════════════════════════════════════════
def _domain_root(mode: str | None) -> Path:
    """도메인 폴더의 **부모**. `full` 만 사용자 업로드 루트다(2026-08-14 사람 결정).

    🔴 프리셋(`datasets/흡연`)과 업로드(`datasets/user_input/흡연`)는 **루트가 다르다.**
       같은 이름을 써도 서로 닿을 수 없다 — 문지기로 막는 게 아니라 자리를 가른다.
       (2026-08-13 에 업로드 화면에서 프리셋 `datasets/흡연/data` 536MB 가 지워졌다.)
    ⚠ `fixture`·`hitl` 은 프리셋 재생이므로 `DOMAIN_ROOT` 그대로다. 비대칭이 정상이다.
    """
    return Path(str(USER_INPUT_ROOT)) if mode == MODE_FULL else Path(str(DOMAIN_ROOT))


def _validate_domain(domain: str, mode: str | None = None) -> None:
    """이름 검증 + 폴더 존재. `mode` 를 주면 그 모드가 실제로 읽을 루트에서 본다.

    🔴 `mode` 를 안 주면 프리셋 루트를 본다 — 업로드 API 처럼 사용자 폴더를 뜻하는
       호출자는 **`MODE_FULL` 을 명시**해야 한다. 기본값에 기대면 「업로드는 통과했는데
       실행은 400」 이 되고, 더 나쁘게는 프리셋 폴더가 있다는 이유로 통과한다.
    """
    if not domain or Path(domain).name != domain or domain in (".", ".."):
        raise RunRequestError(f"도메인 이름이 잘못됐습니다: {domain!r}")
    root = _domain_root(mode)
    if not (root / domain).is_dir():
        raise RunRequestError(f"도메인 폴더가 없습니다: {root}/{domain}")


def _read_seq_ledger() -> dict[str, int]:
    """날짜별 **최고수위**(그날 지금까지 발급한 최대 번호). 파일이 없으면 `{}`.

    🔴 깨져 있으면 `raise` 한다. 조용히 `{}` 로 넘기면 판정이 폴더 세기로 되돌아가는데,
       그게 바로 이 파일이 막으려는 재사용이다(원칙 1). 사람이 보고 지우는 건 되지만
       코드가 알아서 무시하면 안 된다.
    """
    if not _SEQ_PATH.exists():
        return {}
    try:
        d = json.loads(_SEQ_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(
            f"run 번호 원장을 읽을 수 없습니다: {_SEQ_PATH} ({e}). "
            "직접 열어 고치거나 지우십시오 — 지우면 번호가 폴더 기준으로 되돌아가 "
            "이전 run 의 id 를 다시 쓸 수 있습니다.") from e
    if not isinstance(d, dict) or not all(
            isinstance(k, str) and isinstance(v, int) for k, v in d.items()):
        raise RuntimeError(f"run 번호 원장의 형식이 잘못됐습니다: {_SEQ_PATH}")
    return d


def _write_seq_ledger(ledger: dict[str, int]) -> None:
    """원장 기록. `_write_status` 와 같은 이유로 원자적 + 재시도다."""
    tmp = _SEQ_PATH.with_suffix(f".json.{threading.get_ident()}.tmp")
    with _IO_LOCK:
        tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(_REPLACE_RETRIES):
            try:
                os.replace(tmp, _SEQ_PATH)
                return
            except PermissionError:
                if attempt == _REPLACE_RETRIES - 1:
                    raise
                time.sleep(_REPLACE_BACKOFF_S)


def _new_run_id() -> str:
    """r_YYYYMMDD_NNN. **한 번 발급한 번호는 다시 쓰지 않는다.**

    🔴 예전엔 `runs/r_<날짜>_*` **폴더를 세어** 다음 번호를 매겼다. 그래서 폴더를
       지우면 번호가 **되돌아갔다** — 같은 run_id 가 다른 실행을 가리킨다. 대가가 셋이다:
       ① 적재기가 그 run_id 로 `booth_candidates` 를 덮으면 이전 run 의 공청회·발화가
       `ON DELETE CASCADE` 로 사라진다 ② 문서에 적어둔 run_id 가 나중에 다른 run 을
       가리킨다(2026-08-10 에 실제로 밟았다 — 근거로 든 `r_20260810_002/hitl/` 이
       다른 도메인 run 이었다) ③ `status.json.loaded.cascaded` 가 0 이 아닌 이유를
       "재사용됐다"로 읽는 진단 자체가 성립하지 않는다.
       이제 발급한 번호를 `runs/run_seq.json` 에 남기고 **폴더와 원장 중 큰 쪽** 다음을
       쓴다. 폴더를 지워도 번호는 안 되돌아간다.

    ⚠ 원장은 **발급 시점에** 쓴다. 뒤이어 `start_run` 이 실패하면 그 번호는 버려진다 —
      번호를 하나 버리는 건 싸고, 다시 쓰는 건 위 셋을 부른다.
    """
    day = datetime.now().strftime("%Y%m%d")
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    # 폴더도 여전히 본다. 원장이 없던 시절의 run 들이 남아 있고, 원장을 지운 사람이
    # 있을 수도 있다 — 둘 중 **큰 쪽**을 쓰면 어느 경우에도 안 겹친다.
    folder_max = max([int(m.group(1))
                      for p in RUNS_ROOT.glob(f"r_{day}_*")
                      if (m := re.match(rf"^r_{day}_(\d+)$", p.name))], default=0)
    ledger = _read_seq_ledger()
    n = max(folder_max, ledger.get(day, 0)) + 1
    ledger[day] = n
    _write_seq_ledger(ledger)
    return f"r_{day}_{n:03d}"


def _prepare_dirs(run_id: str, domain: str, mode: str = MODE_FIXTURE) -> None:
    """run 별 출력 폴더 + **감리 입력 고정**.

    STEP1 도 가른다 — 계약 5절에는 STEP2~4 만 적혀 있지만, 그대로 두면
    파이프라인이 정본 `step1_output/` 의 reviewed.json 을 읽는다. 누가 STEP1 을
    다시 돌리면 같은 `mode:"fixture"` 요청이 **조용히 다른 값**을 낸다.
    'fixture 모드'라면 감리 입력도 픽스처 것이어야 이름이 거짓말을 안 한다.
    (2026-08-04 사람 승인)

    ⚠ `OMNISITE_DATA_ROOT` 와 `OMNISITE_CACHE_DIR` 는 건드리지 않는다.
      바꾸면 SEARCH_CACHE_DIR 가 갈라져 지오코딩·지목 캐시가 무효가 되고
      LLM 호출이 폭증한다(계약 5절).
    """
    d = run_dir(run_id)
    for sub in ("step1", "step2", "step3", "step4"):
        (d / sub).mkdir(parents=True, exist_ok=True)

    # 🔴 full 모드는 여기서 **아무것도 복사하지 않는다.** 감리를 이 run 안에서 직접
    #    돌리므로 step1 산출물이 run 폴더에 새로 생긴다. 정본 step1_output 을 복사해
    #    두면 감리가 실패했을 때 **남의 도메인/이전 실행 결과로 그대로 진행**한다 —
    #    안 터지고 값만 틀리는 전형이다(원칙 1·4).
    if mode == MODE_FULL:
        return

    _, fix_rev = _load_fixture(domain)
    pre = domain_prefix(domain)

    # 정본 step1_output 의 나머지 감리 산출물도 복사해 둔다. 파이프라인이 읽는 것은
    # reviewed 하나지만(실측), 폴백 체인(reviewed > enriched > audit_result)이 있어
    # 한 파일만 두면 나중에 폴백이 조용히 다른 경로를 타게 된다.
    # `step1_output` 은 도메인 폴더가 아니라 **공용 산출물**이다 → `DATA_ROOT` 다.
    # (`DOMAIN_ROOT` 로 두면 그 값을 옮긴 프로세스에서 조용히 못 찾는다.)
    live_step1 = Path(str(DATA_ROOT)) / "step1_output"
    if live_step1.is_dir():
        for src in live_step1.glob(f"{pre}_*"):
            if src.is_file():
                shutil.copyfile(src, d / "step1" / src.name)

    # reviewed 는 **픽스처 것으로 덮어쓴다** — 이게 고정의 핵심이다.
    rev = d / "step1" / f"{pre}_audit_result_reviewed.json"
    shutil.copyfile(fix_rev, rev)

    # 🔴 hitl 모드는 이 사본의 **배제 확정을 전부 제안값으로 되돌린다**(2026-08-10 사람 지시).
    #    픽스처에는 예전 확정(`confirmed:true`)이 박혀 있어 그대로 두면 게이트A 가
    #    `editable:false` 로 내보낸다 — 사람이 보기만 하고 못 고친다. 「HITL 인데
    #    사람이 전부 확인한다」가 성립하지 않는다.
    #    fixture 는 게이트가 없으므로 되돌리면 STEP2 가 미확정으로 멈춘다 → 손대지 않는다.
    #    (원본 픽스처가 아니라 **run 안의 사본**만 바꾼다)
    if mode == MODE_HITL:
        from app.services import gam2_audit_judgment_test as A

        doc = json.loads(rev.read_text(encoding="utf-8"))
        n = A.reset_exclusion_confirmations(doc)
        rev.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        if n:
            print(f"[{run_id}] hitl — 배제 {n}건을 제안값으로 되돌림(사람 재확인 대상)")


def _child_env(run_id: str, mode: str) -> dict:
    env = os.environ.copy()
    d = run_dir(run_id)
    # 🔴 full 자식만 도메인 루트를 사용자 업로드 쪽으로 바꾼다. `OMNISITE_DATA_ROOT` 가
    #    아니라 **`OMNISITE_DOMAIN_ROOT`** 다 — 지오코딩·지목 캐시(`search_cache`)와
    #    `region_data`·`step*_output` 은 도메인 무관 공용이라 `DATA_ROOT` 아래 남아야
    #    한다. 같이 옮기면 캐시가 갈라져 LLM·지오코딩 호출이 폭증한다.
    #    argv 의 도메인 이름은 **그대로 `흡연`** 이다: DB `audit_rules.domain` 컬럼과
    #    `<도메인>_audit_result_reviewed.json` 파일명이 그 값에서 나온다(경로를 넘기면
    #    둘 다 조용히 오염된다).
    if mode == MODE_FULL:
        env["OMNISITE_DOMAIN_ROOT"] = str(USER_INPUT_ROOT)
    env["OMNISITE_STEP1_DIR"] = str(d / "step1")
    env["OMNISITE_STEP2_DIR"] = str(d / "step2")
    env["OMNISITE_STEP3_DIR"] = str(d / "step3")
    env["OMNISITE_STEP4_DIR"] = str(d / "step4")
    # 🔴 없으면 콘솔 코드페이지(cp949)에서 이모지 출력 순간 UnicodeEncodeError 로
    #    죽는다. 값이 틀린 게 아니라 **출력에서** 터지는 것이라 회귀로 오인하기 쉽다.
    env["PYTHONIOENCODING"] = "utf-8"
    # 🔴 파이프로 붙은 stdout 은 기본이 블록 버퍼(8KB)다. 이게 없으면 gam4 의
    #    [B]·[C]·[H] 마커가 **프로세스 종료 직전에 한꺼번에** 도착한다 — 4-1 이 20초
    #    걸린 것처럼 보이고 4-2 는 0.0초로 스쳐 지나간다. 진행률이 거짓말을 한다.
    #    (2026-08-04 실측: r_20260804_001 에서 그렇게 나왔다)
    env["PYTHONUNBUFFERED"] = "1"
    return env


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


# ══════════════════════════════════════════════════════════════════
# 8. 실행 계획 — 게이트는 계획 안의 한 칸이다
# ══════════════════════════════════════════════════════════════════
# 🔴 `hitl` 이 `fixture` 에 게이트 두 칸과 제안 패스를 끼워 넣은 것뿐이라는 게 중요하다.
#    단계 커맨드는 두 모드가 **같은 `_proc_of`** 를 탄다. 모드별로 따로 짜면
#    "픽스처는 맞는데 hitl 은 다른 값" 이 나오고 그건 이 프로젝트가 반복해서 당한 유형이다.
#
#    재실행은 0회다. 게이트에서 **스레드가 끝나고**, 답이 오면 그 다음 칸부터
#    새 스레드가 이어 간다. 진행 상태는 전부 디스크(status.json · run 폴더)에 있으므로
#    서버가 재시작돼도 답변 POST 로 이어갈 수 있다.
#
#    🔴 `full` 은 `hitl` 앞에 **STEP0·1 과 seed 칸**을 더 붙인 것뿐이다.
#       게이트 뒤쪽(2·3-1·propose·gate:weight·3-2·4)은 `hitl` 과 **같은 배열**이고
#       같은 `_proc_of` 를 탄다. 다르게 짜면 "hitl 로는 되는데 full 은 다른 값"이 나온다.
#       뒤로도 **두 칸**이 더 붙는다 — `load-audit`(reviewed → audit_rules)와
#       `load`(Top-N → booth_candidates). 화면5 는 두 테이블을 다 읽는다:
#       앞이 토론의 근거, 뒤가 논의 대상 목록이다. 하나만 넣으면 목록은 뜨는데
#       토론이 첫 줄에서 죽는다(2026-08-10 실측).
#       근거를 먼저 넣는다 — 순서상 의존은 없지만, 목록이 먼저 보이면 사람이
#       고를 수 있는데 눌러도 안 되는 구간이 생긴다.
_PLAN: dict[str, tuple[str, ...]] = {
    MODE_FIXTURE: ("2", "3-1", "3-2", "4", "load-audit", "load"),
    MODE_HITL: ("gate:audit", "2", "3-1", "propose", "gate:weight", "3-2", "4",
                "load-audit", "load"),
    MODE_FULL: ("0-1", "seed", "gate:audit", "2", "3-1", "propose",
                "gate:weight", "3-2", "4", "load-audit", "load"),
}

GATE_IDS = ("audit", "weight")

# 「고속 자동 분석」이 STEP1 산출물에 적는 출처. `human_confirmed` 자리에 들어간다.
# 🔴 `human` 도 `llm` 도 아닌 **따로 만든 낱말**인 이유 — 이 자리의 기존 값은
#    「조항 문자열」이거나 리터럴 `human_confirmed` 둘뿐이라(audit.py:70),
#    `llm` 처럼 짧은 낱말을 넣으면 조례 출처처럼 읽힌다. STEP3 쪽 어휘
#    (`run_weight_model.SRC_RADIUS["llm"]`)와 굳이 같게 맞추지 않는다:
#    두 자리는 뜻이 다르다(여긴 배제반경의 근거, 저긴 값의 출처).
AUTO_APPROVE_SRC = "llm_auto_approved"


def _resume_index(mode: str, gate_id: str) -> int:
    """`gate.id` 로 이어갈 위치를 계획에서 되찾는다.

    status.json 에 '어디까지 했나' 필드를 새로 두지 않는다 — 계약 3절의 스키마를
    늘리지 않으려는 것도 있지만, 그보다 **같은 사실을 두 곳에 적으면 갈리기** 때문이다.
    계획은 고정 배열이고 게이트 id 는 그 안에서 유일하므로 위치는 유도된다.
    """
    plan = _PLAN[mode]
    return plan.index(f"gate:{gate_id}") + 1


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


def _close_cancelled(run_id: str, doc: dict, msg: str = _CANCEL_MSG) -> dict:
    """실행 스레드가 **없는** 취소를 여기서 닫는다(게이트 대기·서버 재시작 뒤).

    스레드가 살아 있으면 이 함수를 부르면 안 된다 — 스레드는 자기 메모리의 `doc` 을
    들고 있어서 단계 전이마다 `_write_status` 를 다시 쓴다. 밖에서 쓴 상태는
    **다음 전이에 조용히 덮인다**(그게 원래 PR 이 `running` run 을 못 멈춘 이유다).

    `msg` 는 `error` 에 적을 사유다. 게이트 만료도 같은 절차로 닫지만
    **문구는 달라야 한다** — 사람이 취소한 게 아닌데 「사용자가 취소했습니다」로
    적으면 산출물이 거짓말을 한다(원칙 4).
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
    doc["error"] = msg
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


# ══════════════════════════════════════════════════════════════════
# 6-1. 게이트 만료 — `awaiting_hitl` 자동 종료
# ══════════════════════════════════════════════════════════════════
def _parse_iso(v) -> datetime | None:
    """ISO 문자열 → naive 로컬 datetime. 못 읽으면 `None`(추측하지 않는다)."""
    if not isinstance(v, str) or not v:
        return None
    try:
        return datetime.fromisoformat(v).replace(tzinfo=None)
    except ValueError:
        return None


def _gate_since(run_id: str, doc: dict) -> tuple[datetime | None, str]:
    """게이트 대기가 시작된 시각과 **그 값이 어디서 왔는지**를 돌려준다.

    셋 다 실패하면 `(None, 사유)` 다 — 「모른다」를 「만료됐다」로 바꾸지 않는다(원칙 1).

    ① `gate.since` — 이 변경 이후 만들어진 run 의 정확한 값.
    ② `status.json` mtime — 옛 run 용 근사값. 게이트가 대기하는 동안에는
       아무도 이 파일을 다시 쓰지 않으므로(`submit_gate` 가 답을 받아야 쓴다)
       마지막 쓰기 = 게이트 진입 시각이다.
    ③ `started_at` — 그것도 없으면 run 시작 시각. 실제 대기 시작보다 이르므로
       만료가 빨라질 수 있지만, 게이트 앞 구간은 몇 분이라 1시간 안에 묻힌다.
    """
    since = _parse_iso((doc.get("gate") or {}).get("since"))
    if since is not None:
        return since, "gate.since"
    p = run_dir(run_id) / "status.json"
    try:
        return datetime.fromtimestamp(p.stat().st_mtime), "status.json mtime"
    except OSError:
        pass
    since = _parse_iso(doc.get("started_at"))
    if since is not None:
        return since, "started_at"
    return None, "시각을 못 읽음"


def expire_stale_gates(now: datetime | None = None) -> list[dict]:
    """제한시간이 지난 `awaiting_hitl` run 을 닫는다. 닫은 목록을 돌려준다.

    🔴 이건 **주기 작업**이다(부팅 1회가 아니다). `reap_orphans` 의 판정식은
       `started_at < _SERVER_BOOT` 라 답이 부팅 시점에 고정되지만, 여기 답은
       시간이 지나면 바뀐다 — 서버가 떠 있는 동안 새로 만료된다.
    """
    now = now or datetime.now()
    limit = timedelta(hours=hitl_timeout_hours())
    closed: list[dict] = []
    if not RUNS_ROOT.exists():
        return closed
    for sf in sorted(RUNS_ROOT.glob("*/status.json")):
        run_id = sf.parent.name
        try:
            doc = read_status(run_id)
        except Exception:
            _log.exception("[게이트만료] %s status.json 을 못 읽었다 — 건너뛴다.", run_id)
            continue
        if not doc or doc.get("status") != "awaiting_hitl":
            continue
        # 스레드가 살아 있으면 뒷정리 주체가 그쪽이다(`cancel_run` 과 같은 판정).
        with _LOCK:
            worker = _WORKERS.get(run_id)
        if worker is not None and worker.is_alive():
            continue
        since, src = _gate_since(run_id, doc)
        if since is None:
            _log.warning("[게이트만료] %s 대기 시작 시각을 못 읽어 건너뛴다(%s).",
                         run_id, src)
            continue
        if now - since < limit:
            continue
        gate_id = (doc.get("gate") or {}).get("id") or "?"
        msg = _HITL_TIMEOUT_MSG.format(
            hours=hitl_timeout_hours(), gate=gate_id,
            since=since.isoformat(timespec="seconds"))
        try:
            _close_cancelled(run_id, doc, msg=msg)
        except RunConflict:
            continue                 # 훑는 사이에 사람이 답했다 — 정상이다
        except Exception:
            # 한 run 이 터져도 나머지는 닫는다. 안 그러면 깨진 run 하나가
            # 정리기를 통째로 멈춰 지금 고치려는 상태로 되돌아간다.
            _log.exception("[게이트만료] %s 를 닫지 못했다.", run_id)
            continue
        closed.append({"run_id": run_id, "domain": doc.get("domain"),
                       "gate": gate_id, "since": since.isoformat(timespec="seconds"),
                       "since_source": src})
        _log.warning("[게이트만료] %s (%s) 게이트 %s · 대기 시작 %s (%s) → failed",
                     run_id, doc.get("domain"), gate_id,
                     since.isoformat(timespec="seconds"), src)
    return closed


async def hitl_sweep_loop() -> None:
    """주기적으로 `expire_stale_gates()` 를 돈다. lifespan 이 띄우고 끈다."""
    interval = hitl_sweep_interval_sec()
    _log.info("[게이트만료] 감시 시작 — 제한 %d시간 · 주기 %d초",
              hitl_timeout_hours(), interval)
    while True:
        try:
            closed = await asyncio.to_thread(expire_stale_gates)
            if closed:
                _log.warning("[게이트만료] %d건 종료: %s", len(closed),
                             ", ".join(c["run_id"] for c in closed))
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("[게이트만료] 훑기 실패 — 아무것도 닫지 않았다.")
        await asyncio.sleep(interval)


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
                    # 대기 시작 시각. 만료 판정은 이 값으로 한다 — 없으면
                    # `status.json` mtime 으로 떨어지는데(옛 run) 그건 근사값이다.
                    gate["since"] = _now_iso()
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


class _StepFailed(Exception):
    pass


class _Cancelled(Exception):
    """사용자 취소. `_StepFailed` 와 나누는 이유는 **사유가 다르기 때문**이다 —
    파이프라인이 틀린 게 아니라 사람이 그만두게 한 것이고, 그 둘을 한 예외로 묶으면
    자식의 종료 코드(-15 등)가 실패 사유로 적힌다."""


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

       🔴 마커가 **없는** 실패도 있다 — 자식이 그냥 예외로 죽는 경우다. 파이썬
       예외 메시지는 여러 줄일 수 있고 그때 마지막 줄은 대개 「참조: …」 같은
       꼬리표라, `tail[-1]` 만 쓰면 화면이 **애먼 파일을 지목한다**. 실측
       (r_20260824_001) —

           ValueError: [03] 행정동 조인 키(코드 또는 이름)를 찾지 못했습니다.
             컬럼: ['순번', '차량소속기관', …]        ← 버려짐
             후보 매칭률: 후보 없음                    ← 버려짐
             참조: 행정동_크로스워크.csv               ← 이 줄만 화면에 떴다

       크로스워크는 멀쩡했는데 두 번 연속 그 파일이 원인으로 읽혔다(원칙 4).
       그래서 트레이스백이면 **예외 줄부터 끝까지**를 사유로 삼는다. 예외 줄은
       들여쓰기가 없고 프레임 줄은 있다는 것으로 가른다(파이썬 표준 형식).
       연쇄 예외면 **마지막** 트레이스백을 쓴다 — 그게 실제로 죽인 예외다.

    둘 다 없으면 마지막 줄로 되돌아간다. **지어내지 않는다** — 못 찾았을 때
    그럴듯한 문장을 합성하면 없는 사유가 기록된다.
    """
    start = None
    for i in range(len(tail) - 1, -1, -1):
        if tail[i].lstrip().startswith("[중단]"):
            start = i
            break
    if start is None:
        for i in range(len(tail) - 1, -1, -1):
            if tail[i].lstrip().startswith("Traceback (most recent call last)"):
                for j in range(i + 1, len(tail)):
                    if tail[j][:1] not in (" ", "\t"):
                        start = j
                        break
                break
    if start is None:
        return tail[-1].strip() if tail else f"종료 코드 {rc}"
    block = tail[start:]
    if len(block) > 12:                    # 트레이스백이 통째로 붙는 경우
        block = block[:12] + [f"… (이하 {len(block) - 12}줄은 run.log)"]
    return "\n".join(block)


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
                # 🔴 **들여쓰기를 지우지 않는다.** 파이썬 트레이스백에서 「예외 줄」과
                #    「프레임 줄」을 가르는 표시는 들여쓰기뿐이다 — strip 해서 담으면
                #    `_fail_reason` 이 여러 줄 예외의 **시작**을 못 찾아 마지막 줄만
                #    사유가 된다. 실제로 그래서 화면이 애먼 파일을 지목했다
                #    (r_20260824_001 — 진단 3줄이 버려지고 `참조: …csv` 만 남았다).
                tail.append(line.rstrip())
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


# ══════════════════════════════════════════════════════════════════
# 8b. HITL 게이트 (계약 7절)
# ══════════════════════════════════════════════════════════════════
# 질문을 만드는 쪽과 답을 적용하는 쪽이 **같은 파일을 본다.**
#   게이트A → `runs/<id>/step1/<pre>_audit_result_reviewed.json`
#   게이트B → `runs/<id>/step3/<domain>_weight_proposal_<run_id>.json`
# 질문을 따로 계산해 두었다가 적용할 때 다시 계산하면 그 사이에 갈릴 수 있다.
#
# 🔴 답을 적용하는 함수는 **정본을 그대로 부른다**(`apply_radius_answer`·
#    `apply_intent_answer`). 새로 짜면 CLI 와 API 가 갈리고, 그게 이 프로젝트가
#    반복해서 당한 유형이다(CLAUDE.md '모듈 사본').


def _hitl_dir(run_id: str) -> Path:
    return run_dir(run_id) / "hitl"


def _answer_path(run_id: str, gate_id: str) -> Path:
    return _hitl_dir(run_id) / f"{gate_id}_answer.json"


def _save_answer(run_id: str, gate_id: str, payload: dict,
                 by: str = "human") -> None:
    """누가 무엇을 답했는지 원본 그대로 남긴다.

    규약 '값마다 누가 정했는지 남긴다' 의 게이트판이다. reviewed.json 에는
    적용 **결과**만 남고 '무엇을 건너뛰었는지'는 안 남는다 — 그건 여기 있다.

    🔴 `by` 를 같이 적는다. 「고속 자동 분석」이 넣은 답은 모양이 사람 답과 똑같아서
       (같은 검증기를 타므로 당연히 그렇다) 이 필드가 없으면 파일만 보고는 구분할
       방법이 없다 — 나중에 「사람이 이렇게 답했다」로 읽힌다(원칙 4).
    """
    _hitl_dir(run_id).mkdir(parents=True, exist_ok=True)
    doc = {"gate": gate_id, "answered_at": _now_iso(),
           "answered_by": by, "answer": payload}
    _answer_path(run_id, gate_id).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_answer(run_id: str, gate_id: str) -> dict | None:
    p = _answer_path(run_id, gate_id)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))["answer"]


def _reviewed_path(run_id: str, domain: str) -> Path:
    return run_dir(run_id) / "step1" / f"{domain_prefix(domain)}_audit_result_reviewed.json"


def _seed_reviewed(run_id: str, domain: str) -> str:
    """full 모드에서 게이트A 가 읽을 `reviewed.json` 을 만든다. 반환: 출처 파일명.

    STEP1 은 `audit_result.json`(감리 판정)과 — 미확정 배제반경이 있으면 —
    `audit_result_enriched.json`(상위법 검색 제안값 포함)까지 쓴다. `reviewed` 는
    **사람이 확정한 판**이라 STEP1 이 만들지 않는다. CLI 에서는 `review_hitl` 이
    대화형으로 만들지만, API 는 그 자리를 게이트A 가 대신한다.

    🔴 폴백 순서(enriched > audit_result)는 `review_hitl`(:1619 주석)과 **같다.**
       여기서 다른 순서를 쓰면 CLI 로 돌린 결과와 API 로 돌린 결과가 갈린다.
    🔴 게이트A 답변은 이 파일을 **제자리에서 고친다**(`_apply_audit`). 즉 이 시드는
       "빈 껍데기"가 아니라 **LLM 제안값 그대로**이고, 사람이 손대지 않은 항목은
       제안값이 그대로 남는다 — `confirmed` 플래그가 그 사실을 구분해 준다.
    """
    d = run_dir(run_id) / "step1"
    pre = domain_prefix(domain)
    for name in (f"{pre}_audit_result_enriched.json", f"{pre}_audit_result.json"):
        src = d / name
        if src.is_file():
            shutil.copyfile(src, _reviewed_path(run_id, domain))
            return name
    raise _StepFailed(
        f"STEP1 감리 산출물이 없습니다: {d}/{pre}_audit_result[_enriched].json — "
        "STEP0·1 이 파일을 남기지 않고 끝났습니다.")


def _proposal_path(run_id: str, domain: str) -> Path:
    """`save_weight_proposal` 이 쓴 곳. 자식은 STEP3_OUTPUT_DIR 이 run 폴더로 잡혀 있다."""
    return run_dir(run_id) / "step3" / f"{domain}_weight_proposal_{run_id}.json"


def build_gate(gate_id: str, run_id: str, domain: str) -> dict:
    if gate_id == "audit":
        return {"id": "audit", "label": "감리 확인 — 배제반경 · 데이터 용도 · 지역 코드",
                "questions": _questions_audit(run_id, domain)}
    if gate_id == "weight":
        return {"id": "weight", "label": "집계반경 · 가중치 확정",
                "questions": _questions_weight(run_id, domain)}
    raise ValueError(f"알 수 없는 게이트: {gate_id!r}")


# ── 게이트A 질문 ───────────────────────────────────────────────────
#  확정분도 **보여준다.** 감추면 사람은 "무엇이 이미 정해졌는지" 를 모른 채 남은 것만
#  답하게 된다 — 화면이 사실의 일부만 보여주는 것이다(원칙 4).
#
#  🔴 **확정분도 이제 고칠 수 있다**(`editable` 은 항상 true). 2026-08-05 에는
#     `false` 였다 — 「HITL 전 confirmed 는 조례에서 근거를 확실히 찾았을 때뿐이라
#     고칠 이유가 없다」가 근거였다. 그 근거가 무너졌다: 조례 대조는 2026-08-10 에
#     **확정에서 제안으로 강등**됐고(함정표 「자동 확정이 flag 를 안 남겨…」),
#     자동 확정 경로 둘을 없앤 지금 남은 confirmed 는 **앞선 게이트 답변** 뿐이다.
#     자기가 방금 넣은 값을 못 고치면 화면은 「다시 시작」 말고는 길이 없다.
#
#  🔴 그래서 **`confirmed` 를 따로 싣는다.** `editable` 을 true 로 바꾸는 것만으로
#     끝내면 「이미 확정된 항목」이라는 사실이 산출물에서 **사라진다** — 그 사실을
#     들고 있던 필드가 `editable` 하나뿐이었기 때문이다(원칙 4). 화면은 이 값으로
#     「확정됨 · 수정 가능」을 표시한다. 두 값은 뜻이 다르다:
#       editable  = 지금 고칠 수 있는가   ·  confirmed = 이미 정해진 값인가
def _source_geometry(domain: str) -> dict[str, dict]:
    """STEP0 프로파일에서 **원본 형태**를 읽는다. dataset_id → `{geometry, rows, why}`.

    🔴 **`exclusion_type` 을 쓰지 않는 이유.** 그 필드는 감리 AI 의 판정이고,
       CLAUDE.md 함정표 「exclusion_type 오판」이 가리키는 바로 그 값이다 —
       S9(`gam4_exclusion_shape.resolve`)가 존재하는 이유가 그걸 뒤집기 위해서다.
       반면 **원본이 점이냐**는 추측이 아니라 파일에서 읽히는 사실이다
       (좌표 컬럼이 있는 csv/xlsx 는 점, `.shp`·`.gpkg` 는 면일 수 있다).

    🔴 **판정이 아니라 사실만 싣는다.** 여기서 「반경 없음은 안 된다」까지 정하지
       않는다 — 점 레이어라도 지목 배수 판정으로 면 필지가 잡히면 반경 없이도
       배제 면적이 나온다(`resolve` 의 `parts.append(...g...)` 갈래). 그래서
       게이트는 **막지 않고 알린다**. 못 읽으면 `unknown` 이다(지어내지 않는다).
    """
    try:
        from app.config import domain_paths
        p = Path(domain_paths(domain)["profiles"])
        if not p.is_file():
            return {}
        prof = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                      # 프로파일은 부가정보다 — 게이트를 죽이지 않는다
        return {}

    out: dict[str, dict] = {}
    for did, pr in (prof or {}).items():
        if not isinstance(pr, dict):
            continue
        ext = str(pr.get("extension") or "").lower().lstrip(".")
        if ext in ("shp", "gpkg", "geojson", "json"):
            geom, why = "unknown", f"공간 파일(.{ext}) — 점/면은 레이어를 열어야 안다"
        elif pr.get("has_coord_col"):
            geom, why = "point", f"좌표 컬럼 {pr.get('coord_cols')}"
        elif pr.get("has_addr_col"):
            geom, why = "point", "주소만 있음 — 지오코딩되어 점이 된다"
        else:
            geom, why = "unknown", "좌표·주소 컬럼이 없다"
        out[str(did)] = {"geometry": geom, "rows": pr.get("row_count"), "why": why}
    return out


def _questions_audit(run_id: str, domain: str) -> list[dict]:
    p = _reviewed_path(run_id, domain)
    if not p.is_file():
        raise _StepFailed(f"감리 결과가 없습니다: {p}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    region = (doc.get("facility_inference") or {}).get("region", "")
    geo = _source_geometry(domain)
    out: list[dict] = []

    def _exclusion_q(did, summary, roles, idx, f):
        role = roles[idx] if idx < len(roles) else {}
        g = geo.get(str(did)) or {}
        return {
            "kind": "exclusion",
            "dataset_id": did,
            "role_index": idx,
            "editable": True,
            # flag 가 없는 배제 role 도 질문이 된다 → **role 쪽 확정도 본다.**
            # flag 만 보면 flag 없는 확정 항목이 `confirmed:false` 로 나가
            # 「이미 정해졌다」는 표시가 항목마다 달라진다.
            "confirmed": bool(f.get("confirmed") or role.get("confirmed")),
            "summary": summary,
            "facility_type": role.get("facility_type"),
            "exclusion_type": role.get("exclusion_type"),
            "rationale": role.get("rationale", ""),
            "radius_m": role.get("배제반경_m"),
            "radius_source": role.get("source"),
            # 제안값은 확정값이 아니다 — 둘을 한 필드로 합치지 않는다.
            "proposed_m": f.get("제안값"),
            "proposal_source": f.get("출처"),
            "evidence": f.get("근거문장"),
            # False 면 "다른 시설 규정일 수 있다" — 화면에 경고로 띄울 것
            "evidence_matches_facility": f.get("근거_시설_일치"),
            # 🔴 **감리 판정이 아니라 원본 파일에서 읽은 사실**이다(`_source_geometry`).
            #    `point` 인데 「반경 없음」으로 확정하면 STEP4 에서 배제 면적 0 으로
            #    run 이 죽을 수 있다 — 그 사실을 **게이트에서** 알리기 위한 값이다.
            #    막지는 않는다: 지목 배수 판정이 면 필지를 잡으면 반경 없이도 배제가 생긴다.
            "source_geometry": g.get("geometry", "unknown"),
            "source_rows": g.get("rows"),
            "source_geometry_why": g.get("why"),
        }

    for r in doc.get("results", []):
        did = r.get("dataset_id")
        summary = r.get("summary", "")
        roles = r.get("roles") or []
        asked: set[int] = set()

        for f in r.get("hitl_flags") or []:
            ftype = f.get("type")
            if ftype == "exclusion_radius_missing":
                idx = f.get("role_index", 0)
                asked.add(idx)
                out.append(_exclusion_q(did, summary, roles, idx, f))
            elif ftype == "data_intent_unclear":
                out.append({
                    "kind": "intent",
                    "dataset_id": did,
                    "editable": True,
                    "confirmed": bool(f.get("confirmed")),
                    "summary": summary,
                    "message": f.get("message", ""),
                    "current_roles": [x.get("role") for x in roles],
                    # `needs_radius` 는 프런트가 반경 입력칸을 띄울 근거다. 배제로
                    # 승격하면 반경이 필요한데 그 질문은 **답변 전에** 만들어질 수
                    # 없으므로(role 이 아직 없다) 같은 항목의 `radius_m` 으로 받는다.
                    "choices": [
                        {"value": 1, "label": "가점(수요)",
                         "needs_weight": True, "needs_radius": False},
                        {"value": 2, "label": "감점(민감도)",
                         "needs_weight": True, "needs_radius": False},
                        {"value": 3, "label": "배제(금지)",
                         "needs_weight": False, "needs_radius": True},
                        {"value": 4, "label": "위치선정 참조용",
                         "needs_weight": False, "needs_radius": False},
                        {"value": 5, "label": "잘못 넣음·제외",
                         "needs_weight": False, "needs_radius": False},
                    ],
                })

        # 🔴 flag 가 없는 배제도 **묻는다**(2026-08-10). 배제는 미확정이면 STEP2 가
        #    멈추는데(`assert_exclusions_confirmed`), 게이트에 안 뜨면 답할 방법이
        #    없어 run 이 죽는다. 지금 두 경로(`enrich_hitl_flags`·
        #    `reset_exclusion_confirmations`)가 flag 를 보장하지만, 보장이 깨졌을 때
        #    조용히 사라지는 쪽이 아니라 **묻는 쪽**으로 넘어져야 한다.
        for idx, role in enumerate(roles):
            if role.get("role") != "hard_exclusion" or idx in asked:
                continue
            out.append(_exclusion_q(did, summary, roles, idx, {}))

        for oi, op in enumerate(r.get("cleaning_ops") or []):
            if op.get("op_id") != "filter_by_code_prefix":
                continue
            prm = op.get("params") or {}
            chk = prm.get("prefix_check") or {}
            out.append({
                "kind": "code_prefix",
                "dataset_id": did,
                # `cleaning_ops` **전체** 기준 인덱스다. filter_by_code_prefix 만
                # 센 번호가 아니다 — 적용할 때 같은 방식으로 찾는다.
                "op_index": oi,
                "editable": True,
                "confirmed": bool(prm.get("prefix_confirmed")),
                "summary": summary,
                "col": prm.get("col"),
                "prefix": prm.get("prefix", ""),
                "region": region,
                "verdict": chk.get("verdict"),
                "reason": chk.get("reason"),
                "detail": chk.get("detail"),
                "suggestion": chk.get("suggestion"),
                "confirmed_by": prm.get("prefix_confirmed_by"),
                # 🔴 감리 때 코드표 대조를 못 했으면(`prefix_check` 없음/unknown)
                #    여기서 다시 판정하지 않는다. `_code_samples` 가 `build_fixtures()`
                #    를 부르고 모듈 전역에 캐시하는데, 이건 오래 사는 API 프로세스가
                #    할 일이 아니다. 못 한 건 못 했다고 내보낸다(원칙 4·5).
                "recheck_skipped": not chk or chk.get("verdict") == "unknown",
            })
    return out


# ── 게이트B 질문 ───────────────────────────────────────────────────
def _questions_weight(run_id: str, domain: str) -> list[dict]:
    p = _proposal_path(run_id, domain)
    if not p.is_file():
        raise _StepFailed(f"가중치 제안이 없습니다: {p}")
    prop = json.loads(p.read_text(encoding="utf-8"))
    conflicts = {c["indicator_id"]: c for c in prop.get("conflicts", [])}
    out = []
    for ind in prop["indicators"]:
        iid = ind["id"]
        rp = (prop.get("radius_proposed") or {}).get(iid) or {}
        out.append({
            "kind": "weight",
            "indicator_id": iid,
            "indicator_kind": ind["kind"],
            # admin 지표는 행정동 단위라 반경 개념이 없다. 답에 넣으면 400 이다.
            "radius_required": ind["kind"] != "admin",
            "direction": ind["direction"],
            "seed_weight": ind["seed_weight"],
            "components": ind.get("components"),
            "rationale": ind.get("rationale", ""),
            "data_note": ind.get("data_note", ""),
            "radius_proposed": rp.get("radius_m"),
            "radius_rationale": rp.get("rationale", ""),
            "radius_source": rp.get("source"),
            "slider_proposed": (prop.get("slider_proposed") or {}).get(iid),
            # 방향 판정 충돌 — 사람이 슬라이더 **부호**로 정해야 넘어간다.
            "conflict": conflicts.get(iid),
        })
    return out


# ── 답변 접수 ──────────────────────────────────────────────────────
def submit_gate(run_id: str, gate_id: str, payload: dict) -> dict:
    """게이트 답을 검증·적용하고 실행을 이어간다. 갱신된 status 를 돌려준다."""
    if gate_id not in GATE_IDS:
        raise RunRequestError(f"알 수 없는 게이트: {gate_id!r}")
    doc = read_status(run_id)
    if doc is None:
        raise KeyError(run_id)              # 라우터가 404
    if doc.get("status") != "awaiting_hitl":
        raise RunRequestError(
            f"이 run 은 사람 확정을 기다리고 있지 않습니다 (status={doc.get('status')!r})")
    gate = doc.get("gate") or {}
    if gate.get("id") != gate_id:
        raise RunRequestError(
            f"지금 기다리는 게이트는 '{gate.get('id')}' 입니다 (요청: '{gate_id}')")
    if not isinstance(payload, dict):
        raise RunRequestError("요청 본문이 객체가 아닙니다.")
    # 계약 7-5 가 body 에 run_id 를 둔다. 경로와 다르면 프런트가 다른 run 을 보고 있다 —
    # 조용히 경로 쪽을 쓰면 남의 run 에 답을 적용한다.
    if payload.get("run_id") not in (None, run_id):
        raise RunRequestError(
            f"본문 run_id 가 경로와 다릅니다: {payload.get('run_id')!r} != {run_id!r}")

    domain = doc["domain"]
    mode = doc.get("mode", MODE_HITL)
    questions = gate.get("questions") or []
    if gate_id == "audit":
        _apply_audit(run_id, domain, questions, payload)
    else:
        _validate_weight(questions, payload)

    # 🔴 서버가 재시작되면 `_ACTIVE` 는 비지만 `awaiting_hitl` 인 run 은 디스크에 남는다
    #    (`reap_orphans` 는 queued/running 만 닫는다 — 게이트 대기는 중단이 아니다).
    #    그 상태에서 답이 오면 여기서 다시 점유한다. 안 하면 같은 도메인에 새 run 이
    #    동시에 돌아 정본 캐시·데이터를 함께 건드린다.
    with _LOCK:
        other = _ACTIVE.get(domain)
        if other and other != run_id:
            raise RunConflict(f"'{domain}' 은 이미 실행 중입니다 (run_id={other})")
        _ACTIVE[domain] = run_id

    _save_answer(run_id, gate_id, payload)
    doc["status"] = "running"
    doc.pop("gate", None)
    _write_status(run_id, doc)
    _spawn(run_id, domain, mode, _resume_index(mode, gate_id))
    return doc


def _q(questions: list[dict], kind: str, **key) -> dict:
    """질문 목록에서 대상 하나를 찾는다. 없으면 400 — 조용히 무시하지 않는다."""
    for q in questions:
        if q["kind"] == kind and all(q.get(k) == v for k, v in key.items()):
            return q
    raise RunRequestError(f"게이트에 없는 대상입니다: {kind} {key}")


def _int_in(v, lo: int, hi: int, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise RunRequestError(f"{what} 은 정수여야 합니다: {v!r}")
    if not (lo <= v <= hi):
        raise RunRequestError(f"{what} 범위는 {lo}~{hi} 입니다: {v}")
    return v


def _num_in(v, lo: float, hi: float, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise RunRequestError(f"{what} 은 숫자여야 합니다: {v!r}")
    if not (lo <= v <= hi):
        raise RunRequestError(f"{what} 범위는 {lo}~{hi} 입니다: {v}")
    return float(v)


def _only_keys(item: dict, allowed: tuple[str, ...], what: str) -> None:
    """항목 안의 알 수 없는 키를 400 으로 막는다.

    🔴 최상위 키는 예전부터 막았는데 **항목 내부는 안 봤다**(2026-08-10 실측).
       그래서 `radius_m` 오타(`radius_mm`)가 「건너뜀 = 미확정 유지」로 읽히고,
       `intents` 에 실은 `radius_m` 은 **200 인데 값이 버려졌다.** 조용히 버리면
       프런트는 성공으로 읽고 run 은 STEP2 에서 죽는다(원칙 1·4).
    """
    if not isinstance(item, dict):
        raise RunRequestError(f"{what} 항목은 객체여야 합니다: {item!r}")
    bad = [k for k in item if k not in allowed]
    if bad:
        raise RunRequestError(
            f"{what} 에 알 수 없는 필드: {bad}. 쓸 수 있는 것: {list(allowed)}")


def _exclusion_flag(result: dict, role_index: int, message: str) -> dict:
    """`exclusion_radius_missing` flag 를 찾고, 없으면 만든다.

    질문은 flag 가 없는 배제 role 로도 만들어진다(`_questions_audit`). 없다고 답을
    버리면 사람이 답한 것이 조용히 사라지고 STEP2 는 미확정이라며 멈춘다.
    """
    flags = result.setdefault("hitl_flags", [])
    for f in flags:
        if f.get("type") == "exclusion_radius_missing" and f.get("role_index", 0) == role_index:
            return f
    flag = {"type": "exclusion_radius_missing", "role_index": role_index,
            "message": message, "제안값": None, "출처": None}
    flags.append(flag)
    return flag


def _drop_exclusion(result: dict, q: dict, auto: bool) -> None:
    """배제(hard_exclusion) 를 **적용하지 않기로** 확정한다 → role 을 `reference_only` 로.

    `_guard_zero_area`(gam4_site_select) 가 직접 안내하는 두 선택지 중 하나다 —
    「배제반경을 입력하거나, **그 레이어를 hard_exclusion 에서 빼세요**」. 반경 근거가
    없는 점 레이어는 앞을 고르면 값을 지어내는 것이므로(원칙 2) 뒤가 유일한 답이다.

    🔴 **버리는 게 아니라 강등**이다. `reference_only` 는 STEP2 가 정제는 그대로 하고
       GIS 입력에서만 빼는 기존 어휘다(`gam2_clean_data:372`) — 데이터는 페르소나·참조로
       계속 쓰인다. `roles: []`(제외)로 만들면 그 데이터셋이 통째로 사라진다.

    🔴 배제를 **안 했다는 사실**을 세 곳에 남긴다: role(`배제_해제`·사유·이전 상태) ·
       flag · `report.json` 의 gap(`배제_해제`, `gam4_export`). 한 곳만 적으면 그 한 곳을
       안 보는 사람에게는 「배제가 원래 없었다」로 읽힌다(원칙 4).
    """
    idx = q.get("role_index", 0)
    roles = result.get("roles") or []
    if idx >= len(roles):
        raise RunRequestError(f"[{q.get('dataset_id')}] roles[{idx}] 이 없습니다.")
    role = roles[idx]
    why = (
        f"배제반경 근거가 없고 원본이 점 레이어입니다"
        f"({q.get('source_geometry_why') or '좌표 컬럼'}) — 반경 없이 확정하면 배제 면적이"
        f" 0 이라 STEP4 에서 멈춥니다. 배제를 적용하지 않고 참조용으로만 씁니다."
    )
    role["배제_해제_이전"] = {
        "role": role.get("role"),
        "exclusion_type": role.get("exclusion_type"),
        "facility_type": role.get("facility_type"),
        "배제반경_m": role.get("배제반경_m"),
    }
    role["role"] = "reference_only"
    role["배제_해제"] = True
    role["배제_해제_사유"] = why
    role["confirmed"] = True
    role["need_review"] = False
    role["source"] = AUTO_APPROVE_SRC if auto else "human_confirmed"

    flag = _exclusion_flag(result, idx, why)
    flag["message"] = why
    flag["배제_해제"] = True
    flag["confirmed"] = True
    flag["confirmed_by_human"] = not auto
    if auto:
        flag["자동승인"] = True


def _apply_audit(run_id: str, domain: str, questions: list[dict], payload: dict,
                 auto: bool = False) -> None:
    """게이트A 답을 reviewed.json 에 반영한다. **정본 함수를 그대로 부른다.**

    🔴 `radius_m` 은 `null`(반경 없음으로 확정)과 **키 생략**(건너뜀 — 미확정 유지)이
       다른 뜻이다. CLI 의 `n` 과 `s` 에 각각 대응한다.

    🔴 `auto` 는 「고속 자동 분석」이 AI 제안값을 그대로 넣은 실행이다. **검증·적용
       경로는 사람 답과 한 글자도 다르지 않다** — 갈라두면 자동 경로만 통과하는
       값이 생긴다. 갈리는 것은 **누가 정했는가** 하나뿐이고, 그래서 산출물에
       `human_confirmed`·`prefix_confirmed_by:"human"` 을 적지 않는다. 적으면
       사람이 본 적 없는 값이 「사람이 확정함」으로 남는다(원칙 4).
    """
    # 늦은 import — 2,000행짜리 감리 모듈을 서버 기동 때 끌고 오지 않는다.
    # (이 모듈 자체는 DB·네트워크를 안 건드린다. 실측 확인함)
    from app.services import gam2_audit_judgment_test as A

    for key in payload:
        if key not in ("run_id", "exclusions", "intents", "code_prefixes"):
            raise RunRequestError(f"알 수 없는 필드: {key!r}")

    path = _reviewed_path(run_id, domain)
    doc = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r.get("dataset_id"): r for r in doc.get("results", [])}

    # 정본 함수들이 쓰는 도메인 경로(프리픽스·data·law)를 확정한다.
    # (배제반경 캐시는 2026-08-10 제거됐다 — 확정은 이 run 안에서만 유효하다)
    A.set_domain(domain)

    def _radius_answer(result: dict, flag: dict, radius: int | None) -> None:
        A.apply_radius_answer(
            result, flag, radius,
            source=AUTO_APPROVE_SRC if auto else "human_confirmed")
        if auto:
            # 🔴 정본 함수는 「사람이 확인함」을 무조건 켠다(:723) — 그 함수의
            #    호출자가 여태 사람뿐이었기 때문이다. 자동승인은 사람이 본 적이
            #    없으므로 여기서 되돌리고, 대신 **무슨 일이 있었는지**를 남긴다.
            flag["confirmed_by_human"] = False
            flag["자동승인"] = True

    # 🔴 **「이미 확정됐으니 수정 불가」검사는 없다**(2026-08-12 제거). 예전엔 세 갈래
    #    각각에 `if not q["editable"]: raise` 가 있었다. 두 가지 이유로 지웠다:
    #      ⓐ `editable` 은 이제 **항상 true** 다(그 근거는 `_questions_audit` 위 주석).
    #         남겨두면 영원히 안 도는 분기가 「그런 규칙이 아직 있다」고 말한다.
    #      ⓑ 애초에 이 검사는 **우리가 방금 만든 질문 dict 를 우리가 되읽는** 것이라
    #         재는 자와 재어지는 자가 같았다. 요청이 보낸 값을 막는 게 아니었다.
    #    확정 여부는 이제 질문의 `confirmed` 로 **화면에 알리기만** 한다.
    for item in payload.get("exclusions") or []:
        _only_keys(item, ("dataset_id", "role_index", "radius_m", "drop"), "exclusions")
        q = _q(questions, "exclusion", dataset_id=item.get("dataset_id"),
               role_index=item.get("role_index"))
        if item.get("drop"):
            # 🔴 배제 해제도 **확정**이다(미확정 유지가 아니다). 반경과 같이 오면
            #    「빼겠다」와 「이 반경으로 배제하겠다」가 동시에 참일 수 없다.
            if "radius_m" in item:
                raise RunRequestError(
                    f"[{q['dataset_id']}] drop 과 radius_m 은 같이 못 씁니다 — "
                    "배제를 빼거나 반경을 정하거나 하나입니다.")
            _drop_exclusion(by_id[q["dataset_id"]], q, auto)
            continue
        if "radius_m" not in item:
            continue                    # 건너뜀 = 미확정 유지. CLI 의 's'
        radius = item["radius_m"]
        if radius is not None:
            radius = _int_in(radius, 1, 5000, f"[{q['dataset_id']}] 배제반경(m)")
        r = by_id[q["dataset_id"]]
        _radius_answer(
            r, _exclusion_flag(r, q["role_index"], "게이트A 에서 직접 확정"), radius)

    for item in payload.get("intents") or []:
        _only_keys(item, ("dataset_id", "choice", "weight", "radius_m"), "intents")
        q = _q(questions, "intent", dataset_id=item.get("dataset_id"))
        choice = _int_in(item.get("choice"), 1, 5, f"[{q['dataset_id']}] choice")
        weight = item.get("weight")
        if choice in (1, 2):
            # 🔴 `apply_intent_answer` 는 abs(weight) 를 쓴다 — None 이면 TypeError 다.
            #    부호는 choice 가 정하므로 여기서는 크기만 받는다.
            weight = _num_in(weight, -1.0, 1.0, f"[{q['dataset_id']}] weight")
            if weight == 0:
                raise RunRequestError(
                    f"[{q['dataset_id']}] 가점/감점인데 크기가 0 입니다. "
                    "제외하려면 choice=5 를 쓰세요.")
        elif weight is not None:
            raise RunRequestError(
                f"[{q['dataset_id']}] weight 는 choice 1·2 에서만 씁니다.")
        if choice != 3 and "radius_m" in item:
            raise RunRequestError(
                f"[{q['dataset_id']}] radius_m 은 choice 3(배제 승격)에서만 씁니다.")

        r = by_id[q["dataset_id"]]
        A.apply_intent_answer(r, choice, weight)

        # 🔴 배제 승격은 **반경을 같은 항목에서 받는다**(2026-08-10, 사람 결정).
        #    `apply_intent_answer(…, 3)` 은 `배제반경_m: None · confirmed: False` 인
        #    role 을 새로 만든다 → 미확정이라 STEP2 가 멈추는데
        #    (`assert_exclusions_confirmed`), 게이트A 질문 목록은 **답변 전에** 만들어져
        #    이 role 의 질문이 없다. 그래서 `exclusions` 로도 답할 수 없었다(400).
        #    여기서 안 받으면 그 run 은 **답할 자리가 없는 채** 죽는다.
        if choice == 3:
            # roles 를 통째로 갈아치웠으므로 옛 확정은 무효다. 지우지 않으면 게이트를
            # 다시 열었을 때 그 flag 가 `confirmed: true` 로 남아, 아무도 답하지 않은
            # 반경이 「사람이 확정했다」로 읽힌다(원칙 4). 예전엔 같은 값이
            # `editable: false` 로도 굳어 아예 답할 수가 없었다 — 그건 2026-08-12 에
            # 없어졌지만, 거짓 확정 표시는 여전히 남으므로 이 정리는 그대로 둔다.
            flag = _exclusion_flag(r, 0, "게이트A 배제 승격 — 반경 확정")
            for k in ("confirmed", "confirmed_by_human", "제안값", "출처", "근거_시설_일치"):
                flag.pop(k, None)
            if "radius_m" in item:      # 키 생략 = 미확정 유지(exclusions 와 같은 규약)
                radius = item["radius_m"]
                if radius is not None:
                    radius = _int_in(radius, 1, 5000, f"[{q['dataset_id']}] 배제반경(m)")
                _radius_answer(r, flag, radius)

    for item in payload.get("code_prefixes") or []:
        _only_keys(item, ("dataset_id", "op_index", "prefix"), "code_prefixes")
        q = _q(questions, "code_prefix", dataset_id=item.get("dataset_id"),
               op_index=item.get("op_index"))
        prefix = item.get("prefix")
        if not isinstance(prefix, str) or not prefix.strip():
            raise RunRequestError(f"[{q['dataset_id']}] prefix 가 비어 있습니다.")
        op = by_id[q["dataset_id"]]["cleaning_ops"][q["op_index"]]
        prm = op.setdefault("params", {})
        prm["prefix"] = prefix.strip()
        prm["prefix_confirmed"] = True
        prm["prefix_confirmed_by"] = AUTO_APPROVE_SRC if auto else "human"

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _validate_weight(questions: list[dict], payload: dict) -> None:
    """게이트B 답 검증. 적용은 `_stage_args` 가 `--radius`·`--weight` 로 넘긴다.

    여기서 막는 것은 **하류에서 조용히 틀릴 것들**이다:
      · 반경 누락 → run_weight_model 이 [R] HITL 로 내려가 stdin 없이 EOFError
      · 충돌 지표 슬라이더 누락 → `--auto-weight` 가 `:352` 에서 ValueError
        (그 자리에서 죽는 건 옳다. 다만 **게이트에서 400 으로 되돌리는 게 낫다** —
         사람이 답을 고칠 수 있는 곳이 게이트뿐이다)
      · 절대값 합 0 → `apply_weight_hitl:1108` ValueError → 전 후보 점수 0
    """
    for key in payload:
        if key not in ("run_id", "radius", "slider"):
            raise RunRequestError(f"알 수 없는 필드: {key!r}")
    radius = payload.get("radius") or {}
    slider = payload.get("slider") or {}
    if not isinstance(radius, dict) or not isinstance(slider, dict):
        raise RunRequestError("radius·slider 는 {지표ID: 값} 객체여야 합니다.")

    known = {q["indicator_id"]: q for q in questions if q["kind"] == "weight"}
    need_radius = {i for i, q in known.items() if q["radius_required"]}

    unknown = sorted((set(radius) | set(slider)) - set(known))
    if unknown:
        raise RunRequestError(f"게이트에 없는 지표ID: {unknown}")
    missing = sorted(need_radius - set(radius))
    if missing:
        raise RunRequestError(f"집계반경이 빠진 지표: {missing}")
    extra = sorted(set(radius) - need_radius)
    if extra:
        raise RunRequestError(
            f"행정동 단위 지표에는 집계반경이 없습니다: {extra}")
    for iid, v in radius.items():
        _int_in(v, 1, 5000, f"[{iid}] 집계반경(m)")

    conflicted = sorted(i for i, q in known.items() if q.get("conflict"))
    unresolved = sorted(set(conflicted) - set(slider))
    if unresolved:
        raise RunRequestError(
            f"방향 판정이 충돌한 지표는 슬라이더 부호로 확정해야 합니다: {unresolved}")
    merged = {i: q["slider_proposed"] for i, q in known.items()}
    for iid, v in slider.items():
        merged[iid] = _num_in(v, -1.0, 1.0, f"[{iid}] 슬라이더")
    if sum(abs(v or 0.0) for v in merged.values()) == 0:
        raise RunRequestError(
            "전 지표 슬라이더 절대값 합이 0 입니다 — 모든 후보 점수가 0 이 됩니다.")


def _auto_answer(gate_id: str, questions: list[dict]) -> dict:
    """게이트 질문을 **AI 제안값만으로** 채운 답을 만든다(「고속 자동 분석」).

    🔴 여기서 값을 **지어내지 않는다.** 넣는 것은 질문이 이미 들고 있는 제안값뿐이고,
       제안이 없으면 없는 대로 답한다. 없는 자리를 기본값으로 메우면 그건 자동승인이
       아니라 **러너가 도메인 값을 정하는 것**이다(원칙 2·5).

    갈래마다 「제안이 없다」의 뜻이 다르다 —
      · exclusion  : 제안값 → 현재값 순으로 쓰고, 둘 다 없으면 `radius_m: null`.
                     그건 **「반경 없음」 확정**이지 건너뜀이 아니다. 키를 빼면
                     미확정으로 남아 STEP2 가 `assert_exclusions_confirmed` 로
                     멈춘다 — 자동 모드가 게이트만 지나고 다음 칸에서 죽는다.
                     🔴 **원본이 점인데 제안이 없으면 `drop`(배제 해제)으로 답한다.**
                     `null` 로 확정하면 점들의 union 이라 배제 면적이 0 이고,
                     STEP4 `_guard_zero_area` 가 몇 분 뒤에 죽인다(실측
                     `r_20260813_005` — 재활용 03 도시공원, 게이트 통과 후
                     94초 뒤 STEP4 에서 중단). 그 자리에서 반경을 **지어내면**
                     러너가 도메인 값을 정하는 것이고(원칙 2), 예전처럼
                     **거절하면** 「모두 자동승인」이 성립하지 않는다.
                     남는 답은 하나다 — `_guard_zero_area` 가 스스로 안내하는
                     「그 레이어를 hard_exclusion 에서 빼세요」. 배제를 **적용하지
                     않았다는 사실**은 role·flag·gap(`배제_해제`) 세 곳에 남으므로
                     조용히 사라지지 않는다(원칙 4). 데이터는 안 버린다 —
                     `reference_only` 라 STEP2 정제는 그대로 돌고 GIS 입력에서만
                     빠진다.
                     ⚠ 사람 게이트는 같은 자리에서 막지 않는다: 화면에
                     `source_geometry` 가 실려 있어 사람이 보고 반경을 정할 수
                     있고, 지목 배수 판정이 면 필지를 잡으면 반경 없이도 배제가
                     생기기 때문이다. `drop` 은 사람도 쓸 수 있다 — 자동 전용
                     어휘를 만들면 「자동 경로에서만 통과하는 값」이 생긴다.
                     ⚠ 판정 근거는 감리의 `exclusion_type`(폴리곤이라 우겼다)이
                     아니라 원본 파일에서 읽은 `source_geometry` 다.
      · intent     : **choice 4(위치선정 참조용)** 하나뿐이다. 1·2 는 AI 가 제안한
                     적 없는 `weight` 숫자를 요구하고, 3 은 반경까지 지어내야 하며,
                     5 는 데이터를 버린다. 4 는 감리에서만 빼고 데이터는 살린다 —
                     이 flag 를 만드는 코드가 붙여 보내는 제안(「참조용이면 감리에서
                     제외하고 위치선정 단계에서 사용」)과 같은 뜻이다.
      · code_prefix: 대조기가 낸 `suggestion`, 없으면 감리가 쓰던 `prefix` 그대로.
      · weight     : 반경이 필요한 지표는 전부 `radius_proposed` 로 채운다(빠지면
                     자식이 [R] 대화형으로 내려가 stdin 없이 EOFError). 슬라이더는
                     **제안이 있는 것만** 넣는다 — 안 넣으면 자식이 자기 제안값을
                     쓰므로 같은 값이고, `null` 을 넣으면 검증에서 400 이다.
                     방향 충돌 지표에 제안이 없으면 여기서 메우지 않는다:
                     `_validate_weight` 의 `unresolved` 가 **시끄럽게** 막는다.
    """
    if gate_id == "audit":
        exclusions, intents, prefixes = [], [], []
        for q in questions:
            if q["kind"] == "exclusion":
                r = q.get("proposed_m")
                if r is None:
                    r = q.get("radius_m")
                if r is None and q.get("source_geometry") == "point":
                    exclusions.append({"dataset_id": q["dataset_id"],
                                       "role_index": q["role_index"],
                                       "drop": True})
                    continue
                exclusions.append({"dataset_id": q["dataset_id"],
                                   "role_index": q["role_index"],
                                   "radius_m": r})
            elif q["kind"] == "intent":
                intents.append({"dataset_id": q["dataset_id"], "choice": 4})
            elif q["kind"] == "code_prefix":
                prefixes.append({"dataset_id": q["dataset_id"],
                                 "op_index": q["op_index"],
                                 "prefix": q.get("suggestion") or q.get("prefix")})
        return {"exclusions": exclusions, "intents": intents,
                "code_prefixes": prefixes}

    radius, slider = {}, {}
    for q in questions:
        if q["kind"] != "weight":
            continue
        iid = q["indicator_id"]
        if q["radius_required"]:
            radius[iid] = q.get("radius_proposed")
        if q.get("slider_proposed") is not None:
            slider[iid] = q["slider_proposed"]
    return {"radius": radius, "slider": slider}


def _run_auto_gate(run_id: str, domain: str, gate_id: str, gate: dict) -> dict:
    """게이트를 사람 대신 AI 제안값으로 통과시킨다. 반환 = 실제로 적용한 답.

    🔴 검증·적용은 `submit_gate` 와 **같은 함수**를 부른다. 자동 경로만 따로 짜면
       사람 답이었으면 400 이었을 값이 조용히 통과한다.
    """
    questions = gate.get("questions") or []
    try:
        # 🔴 답을 **만드는 것**도 try 안이다. 「AI 제안값으로는 못 채운다」는
        #    검증 실패와 같은 뜻이고, 같은 문구로 알려야 한다.
        payload = _auto_answer(gate_id, questions)
        if gate_id == "audit":
            _apply_audit(run_id, domain, questions, payload, auto=True)
        else:
            _validate_weight(questions, payload)
    except RunRequestError as e:
        # 🔴 삼키지 않는다. AI 제안값으로 못 채우는 게이트는 **사람이 봐야 하는**
        #    게이트다 — 조용히 넘기면 그 자리를 아무도 안 본 채 run 이 완주한다.
        raise _StepFailed(
            f"자동승인: AI 제안값으로 게이트 '{gate_id}' 를 채울 수 없습니다 — {e}. "
            f"맞춤형 대화 분석 모드로 다시 돌리면 이 자리를 직접 확정할 수 있습니다."
        ) from e
    _save_answer(run_id, gate_id, payload, by=AUTO_APPROVE_SRC)
    return payload


def _stage_args(run_id: str, mode: str, stage: str) -> tuple[str | None, str | None]:
    """단계에 넘길 `--radius`·`--weight`. 게이트B 답이 여기서 CLI 인자로 바뀐다.

    🔴 사람 답을 코드로 다시 해석하지 않는다. 받은 값을 그대로 문자열로 옮긴다.
       (`slider` 는 `-1~+1` 그대로 — 분해는 `apply_weight_hitl` 이 경계에서 한다)
    """
    if mode not in (MODE_HITL, MODE_FULL) or stage != "3-2":
        return (None, None)
    ans = _read_answer(run_id, "weight")
    if ans is None:                       # 게이트를 안 거치고 3-2 에 온 것 = 러너 버그
        raise RuntimeError(f"게이트B 답변이 없습니다: {_answer_path(run_id, 'weight')}")
    radius = ",".join(f"{k}={int(v)}" for k, v in (ans.get("radius") or {}).items())
    weight = ",".join(f"{k}={v}" for k, v in (ans.get("slider") or {}).items())
    return (radius or None, weight or None)


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


# ══════════════════════════════════════════════════════════════════
# 10. 실행 로그 — 내보내기 전에 마스킹한다
# ══════════════════════════════════════════════════════════════════
# `run.log` 는 자식 프로세스의 stdout+stderr 원본이다. 우리가 무엇을 찍을지
# 통제하지 않는다 — 파이프라인 모듈이 찍고, 예외 트레이스백이 찍고, 서드파티
# 라이브러리(pyogrio·geopandas)가 경고를 찍는다. 그래서 "지금 키가 안 보인다"는
# "앞으로도 안 나온다"가 아니다(원칙 5). 실측으로 확인된 것:
#   · 절대경로 다수 — `D:\B_P\...`(저장소 위치) · `C:\Users\<사용자>\...`(OS 계정명)
#   · API 키 0건 — **성공 실행에서만** 그렇다. 지오코딩·VWorld 호출이 실패하면
#     `key=` 가 붙은 요청 URL 이 트레이스백에 그대로 실릴 수 있고, 하필 그때가
#     프런트가 로그를 제일 보고 싶어 하는 순간이다.
#
# 🔴 마스킹은 **보이게** 한다. 지운 자리에 `<마스킹:NAME>` 을 남긴다 —
#    조용히 없애면 로그가 "원본"인 척하게 된다(원칙 4).

# 값이 비밀임을 이름으로 판정한다. 값 자체를 패턴으로 추측하지 않는다 —
# 키 형식은 벤더마다 다르고, 추측하면 놓치거나 멀쩡한 값을 지운다.
_SECRET_NAME_RE = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PASSWD|DSN|DATABASE_URL", re.I)

# 위 목록에 없는 출처(예: 모듈에 박힌 키)를 위한 2차 방어. 쿼리스트링 형태만 본다.
_QUERY_SECRET_RE = re.compile(
    r"((?:api_?key|service_?key|auth_?key|access_?token|key|token)=)[^&\s\"'<>]+", re.I)


def _path_re(p: str) -> re.Pattern:
    """경로 하나를 구분자·대소문자 무관 정규식으로. 윈도우는 `\\` 와 `/` 가 섞인다."""
    return re.compile("[\\\\/]".join(re.escape(s) for s in re.split(r"[\\/]", p)), re.I)


def _scrub(text: str) -> str:
    """로그에서 비밀값과 서버 로컬 경로를 지운다."""
    # 1) 실제 비밀 **값** 대조. settings 는 .env 도 읽으므로 os.environ 과 합친다.
    seen: set[str] = set()
    for src in (os.environ, vars(settings)):
        for name, val in src.items():
            if not isinstance(val, str) or len(val) < 8 or val in seen:
                continue
            if _SECRET_NAME_RE.search(name):
                seen.add(val)
                text = text.replace(val, f"<마스킹:{name}>")
    # 2) 이름을 모르는 키 — 쿼리 파라미터 자리만
    text = _QUERY_SECRET_RE.sub(r"\1<마스킹>", text)
    # 3) 서버 로컬 경로.
    #    인터프리터를 먼저 지운다 — `.venv` 가 저장소 안에 있으면 `<repo>` 에
    #    먼저 걸려 `<python>` 규칙이 못 닿는다. 파일이 아니라 **폴더**를 지운다:
    #    site-packages 경고(pyogrio 등)가 같은 폴더 아래 경로를 찍기 때문이다.
    for exe in (os.environ.get("OMNISITE_PYTHON"), sys.executable):
        if exe:
            text = _path_re(str(Path(exe).parent)).sub("<python>", text)
    text = _path_re(str(BASE_DIR)).sub("<repo>", text)
    text = _path_re(str(Path.home())).sub("<home>", text)
    return text


def read_log(run_id: str, tail: int | None = None) -> str | None:
    """마스킹한 `run.log`. 없는 run_id 면 None(라우터가 404).

    run 은 있는데 로그가 아직 없으면 **빈 문자열**이다 — 404 가 아니다.
    "run 이 없다"와 "아직 안 찍혔다"는 다른 사실이고, 폴링하는 쪽은 이 둘을
    구분할 수 있어야 한다(계약 4절).

    실행 중에도 읽는다. 쓰는 중인 파일을 읽으므로 마지막 줄이 잘려 있을 수 있다 —
    로그의 성질상 허용한다. 락을 걸면 자식 프로세스 출력이 막힌다.
    """
    if read_status(run_id) is None:
        return None
    p = run_dir(run_id) / "run.log"
    if not p.is_file():
        return ""
    text = p.read_text(encoding="utf-8", errors="replace")
    if tail is not None and tail > 0:
        text = "".join(text.splitlines(keepends=True)[-tail:])
    return _scrub(text)
