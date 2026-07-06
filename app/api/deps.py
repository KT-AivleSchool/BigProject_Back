from typing import Generator
from app.db.session import SessionLocal

def get_db() -> Generator:
    """
    FastAPI 의존성 주입용 DB Session generator
    각 API 요청이 시작될 때 세션을 열고, 완료 또는 에러 시 세션을 자동으로 닫습니다.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
