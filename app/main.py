import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

# DB & Redis 커넥션 인프라 수거 객체
from app.db.session import engine
from app.api.deps import redis_pool

# 라우터 Import (v1 하위 라우터 연동)
#
# 🔴 2026-08-04 — import 가 안 되는 라우터를 뺐다. 전부 실측이다(추정 아님).
#    try/except 로 감싸서 "실패하면 건너뛰기" 하지 않는다 — 라우터가 사라졌는데
#    서버는 200 을 주는 상태가 제일 나쁘다(원칙 1: 조용한 실패 금지).
from app.api.v1 import auth, audit, pipeline

# ── 🔴 폐기된 스캐폴딩 — lands·ahp (사람 확인 2026-08-04) ─────────────────────
#    두 라우터가 부르던 gis_service·ahp_service 는 다른 팀원이 **임시로 만들어둔
#    초안**이었고 **쓸 예정이 없다.** 5e55dee 에서 dummy/ 로 옮겨졌다가 삭제됐다.
#    "구현이 아직 안 된" 게 아니라 **폐기된 것**이다. 되살릴 조건이
#    "그 서비스를 만들어라"가 아니다 — 만들면 안 된다. 대체재가 이미 있다.
#      gis_service  → geopandas 로 간다 (메모리 한계 때문. S5 결론 참조)
#      ahp_service  → STEP3 가중치 구조가 이미 그 일을 한다 (gam2_weight_model.py)
#    lands.py · ahp.py 는 **삭제했다**(7f66fd9). 죽은 서비스 호출 아니면 하드코딩
#    응답이었다 — `/lock` 은 입력과 무관하게 `is_locked: True`, `/upload` 는 항상
#    `imported: 95`. 잠그지도 넣지도 않고 "했다"고 말하는 코드다(원칙 4).
#    살릴 조각(로드뷰 딥링크·경계포함 SQL)은 삭제 커밋 메시지에 남겼다.
#
# ── ⏱ 여기부터는 **폐기가 아니다.** import 시점 DB 접속 때문에 못 붙인다 ────────
#
# 🔴 simulations 는 스캐폴딩이 아니라 **다중에이전트 공청회 시뮬레이션**이다
#    (CLAUDE.md 첫 줄의 프로젝트 두 축 중 하나. app/core/sim_ai/ 547행이 엔진).
#    2026-08-04 에 내가 폐기로 잘못 분류했다가 정정했다.
#    막고 있던 `pdf_service` import(구 15행)는 **함수 안으로 옮겨 해소**했다.
#    남은 차단 요인은 딱 하나 — 아래 `sim_ai/graph.py:57` 의 import 시점 접속이다.
#    ※ 화면6(PDF) — 🔴 **아래 옛 주석은 틀렸다. 지금은 된다**(2026-08-09 실측 정정).
#      예전 주석: "`pdf_service.py`·`report_template.html` 복구 + weasyprint(GTK3) 필요".
#      2026-08-04 에 두 파일이 잠깐 지워졌던 시점 기준으로 적었고, 복구된 뒤에도
#      주석만 남았다. 실측: 두 파일 모두 **존재**하고 `pdf_service.py` 는 weasyprint 가
#      아니라 **playwright(chromium headless)** 를 쓴다. weasyprint 는 코드 참조가
#      **0회**다(import 하면 libgobject 로 실패하지만 아무도 안 부른다).
#      실제 생성도 확인했다 — 33,335 bytes, 헤더 `%PDF-`.
#      교훈: 안 고친 주석은 남이 요구사항으로 옮겨 적는다. 실제로 프런트 쪽에
#      "weasyprint GTK 미설치로 화면6 막힘"으로 전달됐다(원칙 4·5).
from app.api.v1 import simulations

#
# 🔴 upload 도 폐기가 아니다 — **앞으로 쓸 것**이다. 이슈 #203 대로
#    gam2_doc_extract.py(문서→텍스트) + gam2_ordinance_select.py(조문 분할·규제 선별)를
#    붙이는 업로드 경로가 여기로 들어온다.
from app.api.v1 import upload
#
# ── ⏱ 둘의 공통 차단 요인 — import 가 **525.7초** 걸린다 (2026-08-04 실측) ────
#    `upload.py:10` 과 `core/sim_ai/graph.py:57` 이 **모듈 최상단에서**
#    `RagVectorStorage()` 를 만든다 → `vector_db.py:32` 의 `PGVector(...)` 가
#    **import 도중에** Postgres 로 접속한다. DB 가 없으면 psycopg 연결 타임아웃
#    (::1 · 127.0.0.1 각각 × 콜렉션 2개)을 다 기다린 뒤 `vector_db.py:37` 의 except 가
#    잡고 넘어간다 — rc=0 으로 **성공은 한다.** 무한이 아니라 지연이다.
#    하지만 등록하면 uvicorn 기동이 9분 가까이 걸린다.
#    되돌릴 조건: (a) pgvector Postgres 를 띄우거나,
#                (b) `RagVectorStorage()` 를 최상단이 아니라 **요청 시점**에 만들 것.
#                (b) 가 근본이다. import 가 외부 서비스에 의존하면 안 된다.
#    🔵 `vector_db.py`·`graph.py` 는 담당이 다르다(파일 주석 `[동현님 담당]`).
#       우리가 고치지 않고 **이슈로 넘긴다** — 인계 문서:
#       obsidian 10_OmniSite/04_이슈/2026-08-04_GH이슈_import시점_외부접속.md

