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

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from app.config import BASE_DIR, DOMAIN_ROOT, domain_prefix, settings

RUNS_ROOT = Path(BASE_DIR) / "runs"
SERVICES_DIR = Path(BASE_DIR) / "app" / "services"

MODE_FIXTURE = "fixture"
# 게이트 모드. 파이프라인이 **원래 갖고 있던** 사람 확정 지점에서 멈춘다.
#   fixture 는 무입력 완주(회귀 검증용)라 게이트가 없어야 한다 — 사람 입력이 끼는 순간
#   check_fixture 57/57 이 재현 불가가 된다. 두 모드를 섞지 않는 이유가 그것이다.
MODE_HITL = "hitl"
MODES = (MODE_FIXTURE, MODE_HITL)

# 서버가 뜬 시각. 이전 서버 프로세스가 남긴 'running' 을 구분하는 데 쓴다(_reap_orphans).
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
# status.json 쓰기 직렬화. 실행 스레드·폴링(_reap_orphans)·게이트 답변이 동시에 쓴다.
# Windows 의 os.replace 는 대상이 열려 있으면 PermissionError 로 터진다.
_IO_LOCK = threading.Lock()
# 이 서버 프로세스가 **지금** 돌리고 있는 것. domain -> run_id
#   409 판정을 파일이 아니라 이걸로 한다. 서버가 죽으면 비므로,
#   죽은 서버가 남긴 status.json 이 새 실행을 영원히 막지 않는다.
_ACTIVE: dict[str, str] = {}


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
    """
    p = _status_path(run_id)
    tmp = p.with_suffix(f".json.{threading.get_ident()}.tmp")
    with _IO_LOCK:
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)


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
    """
    _reap_orphans()
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
    return doc


def _reap_orphans() -> None:
    """서버가 죽어 중단된 run 을 failed 로 닫는다.

    안 하면 status 가 'running' 인 채로 남아 프런트가 **영원히 폴링한다.**
    계약 4절('succeeded 또는 failed 가 되면 멈춘다')이 지켜지지 않는다.
    판정 근거: 이 서버 부팅 시각보다 먼저 시작됐는데 아직 진행 중으로 적혀 있다
    = 이전 프로세스의 것이다.
    """
    if not RUNS_ROOT.is_dir():
        return
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
        _write_status(doc["run_id"], doc)


# ══════════════════════════════════════════════════════════════════
# 4. 픽스처 — 실행 조건의 출처
# ══════════════════════════════════════════════════════════════════
def _fixture_dir(domain: str) -> Path:
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


def _radius_arg(base: dict) -> str:
    """`--radius` 문자열을 픽스처의 지표별 radius_m 에서 조립한다.

    radius_m 이 null 인 지표(= admin, 반경 개념이 없다)는 뺀다.
    비-admin 지표가 빠지면 run_weight_model 이 [R] HITL 로 내려가 stdin 이 없어
    EOFError 로 죽는다 — 조용히 넘어가지 않으므로 그대로 둔다.
    """
    parts = [f"{iid}={v['radius_m']}"
             for iid, v in base["STEP3_가중치"].items()
             if v.get("radius_m") is not None]
    if not parts:
        raise RunRequestError("픽스처의 STEP3_가중치 에 radius_m 이 하나도 없습니다.")
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


def _proc_of(stage: str, domain: str, base: dict,
             radius: str | None = None, weight: str | None = None) -> _Proc:
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
        # `radius` 가 있다 = `_stage_args` 가 게이트B 답을 넘겼다(= hitl 모드).
        # 없으면 픽스처에서 조립한다 — 사람 개입 0회다.
        return _Proc(("3-2",), [py, _svc("run_weight_model.py"), domain]
                     + _weight_args(base, radius or _radius_arg(base), weight,
                                    "human" if radius else "fixture"))
    if stage == "4":
        # STEP4 위치 선정. 한 프로세스가 4-1·4-2·4-3 을 전부 담당한다.
        return _Proc(("4-1", "4-2", "4-3"),
                     [py, _svc("gam4_site_select.py"), domain,
                      "--spacing", str(cond["spacing"])],
                     markers=_GAM4_MARKERS)
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
def _validate_domain(domain: str) -> None:
    if not domain or Path(domain).name != domain or domain in (".", ".."):
        raise RunRequestError(f"도메인 이름이 잘못됐습니다: {domain!r}")
    if not (Path(str(DOMAIN_ROOT)) / domain).is_dir():
        raise RunRequestError(f"도메인 폴더가 없습니다: {DOMAIN_ROOT}/{domain}")


