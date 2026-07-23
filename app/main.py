import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

# DB & Redis 커넥션 인프라 수거 객체
from app.db.session import engine
from app.api.deps import redis_pool

# 라우터 Import (v1 하위 라우터 연동)
from app.api.v1 import auth, lands, ahp, simulations, audit, upload

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
app.include_router(
    lands.router, prefix=settings.API_V1_STR + "/lands", tags=["Lands & HITL"]
)
app.include_router(ahp.router, prefix=settings.API_V1_STR + "/ahp", tags=["AHP Engine"])
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


# 루트 헬스체크 엔드포인트
@app.get("/", tags=["Health Check"])
def read_root():
    return {
        "status": "online",
        "project": settings.PROJECT_NAME,
        "message": "Welcome to OmniSite Backend API Server!",
    }
