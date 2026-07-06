from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.config import settings

# 1. SQLAlchemy 비동기 연동을 위해 드라이버 문자열 포매팅
# (postgresql:// 로 시작 시 postgresql+asyncpg:// 로 치환하여 비동기 연결 보장)
database_url = settings.DATABASE_URL
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# 2. 비동기 데이터베이스 연결 엔진 생성
engine = create_async_engine(
    database_url,
    pool_pre_ping=True, # 주기적으로 연결 핑을 날려 유실된 세션을 자동 탐지 및 재수거
    echo=False          # 개발 시 SQL 쿼리 로깅이 필요하면 True로 변경 가능
)

# 3. 비동기 세션 팩토리 생성 (AsyncSession 주입 객체 빌드)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False, # 커밋 시 ORM 인스턴스의 상태 유실을 방지하기 위해 False로 고정
    autocommit=False,
    autoflush=False
)

# 4. ORM 기본 Base 클래스 선언 (SQLAlchemy 메타데이터 통합용)
Base = declarative_base()

# 5. FastAPI 라우터 의존성 주입(Dependency Injection)용 비동기 세션 제너레이터 헬퍼
async def get_db():
    """
    FastAPI 라우터 진입 시 비동기 세션을 요청별로 하나씩 열어주고,
    실행이 끝나면(성공/에러 무관) 컨텍스트를 소거 및 안전하게 닫아(Close) 줍니다.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit() # 예외가 없으면 트랜잭션 최종 커밋
        except Exception:
            await session.rollback() # 오류 시 즉시 롤백
            raise
        finally:
            await session.close() # 세션 반환 및 종료

