import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api.v1 import pipeline
from app.utils.memory_pubsub import pipeline_pubsub
import asyncio

logger = logging.getLogger("uvicorn.error")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    [FastAPI Lifespan]
    In-Memory Pub/Sub 큐에 이벤트 루프를 바인딩하여 
    SSE 통신이 동기/비동기 혼합 환경에서 뻗지 않도록 설정합니다.
    """
    logger.info("🚀 [Startup] OmniSite Backend Server starting up...")
    pipeline_pubsub.loop = asyncio.get_running_loop()
    
    yield
    
    logger.info("🛑 [Shutdown] Server shutting down...")

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="OmniSite 스마트시티 입지선정 및 공공갈등 예측 플랫폼 통합 백엔드 API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    pipeline.router,
    prefix=settings.API_V1_STR + "/pipeline",
    tags=["GAM2 Pipeline"],
)

@app.get("/", tags=["Health Check"])
def read_root():
    return {
        "status": "online",
        "project": settings.PROJECT_NAME,
        "message": "Welcome to OmniSite Backend API Server!",
    }
