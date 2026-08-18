# -*- coding: utf-8 -*-
"""실행 로그 — 내보내기 전에 마스킹한다.

`run.log` 는 우리가 뭘 찍을지 통제하지 않는 **자식 stdout** 이다. 지운 자리에는
표시를 남긴다 — 조용히 없애면 원본인 척한다(원칙 4).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from app.config import BASE_DIR, settings

from .state import run_dir
from .status import read_status


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
