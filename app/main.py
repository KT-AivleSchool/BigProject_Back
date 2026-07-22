from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

# 라우터 Import (v1 하위 라우터 연동)
from app.api.v1 import auth, lands, ahp, simulations, audit

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="OmniSite 스마트시티 입지선정 및 공공갈등 예측 플랫폼 통합 백엔드 API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
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


# 루트 헬스체크 엔드포인트
@app.get("/", tags=["Health Check"])
def read_root():
    return {
        "status": "online",
        "project": settings.PROJECT_NAME,
        "message": "Welcome to OmniSite Backend API Server!",
    }
