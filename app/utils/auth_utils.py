import uuid
from datetime import datetime, timezone, timedelta
import jwt

from app.config import settings


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """사용자 정보를 담은 JWT Access Token을 생성합니다. (jti 자동 주입)"""
    to_encode = data.copy()

    # 토큰 고유 식별자 (jti) 및 생성/만료 시각 주입
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({
        "exp": expire,
        "iat": now,
        "jti": jti,
        "type": "access",
    })

    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def create_refresh_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """재발급용 JWT Refresh Token을 생성합니다. (jti 자동 주입)"""
    to_encode = data.copy()

    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    to_encode.update({
        "exp": expire,
        "iat": now,
        "jti": jti,
        "type": "refresh",
    })

    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def decode_token(token: str) -> dict:
    """JWT 토큰을 검증하고 페이로드를 복원합니다."""
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise ValueError("토큰이 만료되었습니다.")
    except jwt.PyJWTError as e:
        raise ValueError(f"유효하지 않은 토큰입니다: {e}")
