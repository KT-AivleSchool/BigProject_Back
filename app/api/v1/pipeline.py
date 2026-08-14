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
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_user_optional
from app.db.base import RunRecord, User
from app.db.session import get_db
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
    # ↓ 「고속 자동 분석 모드」. 게이트를 **계획에서 빼는 게 아니라** 그 자리에서 AI
    #   제안값으로 답한다 — 질문도 검증기도 사람 경로와 한 글자도 다르지 않고, 무엇을
    #   승인했는지는 `hitl/<gate>.json` 과 산출물 `source` 에 남는다(원칙 4).
    #   `mode="fixture"` 는 게이트가 없어 400 이다(판정은 `runner.start_run` 한 곳).
    auto_approve: bool = False


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
                                  user_id=user.id if user else None,
                                  auto_approve=req.auto_approve)
    except runner.RunRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except runner.RunConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"run_id": run_id}


@router.get("/runs")
async def list_runs(
    mine: str = Query(..., description="지금은 `true` 하나만 정의돼 있다"),
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """마이페이지 run 이력 — **내 것 + 익명**을 최신순으로(계약 3-3).

    🔴 **`mine=true` 인데 익명 행도 돌려준다.** 이름과 내용이 어긋나 보이지만 의도다 —
       가르는 자리는 서버가 아니라 화면이고, 그러려면 **응답에 있어야** 센다.
       실제 화면은 익명을 감추고 **감춘 수를 적는다**(「로그인 없이 실행된 19건은
       표시하지 않습니다 — 지워진 것이 아니라 주인이 없는 기록입니다」).
       `WHERE user_id = :me` 로 여기서 거르면 화면은 **감춘 사실조차 모르고**
       20건이 1건으로 조용히 줄어든다(원칙 4). 이름이 아니라 계약 §3-3-1 을 따른다.
       ⚠ **남의 행은 안 준다.** 「내 것 + 익명」이지 「전부」가 아니다 — 화면이 가르는
       기준은 `is_mine` 뿐이라 남의 run 은 익명과 구분이 안 되고, 「주인이 없는
       기록」으로 세어진다. 그건 화면이 사실이 아닌 말을 하는 것이다.

    🔴 **인증은 필수다**(`POST /runs` 는 선택). 「내 것」이 뜻을 가지려면 내가 누구인지
       알아야 한다. 토큰이 없거나 죽었으면 401 — 익명으로 떨어뜨리면 모든 행이
       `is_mine: false` 가 되어 **로그인했는데 내 기록이 없는 화면**이 된다.

    🔴 `status` 는 `run_records.last_known_status` **그대로**다. 값이 `queued`·
       `succeeded`·`failed` 셋뿐인 것 자체가 정보다 — `running`·`awaiting_hitl` 을
       여기서 지어내면 정본(`status.json`)이 둘이 된다(계약 3-3).

    시각은 `.isoformat()` 그대로 내보낸다. `astimezone()` 같은 걸 태우지 않는다 —
    컬럼이 TIMESTAMPTZ 라 이미 tz 가 붙어 있고, 한 번 더 돌리면 값이 아니라
    **표기**만 바뀌어 읽는 쪽이 시차로 오해한다.

    상한: 기본 100건(`?limit=`, 최대 500). 프런트에 페이지네이션이 없어 무한히 쌓이는
    것을 그대로 부으면 화면이 죽는다. 🔴 자른 사실은 **응답에 적는다**(`total`·
    `truncated`) — 안 적으면 사용자는 옛 run 이 **지워진 줄 안다**(원칙 4).
    """
    if mine != "true":
        # 조용히 같은 응답을 주면 나중에 「거르는 줄 알았다」가 된다(원칙 1).
        raise HTTPException(
            status_code=400,
            detail=f"mine 은 'true' 만 정의돼 있습니다: {mine!r}",
        )

    scope = or_(RunRecord.user_id == user.id, RunRecord.user_id.is_(None))
    total = (await db.execute(select(func.count()).select_from(RunRecord).where(scope))).scalar_one()
    rows = (
        await db.execute(
            select(RunRecord).where(scope).order_by(RunRecord.started_at.desc()).limit(limit)
        )
    ).scalars().all()

    return {
        "runs": [
            {
                "run_id": r.run_id,
                "domain": r.domain,
                "mode": r.mode,
                "status": r.last_known_status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "is_mine": r.user_id == user.id,
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "truncated": total > len(rows),
    }


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


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: str):
    """실행 취소 → 204(본문 없음). 계약 3-4.

    🔴 **자식 프로세스를 실제로 죽이고 나서** 응답한다. status.json 만 고쳐 쓰면
       화면은 「멈췄다」고 하는데 파이프라인은 계속 돌아 정본 캐시·산출물
       디렉터리를 갈아엎는다 — 그리고 실행 스레드가 다음 단계 전이에서 그
       취소 상태를 **덮어쓴다**(원칙 4). 판단은 전부 `runner.cancel_run` 에 있다.

    🔴 **인증이 없다.** `GET /runs/{id}`·`POST .../hitl/{gate}` 와 같다.
       여기만 인증을 붙이면 **익명 run 은 영원히 취소할 수 없다** — 익명은
       미구현이 아니라 의도된 정상 상태이고(4계층 ㉠) 나중에 주인을 채우는
       경로도 없다. 즉 「나중에 로그인해서 지운다」가 성립하지 않는다.

    이미 끝난 run 은 **409** 다. 204 로 답하면 「취소했다」는 말이 되는데
    실제로는 아무 일도 안 일어났다. 없는 run_id 는 404.
    """
    try:
        runner.cancel_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"없는 run_id 입니다: {run_id}")
    except runner.RunConflict as e:
        raise HTTPException(status_code=409, detail=str(e))


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
