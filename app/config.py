import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # API 및 서버 기본 설정
    PROJECT_NAME: str = "OmniSite FastAPI Monolith"
    API_V1_STR: str = "/api/v1"

    # 데이터베이스 설정 (로컬 sqlite 메모리를 fallback으로 셋업하여 CI 환경 대응)
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/omnisite"
    )
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # AI 및 외부 연동 API 설정
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    KAKAO_REST_API_KEY: str = os.getenv("KAKAO_REST_API_KEY", "")
    VWORLD_API_KEY: str = os.getenv("VWORLD_API_KEY", "")

    # 보안 및 JWT 인증 설정
    SECRET_KEY: str = os.getenv("SECRET_KEY", "SUPER_SECRET_TOKEN_OMNISITE_2026_KEY")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 1주일

    # pydantic_settings v2 규격 설정
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