def _new_run_id() -> str:
    """r_YYYYMMDD_NNN. 같은 날짜의 기존 run 다음 번호를 쓴다."""
    day = datetime.now().strftime("%Y%m%d")
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    used = [int(m.group(1))
            for p in RUNS_ROOT.glob(f"r_{day}_*")
            if (m := re.match(rf"^r_{day}_(\d+)$", p.name))]
    return f"r_{day}_{max(used, default=0) + 1:03d}"


def _prepare_dirs(run_id: str, domain: str) -> None:
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

    _, fix_rev = _load_fixture(domain)
    pre = domain_prefix(domain)

    # 정본 step1_output 의 나머지 감리 산출물도 복사해 둔다. 파이프라인이 읽는 것은
    # reviewed 하나지만(실측), 폴백 체인(reviewed > enriched > audit_result)이 있어
    # 한 파일만 두면 나중에 폴백이 조용히 다른 경로를 타게 된다.
    live_step1 = Path(str(DOMAIN_ROOT)) / "step1_output"
    if live_step1.is_dir():
        for src in live_step1.glob(f"{pre}_*"):
            if src.is_file():
                shutil.copyfile(src, d / "step1" / src.name)

    # reviewed 는 **픽스처 것으로 덮어쓴다** — 이게 고정의 핵심이다.
    shutil.copyfile(fix_rev, d / "step1" / f"{pre}_audit_result_reviewed.json")


def _child_env(run_id: str, domain: str = "흡연") -> dict:
    env = os.environ.copy()
    d = run_dir(run_id)
    env["OMNISITE_STEP1_DIR"] = str(d / "step1")
    env["OMNISITE_STEP2_DIR"] = str(d / "step2")
    env["OMNISITE_STEP3_DIR"] = str(d / "step3")
    env["OMNISITE_STEP4_DIR"] = str(d / "step4")

    # ── Redis 데이터 스테이징 복원 & OMNISITE_DATA_ROOT 주입 ────────
    try:
        status_file = d / "status.json"
        if status_file.exists():
            st_doc = json.loads(status_file.read_text(encoding="utf-8"))
            domain = st_doc.get("domain") or domain

        from app.utils.redis_data_seeder import stage_domain_data_from_redis
        staging_dir = d / "raw_data"
        staged = stage_domain_data_from_redis(domain, str(staging_dir))
        if staged:
            env["OMNISITE_DATA_ROOT"] = str(staging_dir)
            print(f"[pipeline_runner] ⚡ Redis 스테이징 데이터 주입: OMNISITE_DATA_ROOT={staging_dir}")
    except Exception as e:
        print(f"[pipeline_runner] ⚠️ Redis 스테이징 복원 중 경고: {e}")

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


