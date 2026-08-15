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


async def _resolve_user(
    token: str,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> User:
    """토큰 문자열 하나를 사용자로 푼다. **푸는 데 실패하면 전부 401.**

    🔴 `get_current_user` 와 `get_current_user_optional` 이 **이 함수 하나**를 쓴다.
       검증을 복붙해서 두 벌로 두면 한쪽만 고쳐지고, 그때 느슨해진 쪽이 곧 구멍이
       된다(이 저장소가 `gam4_spatial_ops.py` 사본으로 실제로 겪은 유형이다).
       두 의존성의 차이는 **헤더가 아예 없을 때**뿐이다 — 401 이냐 익명이냐.
    """
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


async def get_current_user(
    auth_credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> User:
    """
    JWT Access Token 검증 및 Redis 블랙리스트 체킹을 수행하는 의존성 주입 함수.

    **인증 필수**다 — 헤더가 없으면 401. 동작은 예전과 한 글자도 안 바뀌었다.
    """
    if not auth_credentials or not auth_credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 헤더(Bearer Token)가 누락되었습니다.",
        )

    return await _resolve_user(auth_credentials.credentials, db, redis)


async def get_current_user_optional(
    auth_credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> User | None:
    """**선택적 인증** — 로그인 없이도 되는 자리에 쓴다.

    세 갈래이고 가운데가 없다:

    | 요청 | 결과 |
    |---|---|
    | `Authorization` 헤더 **없음** | `None` — 익명. 정상 처리한다 |
    | 유효한 access token | `User` |
    | **만료·위조·폐기·없는 사용자** | **401** |

    🔴 세 번째가 갈림길이었고 **거절로 정했다**(사람 결정 2026-08-12).
       토큰이 왔는데 못 푸는 것은 **「누구인지 모른다」가 아니라 「무언가 잘못됐다」**다.
       조용히 `None` 으로 떨어뜨리면 만료된 사람의 실행이 **익명 run 으로 기록되고**,
       화면에는 로그인 상태로 보이는데 마이페이지에서만 안 보인다 — 안 터지고
       값만 틀리는, 이 저장소가 반복해서 당해온 모양이다(원칙 1·4).
       프런트는 401 을 만나면 사람이 헤더 버튼으로 재발급한다
       (`BigProject_Front/src/lib/omnisite/client.ts:100-106` — 자동 재발급을 일부러
       안 한다. RTR 이라 재발급 실패가 전 세션을 지우기 때문이다).

    🔴 **헤더가 없는 것은 오류가 아니다.** 익명 실행은 미구현이 아니라 **의도된 정상
       상태**다(`01_설계결정\\산출물_저장구조_4계층_확정.md` ㉠ · `run_records.user_id`
       가 영구 nullable 인 이유). 여기에 필수 인증을 걸면 그 결정이 뒤집힌다.
    """
    if not auth_credentials or not auth_credentials.credentials:
        return None

    return await _resolve_user(auth_credentials.credentials, db, redis)


__all__ = ["get_db", "get_redis", "get_current_user", "get_current_user_optional"]
