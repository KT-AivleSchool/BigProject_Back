from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

# 라우터 Import (v1 하위 라우터 연동)
#
# 🔴 2026-08-04 — import 가 안 되는 라우터를 뺐다. 전부 실측이다(추정 아님).
#    try/except 로 감싸서 "실패하면 건너뛰기" 하지 않는다 — 라우터가 사라졌는데
#    서버는 200 을 주는 상태가 제일 나쁘다(원칙 1: 조용한 실패 금지).
from app.api.v1 import auth, audit, pipeline

# ── 🔴 폐기된 스캐폴딩 (사람 확인 2026-08-04) ────────────────────────────────
#    lands·ahp·simulations 가 부르는 gis_service·ahp_service·pdf_service 는
#    다른 팀원이 **임시로 만들어둔 초안**이었고 **쓸 예정이 없다.** 5e55dee 에서
#    dummy/ 로 옮겨졌다가 삭제됐다. 즉 "구현이 아직 안 된" 게 아니라 **폐기된 것**이다.
#    되살릴 조건이 "그 서비스를 만들어라"가 아니다 — 만들면 안 된다. 대체재가 이미 있다.
#      gis_service  → geopandas 로 간다 (메모리 한계 때문. S5 결론 참조)
#      ahp_service  → STEP3 가중치 구조가 이미 그 일을 한다 (gam2_weight_model.py)
#      pdf_service  → gam2_doc_extract.py + gam2_ordinance_select.py (이슈 #203)
#
#    lands.py · ahp.py 는 **삭제했다.** 두 파일은 죽은 서비스 호출 아니면
#    하드코딩 응답이었다 — `/lock` 은 입력과 무관하게 `is_locked: True` 를,
#    `/upload` 는 항상 `imported: 95` 를 돌려줬다. 잠그지도 넣지도 않고
#    "했다"고 말하는 코드다(원칙 4). 살릴 조각(로드뷰 딥링크 형식·경계포함 SQL)은
#    삭제 커밋 메시지에 남겼다.
# from app.api.v1 import simulations  # ModuleNotFoundError: app.services.pdf_service (simulations.py:15)
#    ⚠ simulations 는 그 에러가 **526.5초 뒤에** 뜬다(실측). 12행 `sim_ai.graph` 가
#      먼저 아래 DB 대기를 유발하고, 그게 끝나야 15행에 닿기 때문이다.
#      "왜 안 뜨지" 하고 8분 기다리게 되는 구조다 — 진짜 사유는 DB 가 아니라 pdf_service 다.
#      pdf_builder 실사용은 simulations.py:500 **한 곳뿐**이다(PDF 내보내기 = 화면6).
#      화면5만 필요하면 그 import 와 호출부만 분리하면 된다. 512행 라우터가 한 줄에 걸려 있다.
#
# ── ⏱ import 는 되지만 **525.7초** 걸리는 것 (2026-08-04 실측) ────────────────
#    `upload.py:10` 이 모듈 최상단에서 `RagVectorStorage()` 를 만든다 →
#    `vector_db.py:32` 의 `PGVector(...)` 가 **import 도중에** Postgres 로 접속한다.
#    DB 가 없으면 psycopg 연결 타임아웃(::1 · 127.0.0.1 각각 × 콜렉션 2개)을 다 기다린 뒤
#    `vector_db.py:37` 의 except 가 잡고 넘어간다 — rc=0 으로 **성공은 한다.**
#    무한이 아니라 지연이다. 하지만 등록하면 uvicorn 기동이 9분 가까이 걸린다.
#    되돌릴 조건: (a) pgvector Postgres 를 띄우거나,
#                (b) `RagVectorStorage()` 를 최상단이 아니라 **요청 시점**에 만들 것.
#                (b) 가 근본이다. import 가 외부 서비스에 의존하면 안 된다.
#    ⚠ upload 는 위 폐기 스캐폴딩과 처지가 다르다 — **앞으로 쓸 것**이다.
#      이슈 #203 대로 gam2_doc_extract.py(문서→텍스트) + gam2_ordinance_select.py
#      (조문 분할·규제 선별)를 붙이는 업로드 경로가 여기로 들어온다.
# from app.api.v1 import upload

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
# 🔴 아래 2개는 뺐다. 사유는 파일 상단 import 주석 참조 — simulations 는 폐기된
#    스캐폴딩(pdf_service), upload 는 import 지연(525.7초)이다. 성격이 다르니
#    같이 취급하지 말 것. (/lands · /ahp 는 라우터 파일째 삭제했다)
#    /simulation 과 /simulations 두 prefix 로 **같은 라우터를 두 번** 등록하고 있었다 —
#    되살릴 때 한쪽만 살리면 프런트 경로가 조용히 404 가 된다. 둘 다 같이 처리할 것.
# app.include_router(
#     simulations.router,
#     prefix=settings.API_V1_STR + "/simulation",
#     tags=["AI Simulation"],
# )
# app.include_router(
#     simulations.router,
#     prefix=settings.API_V1_STR + "/simulations",
#     tags=["AI Simulation"],
# )
app.include_router(
    audit.router, prefix=settings.API_V1_STR + "/audit", tags=["Audit AI"]
)
# app.include_router(
#     upload.router,
#     prefix=settings.API_V1_STR + "/upload",
#     tags=["Regulation & File Upload"],
# )
app.include_router(
    pipeline.router,
    prefix=settings.API_V1_STR + "/pipeline",
    tags=["Pipeline Run"],
)


# 루트 헬스체크 엔드포인트
@app.get("/", tags=["Health Check"])
def read_root():
    return {
        "status": "online",
        "project": settings.PROJECT_NAME,
        "message": "Welcome to OmniSite Backend API Server!",
    }