# ── 다인 토론(B) · HWPX 보고서 — PR #224 (민영님) ────────────────────────────
#    `stakeholders` = 이해관계자 동적 생성 + 다인 토론 그래프(app/core/stakeholder_mode/)
#    `report`       = 화면6 의 두 번째 출력 형식(HWPX). 기존 PDF 경로는 그대로 둔다.
#    둘 다 import 시점 외부접속이 없다(확인함) — 위 upload·simulations 와 사정이 다르다.
#
# 🔴 PR #224 의 원본은 `auth, lands, ahp, …` 였고 pipeline·upload 를
#    `try/except ImportError: None` 으로 감싸고 있었다. 둘 다 안 받았다:
#      · `lands`·`ahp` 는 **삭제된 파일**이다(7f66fd9, 폐기 확정). import 하면 기동이 죽는다
#      · try/except 는 라우터가 사라져도 서버가 뜨게 만든다 → 프런트엔 404 로만 보인다.
#        "실패하면 건너뛰기"는 조용한 실패다(원칙 1). 못 붙일 이유가 있으면 위처럼 **적는다**
from app.api.v1 import stakeholders, report

# Uvicorn 콘솔 로거 인스턴스 획득 (터미널에 INFO 로그가 바로 노출되도록 설정)
logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    [FastAPI Lifespan 생명주기 관리자]
    서버 구동(Startup) 시 DB/Redis 커넥션 풀 웜업 및 상태 체크
    서버 종료(Shutdown) 시 SQLAlchemy 엔진 및 Redis 풀의 비동기 커넥션을 안전하게 해제합니다.
    """
    logger.info("🚀 [Startup] OmniSite Backend Server starting up...")
    logger.info(
        f"🔗 [DB Engine] SQLAlchemy async engine initialized ({settings.PROJECT_NAME})"
    )
    logger.info("⚡ [Redis Pool] Redis connection pool initialized.")

    # 🔴 이전 서버가 죽어 'running' 인 채 남은 run 을 여기서 **한 번만** 닫는다.
    #    안 닫으면 프런트가 영원히 폴링한다(계약 4절). 예전엔 `read_status` 마다
    #    돌아서 runs/ 전수 스캔이 초당 수 회 일어났고, 그 읽기가 `_write_status` 의
    #    os.replace 와 부딪혀 WinError 5 로 run 이 조용히 죽었다(2026-08-08).
    #    판정식이 `started_at < _SERVER_BOOT` 라 **답은 부팅 시점에 이미 고정**이다.
    from app.services import pipeline_runner

    pipeline_runner.reap_orphans()
    logger.info("🧹 [Runs] 이전 서버의 중단된 run 정리 완료.")

    yield

    logger.info("🛑 [Shutdown] Server shutting down... Cleaning up connection pools.")
    try:
        await engine.dispose()
        logger.info("✅ [DB Engine] SQLAlchemy async engine disposed successfully.")
    except Exception as e:
        logger.error(f"❌ [DB Engine Error] Engine dispose failed: {e}")

    try:
        await redis_pool.disconnect()
        logger.info("✅ [Redis Pool] Redis connection pool disconnected successfully.")
    except Exception as e:
        logger.error(f"❌ [Redis Pool Error] Redis disconnect failed: {e}")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="OmniSite 스마트시티 입지선정 및 공공갈등 예측 플랫폼 통합 백엔드 API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS 미들웨어 설정 (프론트엔드 Next.js 개발 서버 연동 허용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 개발 단계 전체 허용, 상용 시 도메인 타이트닝 설정 가능
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 연결
app.include_router(
    auth.router, prefix=settings.API_V1_STR + "/auth", tags=["Authentication"]
)
# 🔴 `/simulation` 과 `/simulations` 두 prefix 로 **같은 라우터를 두 번** 등록한다 —
#    한쪽만 등록하면 프런트 경로가 조용히 404 가 된다. 둘 다 같이 처리할 것.
#    (PR #224 병합에서 복수형이 빠져 있었다. 충돌 표시 없이 사라진 자리다)
app.include_router(
    simulations.router,
    prefix=settings.API_V1_STR + "/simulation",
    tags=["AI Simulation"],
)
app.include_router(
    simulations.router,
    prefix=settings.API_V1_STR + "/simulations",
    tags=["AI Simulation"],
)
app.include_router(
    audit.router, prefix=settings.API_V1_STR + "/audit", tags=["Audit AI"]
)
app.include_router(
    upload.router,
    prefix=settings.API_V1_STR + "/upload",
    tags=["Regulation & File Upload"],
)
app.include_router(
    pipeline.router,
    prefix=settings.API_V1_STR + "/pipeline",
    tags=["Pipeline Run"],
)
# 다인 토론(B) · HWPX — PR #224
app.include_router(
    stakeholders.router,
    prefix=settings.API_V1_STR + "/stakeholders",
    tags=["Dynamic Stakeholders"],
)
app.include_router(
    report.router,
    prefix=settings.API_V1_STR + "/report",
    tags=["Report Generation"],
)


# 루트 헬스체크 엔드포인트
@app.get("/", tags=["Health Check"])
def read_root():
    return {
        "status": "online",
        "project": settings.PROJECT_NAME,
        "message": "Welcome to OmniSite Backend API Server!",
    }
