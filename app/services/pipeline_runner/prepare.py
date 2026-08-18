# -*- coding: utf-8 -*-
"""run 준비 — 격리(폴더·번호·자식 환경).

🔴 `_new_run_id` 는 폴더를 세지 않는다. `runs/run_seq.json`(날짜→마지막 발급 번호)
   의 **최고수위**를 쓴다 — 폴더를 지워도 번호가 안 되돌아간다. 폴더 개수는
   발급 이력이 아니라 **현재 상태**였고, 그래서 남의 적재 결과가 CASCADE 로
   지워졌다(2026-08-11).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from app.config import DATA_ROOT, DOMAIN_ROOT, USER_INPUT_ROOT, domain_prefix

from .conditions import _load_fixture
from .state import (
    MODE_FIXTURE,
    MODE_FULL,
    MODE_HITL,
    RUNS_ROOT,
    _IO_LOCK,
    _REPLACE_BACKOFF_S,
    _REPLACE_RETRIES,
    _SEQ_PATH,
    RunRequestError,
    run_dir,
)


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

