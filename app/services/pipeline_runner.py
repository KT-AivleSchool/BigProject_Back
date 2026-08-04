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

# 서버가 뜬 시각. 이전 서버 프로세스가 남긴 'running' 을 구분하는 데 쓴다(_reap_orphans).
_SERVER_BOOT = datetime.now()

_LOCK = threading.Lock()
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
    """원자적 기록. 폴링과 겹쳐도 반쯤 쓰인 JSON 을 읽지 않게 한다."""
    p = _status_path(run_id)
    tmp = p.with_suffix(".json.tmp")
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


def build_commands(domain: str) -> list[_Proc]:
    """픽스처 재실행(STEP2~4) 커맨드. 값은 전부 픽스처에서 온다.

    CLAUDE.md 의 표준 CLI 와 다른 점 두 가지 — 둘 다 의도한 것이다:
      · `--auto-radius` 를 쓰지 않는다. 쓰면 `radius_conf["_confirmed"]` 가 안 찍힌다
        (run_weight_model.py:283). 픽스처는 `--radius` 로 고정한 실행이다.
      · `--no-diag --bootstrap 0` 을 쓰지 않는다. 픽스처가 기록한 실행 조건에 없다.
        (진단은 가중치와 무관하지만, 안 재본 것을 같다고 단정하지 않는다 — 원칙 5)
    """
    base, _ = _load_fixture(domain)
    cond = base["조건"]
    py = _python_exe()

    def svc(name: str) -> str:
        return str(SERVICES_DIR / name)

    return [
        # STEP2 정제. facility·region 은 reviewed.json 에서 스스로 읽는다.
        _Proc(("2",), [py, svc("gam2_clean_data.py"), domain]),

        # STEP3 후보 필지. --facility/--region 을 주지 않는다 —
        # _facility_of()/_region_of() 가 reviewed.json 에서 읽으므로 값이 같고,
        # 주면 그 순간 도메인 값이 러너에 박힌다.
        _Proc(("3-1",), [py, svc("make_parcel_candidates.py"), domain]),

        # STEP3 가중치. --radius 가 비-admin 전 지표를 덮으므로 [R] HITL 이 비고,
        # --auto-weight 가 [W] HITL 을 건너뛴다 → 무입력 완주.
        _Proc(("3-2",), [
            py, svc("run_weight_model.py"), domain,
            "--candidates", cond["candidates"],
            "--alpha", str(cond["alpha"]),
            "--decay", cond["decay"]["func"],
            "--sigma-ratio", str(cond["decay"]["sigma_ratio"]),
            "--scale", cond["scale"],
            "--radius", _radius_arg(base),
            "--auto-weight",
        ]),

        # STEP4 위치 선정. 한 프로세스가 4-1·4-2·4-3 을 전부 담당한다.
        _Proc(("4-1", "4-2", "4-3"),
              [py, svc("gam4_site_select.py"), domain,
               "--spacing", str(cond["spacing"])],
              markers=_GAM4_MARKERS),
    ]


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


def _child_env(run_id: str) -> dict:
    env = os.environ.copy()
    d = run_dir(run_id)
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


def _new_status(run_id: str, domain: str) -> dict:
    return {
        "run_id": run_id,
        "domain": domain,
        "mode": MODE_FIXTURE,
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
# 8. 실행
# ══════════════════════════════════════════════════════════════════
def start_run(domain: str, mode: str) -> str:
    """검증 → run 폴더 준비 → 백그라운드 실행. run_id 를 돌려준다."""
    if mode != MODE_FIXTURE:
        raise RunRequestError(f"지원하지 않는 mode 입니다: {mode!r} (현재 'fixture' 뿐)")
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
        doc = _new_status(run_id, domain)
        # `reviewed` 는 방금 _prepare_dirs 가 넣어서 **이미 있다.** 여기서 안 갱신하면
        # 첫 단계 전이까지 status 는 null 인데 엔드포인트는 200 을 준다 — status 가
        # 거짓말을 한다(원칙 4). 나머지 6개는 아직 없으므로 그대로 null 이다.
        _refresh_artifacts(doc)
        _write_status(run_id, doc)
    except Exception:
        with _LOCK:
            _ACTIVE.pop(domain, None)
        raise

    threading.Thread(target=_execute, args=(run_id, domain), daemon=True).start()
    return run_id


def _execute(run_id: str, domain: str) -> None:
    """프로세스를 순서대로 돌리며 단계 전이를 status.json 에 기록한다."""
    doc = read_status(run_id) or _new_status(run_id, domain)
    doc["status"] = "running"
    _write_status(run_id, doc)

    log_path = run_dir(run_id) / "run.log"
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            for proc in build_commands(domain):
                _run_one(run_id, doc, proc, log)
        doc["status"] = "succeeded"
    except _StepFailed as e:
        doc["status"] = "failed"
        doc["error"] = str(e)
    except Exception as e:  # 러너 자신의 버그도 숨기지 않는다
        doc["status"] = "failed"
        doc["error"] = f"{type(e).__name__}: {e}"
    finally:
        doc["finished_at"] = _now_iso()
        _refresh_artifacts(doc)
        _write_status(run_id, doc)
        with _LOCK:
            if _ACTIVE.get(domain) == run_id:
                _ACTIVE.pop(domain, None)


class _StepFailed(Exception):
    pass


def _run_one(run_id: str, doc: dict, proc: _Proc, log) -> None:
    log.write(f"\n$ {' '.join(proc.argv)}\n")
    log.flush()

    cur = proc.step_ids[0]
    _step(doc, cur)["status"] = "running"
    started = time.perf_counter()
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
        _step(doc, cur)["status"] = "failed"
        _refresh_artifacts(doc)
        _write_status(run_id, doc)
        raise _StepFailed(tail[-1] if tail else f"종료 코드 {child.returncode}")

    _step(doc, cur).update(status="done", sec=round(time.perf_counter() - started, 2))
    # 마커를 못 본 나머지 단계 — 프로세스는 정상 종료했으니 done 이다.
    # 다만 **소요 시간은 지어내지 않는다**(sec=null). 원칙 4.
    for sid in proc.step_ids:
        if _step(doc, sid)["status"] == "idle":
            _step(doc, sid)["status"] = "done"
    _refresh_artifacts(doc)
    _write_status(run_id, doc)


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
