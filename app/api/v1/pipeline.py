# -*- coding: utf-8 -*-
"""파이프라인 실행 라우터 (픽스처 재실행 STEP2~4).

계약
  `pipeline_run_contract.md` 가 유일한 기준이다. 경로·필드명·상태값이 전부 거기 있다.

🔴 이 파일은 **얇다.** 커맨드 조립·격리·상태 기록은 전부 `pipeline_runner.py` 에 있다.
   나중에 오케스트레이터로 갈아끼울 때 라우터를 건드리지 않기 위해서다.
   status.json 과 산출물은 **가공하지 않고 그대로** 내보낸다(계약 4절).
"""

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from app.api.deps import get_current_user_optional
from app.db.base import User
from app.services import pipeline_runner as runner

router = APIRouter()


# 확장자 → Content-Type.
#
# 🔴 왜 명시하나 — starlette 의 `FileResponse` 는 `media_type` 을 안 주면
#    `mimetypes.guess_type()` 에 맡기고, 못 알아보면 **`text/plain; charset=utf-8`**
#    로 떨어진다. `.gpkg` 는 파이썬 mimetypes 에 없다. 그래서 25MB SQLite 바이너리가
#    "utf-8 텍스트"라고 적힌 채 나갔다(2026-08-04 실측).
#    내용은 바이너리인데 헤더는 텍스트라고 말하는 것 — **산출물이 거짓말하는 것이다(원칙 4)**.
#    받는 쪽이 `res.text()` 를 쓰면 안 터지고 조용히 깨진다. 이 프로젝트가 계속 당해온 유형이다.
#
#    모르는 확장자는 `text/plain` 이 아니라 `application/octet-stream` 으로 떨어뜨린다.
#    "텍스트다"라는 틀린 단정보다 "바이트다"라는 참인 진술이 낫다.
_MEDIA_TYPES: dict[str, str] = {
    ".json": "application/json",
    ".csv": "text/csv; charset=utf-8",
    # IANA 등록 타입. GeoPackage 는 SQLite 컨테이너다.
    ".gpkg": "application/geopackage+sqlite3",
    # RFC 7946. `application/json` 이 아니라 이쪽이다 — 파싱은 어느 쪽이든 되지만
    # "GeoJSON 이다"가 더 참인 진술이고, 지도 라이브러리가 타입으로 분기하기도 한다.
    ".geojson": "application/geo+json",
}


class RunRequest(BaseModel):
    domain: str
    mode: str = runner.MODE_FIXTURE
    # ↓ mode="full" 전용. 다른 모드에서 주면 400 이다 — 받아놓고 안 쓰면
    #   호출자는 반영됐다고 읽는다(원칙 4). 판정은 `runner.start_run` 한 곳에서 한다.
    #
    # user_input : 사용자 의도. STEP0.5 가 여기서 **시설·지역을 확정**한다.
    #              예 "용산구 흡연부스 부지 선정". 없으면 400 (추측하지 않는다).
    # topn       : STEP4 가 뽑을 후보 개수. 기본 20.
    #              화면4 목록의 길이이자 화면5 가 고를 수 있는 후보의 수다.
    user_input: str | None = None
    topn: int | None = None


@router.post("/runs", status_code=202)
def create_run(
    req: RunRequest,
    user: User | None = Depends(get_current_user_optional),
):
    """실행 시작 → 202 `{"run_id": ...}`. run_id 는 **백엔드가 만든다.**

    🔴 **인증은 선택이다.** 토큰을 실으면 그 run 의 주인이 되고(`run_records.user_id`),
       안 실으면 **익명 run** 으로 정상 실행된다 — 익명은 미구현이 아니라 의도된 정상
       상태다(4계층 문서 ㉠). 다만 **토큰을 실었는데 못 풀면 401** 이다(`deps.py` 의
       `get_current_user_optional` 참조) — 만료된 사람의 실행이 익명으로 새면
       마이페이지에서만 조용히 사라진다.

       주인은 **발급 시점에 한 번** 박히고 나중에 안 채워진다. 로그인 전에 시작한
       run 을 로그인 후에 내 것으로 만드는 경로는 **없다**(만들면 「누구 run 이었나」의
       정본이 둘이 된다). 그래서 이 값이 비는 것은 사고가 아니라 기록이다.
    """
    try:
        run_id = runner.start_run(req.domain, req.mode,
                                  user_input=req.user_input, topn=req.topn,
                                  user_id=user.id if user else None)
    except runner.RunRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except runner.RunConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"run_id": run_id}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    """계약 3절의 status.json 을 그대로 반환. 없는 run_id 는 404.

    `failed` 는 정상 응답이다 — 200 으로 내려보낸다. 실패를 HTTP 오류로 바꾸면
    프런트가 error 문구를 못 읽고 폴링도 못 멈춘다(계약 4절).
    """
    doc = runner.read_status(run_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"없는 run_id 입니다: {run_id}")
    return doc


@router.post("/runs/{run_id}/hitl/{gate_id}")
def submit_hitl(run_id: str, gate_id: str, payload: dict = Body(...)):
    """게이트 답변 접수 → 즉시 `running` 으로 돌아간 status 를 반환(계약 7절).

    🔴 본문을 pydantic 모델로 고정하지 않는다. 게이트마다 모양이 다르고(A 는 3종
       배열, B 는 지표별 맵), 무엇보다 **검증 기준이 그 run 의 질문 목록**이기
       때문이다 — 어떤 지표ID·dataset_id 가 유효한지는 스키마가 아니라
       `status.gate.questions` 가 정한다. 검증은 전부 러너에 있다.

    응답은 답변 **직후의 status** 다. 프런트는 이걸 받고 폴링을 재개하면 된다.
    """
    try:
        return runner.submit_gate(run_id, gate_id, payload)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"없는 run_id 입니다: {run_id}")
    except runner.RunRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except runner.RunConflict as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/runs/{run_id}/log", response_class=PlainTextResponse)
def get_run_log(run_id: str, tail: int | None = Query(None, ge=1)):
    """실행 로그(마스킹본). 실행 중에도 읽힌다. `?tail=N` 이면 마지막 N 줄.

    🔴 `FileResponse` 가 아니다 — 파일을 그대로 내보내면 안 되기 때문이다.
       비밀값·서버 로컬 경로를 지운 뒤 본문으로 만든다(`runner._scrub`).
       그래서 여기만 산출물 규칙("가공하지 않고 그대로")의 예외다.

    run 은 있는데 로그가 아직 없으면 200 + 빈 본문이다. 404 로 하면
    "없는 run" 과 구분이 안 된다.
    """
    text = runner.read_log(run_id, tail)
    if text is None:
        raise HTTPException(status_code=404, detail=f"없는 run_id 입니다: {run_id}")
    # media_type 명시 — 미지정 시 mimetypes 추측에 맡기지 않는다(`.gpkg` 건과 같은 이유).
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


@router.get("/runs/{run_id}/artifacts/{name}")
def get_artifact(run_id: str, name: str):
    """산출물 파일 그대로 전달.

    🔴 `name` 을 경로로 쓰지 않는다. `artifact_path()` 의 화이트리스트를 통해서만
       파일에 닿는다 — 여기서 join 하면 경로 조작이 열린다.
    """
    path = runner.artifact_path(run_id, name)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=f"산출물이 아직 없거나 허용되지 않는 이름입니다: {name}",
        )
    return FileResponse(
        str(path),
        filename=path.name,
        media_type=_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
    )