def _new_status(run_id: str, domain: str, mode: str = MODE_FIXTURE) -> dict:
    # 🔴 `gate` 키는 여기 없다. 계약 7-3 — `awaiting_hitl` 일 때만 **키가 생긴다**.
    #    항상 두고 null 을 넣으면 "게이트가 있는데 질문이 없다"로 읽힌다.
    return {
        "run_id": run_id,
        "domain": domain,
        "mode": mode,
        "status": "queued",
        "steps": [{"id": i, "label": lb, "status": "idle", "sec": None}
                  for i, lb in STEP_LABELS],
        "artifacts": {k: None for k in ARTIFACTS},
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
_PLAN: dict[str, tuple[str, ...]] = {
    MODE_FIXTURE: ("2", "3-1", "3-2", "4"),
    MODE_HITL: ("gate:audit", "2", "3-1", "propose", "gate:weight", "3-2", "4"),
}

GATE_IDS = ("audit", "weight")


def _resume_index(mode: str, gate_id: str) -> int:
    """`gate.id` 로 이어갈 위치를 계획에서 되찾는다.

    status.json 에 '어디까지 했나' 필드를 새로 두지 않는다 — 계약 3절의 스키마를
    늘리지 않으려는 것도 있지만, 그보다 **같은 사실을 두 곳에 적으면 갈리기** 때문이다.
    계획은 고정 배열이고 게이트 id 는 그 안에서 유일하므로 위치는 유도된다.
    """
    plan = _PLAN[mode]
    return plan.index(f"gate:{gate_id}") + 1


def start_run(domain: str, mode: str) -> str:
    """검증 → run 폴더 준비 → 백그라운드 실행. run_id 를 돌려준다."""
    if mode not in MODES:
        raise RunRequestError(
            f"지원하지 않는 mode 입니다: {mode!r} (가능: {', '.join(MODES)})")
    _validate_domain(domain)
    _load_fixture(domain)          # 픽스처가 없으면 여기서 400
    build_commands(domain)         # 커맨드 조립도 미리 해본다(실패를 실행 전에 낸다)
    _reap_orphans()

    with _LOCK:
        if domain in _ACTIVE:
            raise RunConflict(f"'{domain}' 은 이미 실행 중입니다 (run_id={_ACTIVE[domain]})")
        run_id = _new_run_id()
        _ACTIVE[domain] = run_id

    try:
        _prepare_dirs(run_id, domain)
        doc = _new_status(run_id, domain, mode)
        # `reviewed` 는 방금 _prepare_dirs 가 넣어서 **이미 있다.** 여기서 안 갱신하면
        # 첫 단계 전이까지 status 는 null 인데 엔드포인트는 200 을 준다 — status 가
        # 거짓말을 한다(원칙 4). 나머지 6개는 아직 없으므로 그대로 null 이다.
        _refresh_artifacts(doc)
        _write_status(run_id, doc)
    except Exception:
        with _LOCK:
            _ACTIVE.pop(domain, None)
        raise

    _spawn(run_id, domain, mode, 0)
    return run_id


def _spawn(run_id: str, domain: str, mode: str, start: int) -> None:
    threading.Thread(target=_execute, args=(run_id, domain, mode, start),
                     daemon=True).start()


def _execute(run_id: str, domain: str, mode: str, start: int = 0) -> None:
    """계획을 `start` 칸부터 돌린다. 게이트를 만나면 **멈추고 스레드가 끝난다.**"""
    doc = read_status(run_id) or _new_status(run_id, domain, mode)
    doc["status"] = "running"
    doc.pop("gate", None)          # 계약 7-3 — running 에는 gate 키가 없다
    _write_status(run_id, doc)

    base, _ = _load_fixture(domain)
    plan = _PLAN[mode]
    log_path = run_dir(run_id) / "run.log"
    paused = False
    try:
        # 이어가는 실행은 append 다. "w" 로 열면 게이트 전 로그가 사라진다 —
        # 프런트가 게이트 화면에서 보던 로그가 답변 순간 증발한다(원칙 4).
        with open(log_path, "a" if start else "w", encoding="utf-8") as log:
            for i in range(start, len(plan)):
                stage = plan[i]
                if stage.startswith("gate:"):
                    gate_id = stage.split(":", 1)[1]
                    log.write(f"\n[게이트 {gate_id}] 사람 확정 대기\n")
                    log.flush()
                    doc["status"] = "awaiting_hitl"
                    doc["gate"] = build_gate(gate_id, run_id, domain)
                    _refresh_artifacts(doc)
                    _write_status(run_id, doc)
                    paused = True
                    break
                proc = (_proc_propose(domain, base, run_id) if stage == "propose"
                        else _proc_of(stage, domain, base,
                                      *_stage_args(run_id, mode, stage)))
                _run_one(run_id, doc, proc, log)
                if stage == "3-2":
                    _assert_provenance(run_id, mode)
        if not paused:
            doc["status"] = "succeeded"
    except _StepFailed as e:
        doc["status"] = "failed"
        doc["error"] = str(e)
    except Exception as e:  # 러너 자신의 버그도 숨기지 않는다
        doc["status"] = "failed"
        doc["error"] = f"{type(e).__name__}: {e}"
    finally:
        if doc["status"] != "awaiting_hitl":
            # 🔴 게이트에서 멈춘 run 은 **끝난 게 아니다.** finished_at 을 찍지 않고
            #    _ACTIVE 에서 빼지도 않는다 — 빼면 같은 도메인으로 새 run 을 시작할 수
            #    있게 되고, 두 run 이 같은 정본 캐시·데이터를 동시에 건드린다.
            doc["finished_at"] = _now_iso()
            _refresh_artifacts(doc)
            _write_status(run_id, doc)
            with _LOCK:
                if _ACTIVE.get(domain) == run_id:
                    _ACTIVE.pop(domain, None)


class _StepFailed(Exception):
    pass


def _assert_provenance(run_id: str, mode: str) -> None:
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
    elif vs != "human":
        # 게이트B 를 거쳐 왔는데 사람 출처가 아니다. 반대 방향(과소기록)이지만
        # 역시 사실과 다르다. 실측된 경로 하나 — 답변의 `radius` 가 비면
        # `_proc_of` 가 `"human" if radius else "fixture"` 로 fixture 를 넘긴다
        # (전 지표가 admin 이면 `_validate_weight` 가 radius 를 금지하므로 도달 가능).
        raise _StepFailed(
            f"게이트B 를 거친 run 인데 값 출처가 사람이 아닙니다: value_source={vs!r}. "
            f"사람이 답했다는 사실이 산출물에서 사라집니다.")


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
    child = subprocess.Popen(
        proc.argv,
        cwd=str(BASE_DIR),
        env=_child_env(run_id),
        stdin=subprocess.DEVNULL,   # 🔴 HITL 이 새로 생기면 EOFError 로 즉시 터진다.
        stdout=subprocess.PIPE,     #    조용히 멈추는 것보다 시끄럽게 죽는 게 낫다.
        stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    assert child.stdout is not None
    for line in child.stdout:
        log.write(line)
        s = line.strip()
        if s:
            tail.append(s)
            del tail[:-40]
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

    if child.wait() != 0:
        if cur:
            _step(doc, cur)["status"] = "failed"
        _refresh_artifacts(doc)
        _write_status(run_id, doc)
        raise _StepFailed(tail[-1] if tail else f"종료 코드 {child.returncode}")

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


def _save_answer(run_id: str, gate_id: str, payload: dict) -> None:
    """사람이 무엇을 답했는지 원본 그대로 남긴다.

    규약 '값마다 누가 정했는지 남긴다' 의 게이트판이다. reviewed.json 에는
    적용 **결과**만 남고 '무엇을 건너뛰었는지'는 안 남는다 — 그건 여기 있다.
    """
    _hitl_dir(run_id).mkdir(parents=True, exist_ok=True)
    doc = {"gate": gate_id, "answered_at": _now_iso(), "answer": payload}
    _answer_path(run_id, gate_id).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_answer(run_id: str, gate_id: str) -> dict | None:
    p = _answer_path(run_id, gate_id)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))["answer"]


