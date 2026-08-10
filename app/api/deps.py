import redis.asyncio as aioredis
from typing import AsyncGenerator
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.db.session import get_db
from app.db.base import User
from app.utils.auth_utils import decode_token

# Redis Connection Pool Singleton Instance
redis_pool = aioredis.ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)
security = HTTPBearer(auto_error=False)


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    """
    FastAPI 의존성 주입(Dependency Injection)용 비동기 Redis 클라이언트 제너레이터.
    사용이 끝나면 커넥션을 안전하게 닫아 풀에 반환합니다.
    """
    client = aioredis.Redis(connection_pool=redis_pool)
    try:
        yield client
    finally:
        await client.close()


async def get_current_user(
    auth_credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> User:
    """
    JWT Access Token 검증 및 Redis 블랙리스트 체킹을 수행하는 의존성 주입 함수.
    """
    if not auth_credentials or not auth_credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 헤더(Bearer Token)가 누락되었습니다.",
        )

    token = auth_credentials.credentials
    try:
        payload = decode_token(token)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="올바른 Access Token이 아닙니다.",
        )

    # 1. Redis 블랙리스트 (jti) 검증 (1ms 내 차단)
    jti = payload.get("jti")
    if jti:
        is_blacklisted = await redis.get(f"blacklist:{jti}")
        if is_blacklisted:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="이미 로그아웃되었거나 폐기된 토큰입니다.",
            )

    # 2. DB 유저 존재 여부 확인
    user_id = payload.get("user_id")
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="존재하지 않거나 비활성화된 사용자입니다.",
        )

    return user


__all__ = ["get_db", "get_redis", "get_current_user"]
