from datetime import datetime, timezone, timedelta
import jwt

from app.config import settings

def create_access_token(data: dict) -> str:
    """사용자 정보를 담은 JWT Access Token을 생성합니다."""
    to_encode = data.copy()

# 유효기간 계산
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )

    to_encode.update({"exp": expire})

# 토큰값 생성
    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
    )

    return encoded_jwt