# -*- coding: utf-8 -*-
"""커맨드 조립 — **여기 한 곳뿐이다.**

자식 프로세스로 부르는 이유는 정본(`gam2_*`)을 import 로 끌어오면 그 모듈의
전역 상태(도메인·경로)가 러너 프로세스에 눌러앉기 때문이다. 조립 지점이 둘이
되면 CLI 와 API 가 다른 인자를 넘기기 시작한다.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from .conditions import _load_fixture, _radius_arg
from .state import SCRIPTS_DIR, SERVICES_DIR, RunRequestError
from .steps import _GAM4_MARKERS, _RUNPIPE_MARKERS


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