def _reviewed_path(run_id: str, domain: str) -> Path:
    return run_dir(run_id) / "step1" / f"{domain_prefix(domain)}_audit_result_reviewed.json"


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
#  🔴 확정분도 **보여준다. 단 수정은 못 한다**(`editable: false`). (사람 결정 2026-08-05)
#     HITL 전에 confirmed 가 되는 건 조례에서 근거를 확실히 찾았을 때뿐이라
#     고칠 이유가 없다. 그렇다고 감추면 사람은 "무엇이 이미 정해졌는지" 를 모른 채
#     남은 것만 답하게 된다 — 화면이 사실의 일부만 보여주는 것이다(원칙 4).
def _questions_audit(run_id: str, domain: str) -> list[dict]:
    p = _reviewed_path(run_id, domain)
    if not p.is_file():
        raise _StepFailed(f"감리 결과가 없습니다: {p}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    region = (doc.get("facility_inference") or {}).get("region", "")
    out: list[dict] = []

    for r in doc.get("results", []):
        did = r.get("dataset_id")
        summary = r.get("summary", "")
        roles = r.get("roles") or []

        for f in r.get("hitl_flags") or []:
            ftype = f.get("type")
            if ftype == "exclusion_radius_missing":
                idx = f.get("role_index", 0)
                role = roles[idx] if idx < len(roles) else {}
                out.append({
                    "kind": "exclusion",
                    "dataset_id": did,
                    "role_index": idx,
                    "editable": not f.get("confirmed"),
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
                })
            elif ftype == "data_intent_unclear":
                out.append({
                    "kind": "intent",
                    "dataset_id": did,
                    "editable": not f.get("confirmed"),
                    "summary": summary,
                    "message": f.get("message", ""),
                    "current_roles": [x.get("role") for x in roles],
                    "choices": [
                        {"value": 1, "label": "가점(수요)", "needs_weight": True},
                        {"value": 2, "label": "감점(민감도)", "needs_weight": True},
                        {"value": 3, "label": "배제(금지)", "needs_weight": False},
                        {"value": 4, "label": "위치선정 참조용", "needs_weight": False},
                        {"value": 5, "label": "잘못 넣음·제외", "needs_weight": False},
                    ],
                })

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
                "editable": not prm.get("prefix_confirmed"),
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
    #    (`_reap_orphans` 는 queued/running 만 닫는다 — 게이트 대기는 중단이 아니다).
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


def _apply_audit(run_id: str, domain: str, questions: list[dict], payload: dict) -> None:
    """게이트A 답을 reviewed.json 에 반영한다. **정본 함수를 그대로 부른다.**

    🔴 `radius_m` 은 `null`(반경 없음으로 확정)과 **키 생략**(건너뜀 — 미확정 유지)이
       다른 뜻이다. CLI 의 `n` 과 `s` 에 각각 대응한다.
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

    # `save_to_exclusion_cache` 가 쓰는 캐시 경로를 도메인별로 확정한다.
    # 안 부르면 다른 도메인의 캐시 파일에 쓴다(계약 7-7).
    A.set_domain(domain)

    for item in payload.get("exclusions") or []:
        q = _q(questions, "exclusion", dataset_id=item.get("dataset_id"),
               role_index=item.get("role_index"))
        if not q["editable"]:
            raise RunRequestError(
                f"[{q['dataset_id']}] 배제반경은 이미 확정된 항목입니다(수정 불가).")
        if "radius_m" not in item:
            continue                    # 건너뜀 = 미확정 유지. CLI 의 's'
        radius = item["radius_m"]
        if radius is not None:
            radius = _int_in(radius, 1, 5000, f"[{q['dataset_id']}] 배제반경(m)")
        r = by_id[q["dataset_id"]]
        flag = next(f for f in r["hitl_flags"]
                    if f.get("type") == "exclusion_radius_missing"
                    and f.get("role_index", 0) == q["role_index"])
        A.apply_radius_answer(r, flag, radius)

    for item in payload.get("intents") or []:
        q = _q(questions, "intent", dataset_id=item.get("dataset_id"))
        if not q["editable"]:
            raise RunRequestError(
                f"[{q['dataset_id']}] 데이터 용도는 이미 확정된 항목입니다(수정 불가).")
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
        A.apply_intent_answer(by_id[q["dataset_id"]], choice, weight)

    for item in payload.get("code_prefixes") or []:
        q = _q(questions, "code_prefix", dataset_id=item.get("dataset_id"),
               op_index=item.get("op_index"))
        if not q["editable"]:
            raise RunRequestError(
                f"[{q['dataset_id']}] 지역 코드는 이미 확정된 항목입니다(수정 불가).")
        prefix = item.get("prefix")
        if not isinstance(prefix, str) or not prefix.strip():
            raise RunRequestError(f"[{q['dataset_id']}] prefix 가 비어 있습니다.")
        op = by_id[q["dataset_id"]]["cleaning_ops"][q["op_index"]]
        prm = op.setdefault("params", {})
        prm["prefix"] = prefix.strip()
        prm["prefix_confirmed"] = True
        prm["prefix_confirmed_by"] = "human"

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


def _stage_args(run_id: str, mode: str, stage: str) -> tuple[str | None, str | None]:
    """단계에 넘길 `--radius`·`--weight`. 게이트B 답이 여기서 CLI 인자로 바뀐다.

    🔴 사람 답을 코드로 다시 해석하지 않는다. 받은 값을 그대로 문자열로 옮긴다.
       (`slider` 는 `-1~+1` 그대로 — 분해는 `apply_weight_hitl` 이 경계에서 한다)
    """
    if mode != MODE_HITL or stage != "3-2":
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
