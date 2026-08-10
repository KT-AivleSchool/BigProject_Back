from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import asyncio
import redis.asyncio as aioredis
import bcrypt

from app.api.deps import get_db, get_redis, get_current_user
from app.config import settings
from app.db.base import User
from app.utils.auth_utils import create_access_token, create_refresh_token, decode_token
from app.schemas.auth import (
    UserRegister,
    UserLogin,
    TokenResponse,
    TokenRefreshRequest,
    LogoutResponse,
    UserResponse,
)
from app.core.security_limiter import LoginLockoutManager

router = APIRouter()
security = HTTPBearer(auto_error=False)


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register_user(user: UserRegister, db: AsyncSession = Depends(get_db)):
    """
    [Cj(찬진) 파트] 신규 구정 관리자 및 실무자 회원가입
    """
    # email/ID 중복 검사
    stmt = select(User).where(User.email == user.email)
    result = await db.execute(stmt)
    existing_user = result.scalars().first()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이미 존재하는 이메일입니다.",
        )

    # 비밀번호 암호화 (CPU-bound bcrypt 작업 스레드 풀 격리)
    salt = await asyncio.to_thread(bcrypt.gensalt)
    hashed_pw_bytes = await asyncio.to_thread(
        bcrypt.hashpw, user.password.encode("utf-8"), salt
    )
    hashed_pw = hashed_pw_bytes.decode("utf-8")

    # 회원정보 생성
    new_user = User(email=user.email, hashed_password=hashed_pw, username=user.username)

    try:
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="회원가입 처리 중 데이터베이스 오류가 발생했습니다.",
        )

    return new_user


@router.post("/login", response_model=TokenResponse)
async def login_user(
    credentials: UserLogin,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    [Cj(찬진) 파트] Dual Token(Access + Refresh) 발급 로그인 및 Redis 저장
    """
    lock_manager = LoginLockoutManager(redis)

    # 1. 로그인 시도 전 차단 여부 선제 확인
    await lock_manager.check_if_locked(credentials.email)

    stmt = select(User).where(User.email == credentials.email)
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user:
        attempts = await lock_manager.record_fail_attempt(credentials.email)
        if attempts >= 5:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="비밀번호 5회 오류로 계정이 잠겼습니다. 5분 후에 다시 시도해 주세요.",
            )
        elif attempts >= 3:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"이메일 혹은 패스워드가 올바르지 않습니다. (현재 {attempts}회 실패: 5회 실패 시 5분 동안 계정이 잠깁니다.)",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 혹은 패스워드가 올바르지 않습니다.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="비활성화된 계정입니다."
        )

    is_password_correct = await asyncio.to_thread(
        bcrypt.checkpw,
        credentials.password.encode("utf-8"),
        user.hashed_password.encode("utf-8"),
    )

    if not is_password_correct:
        attempts = await lock_manager.record_fail_attempt(credentials.email)
        if attempts >= 5:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="비밀번호 5회 오류로 계정이 잠겼습니다. 5분 후에 다시 시도해 주세요.",
            )
        elif attempts >= 3:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"이메일 혹은 패스워드가 올바르지 않습니다. (현재 {attempts}회 실패: 5회 실패 시 5분 동안 계정이 잠깁니다.)",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 혹은 패스워드가 올바르지 않습니다.",
        )

    # 로그인 성공 시 실패 카운트 리셋
    await lock_manager.reset_attempts(credentials.email)

    token_payload = {
        "sub": user.email,
        "username": user.username,
        "user_id": user.id,
    }

    # 듀얼 토큰 생성 (Access 15분, Refresh 7일)
    access_token = create_access_token(token_payload)
    refresh_token = create_refresh_token(token_payload)

    # Refresh Token 페이로드에서 jti 추출 후 Redis 저장 (TTL: 7일)
    refresh_payload = decode_token(refresh_token)
    refresh_jti = refresh_payload["jti"]
    ttl_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400

    await redis.set(f"refresh_token:{user.id}:{refresh_jti}", refresh_token, ex=ttl_seconds)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    }


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    req: TokenRefreshRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    [Cj(찬진) 파트] Refresh Token Rotation (RTR) 기반 Access & Refresh Token 갱신
    - 이미 파기되거나 사용된 구형 Refresh Token 사용 시 Family Revocation (유저 전체 세션 강제 무효화)
    """
    try:
        payload = decode_token(req.refresh_token)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"유효하지 않은 Refresh Token입니다: {e}",
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh Token이 아닙니다.",
        )

    user_id = payload.get("user_id")
    jti = payload.get("jti")
    redis_key = f"refresh_token:{user_id}:{jti}"

    # 1. Redis에서 토큰 유효성 확인
    stored_token = await redis.get(redis_key)

    # RTR 탈취 감지 (Family Revocation): 이미 파기된 구형 토큰 인입 시 해당 유저의 모든 세션 삭제
    if not stored_token:
        pattern = f"refresh_token:{user_id}:*"
        keys = await redis.keys(pattern)
        if keys:
            await redis.delete(*keys)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="보안상 유효하지 않거나 이미 사용된 Refresh Token입니다. 모든 세션이 종료되었으니 다시 로그인해 주세요.",
        )

    # 유저 활성화 여부 확인
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user or not user.is_active:
        await redis.delete(redis_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="존재하지 않거나 비활성화된 계정입니다.",
        )

    # 2. 기존 Refresh Token 삭제 (RTR)
    await redis.delete(redis_key)

    token_payload = {
        "sub": user.email,
        "username": user.username,
        "user_id": user.id,
    }

    # 3. 신규 듀얼 토큰 생성 및 Redis 신규 등록
    new_access_token = create_access_token(token_payload)
    new_refresh_token = create_refresh_token(token_payload)

    new_refresh_payload = decode_token(new_refresh_token)
    new_jti = new_refresh_payload["jti"]
    ttl_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400

    await redis.set(f"refresh_token:{user.id}:{new_jti}", new_refresh_token, ex=ttl_seconds)

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "expires_in_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    }


@router.post("/logout", response_model=LogoutResponse)
async def logout_user(
    current_user: User = Depends(get_current_user),
    auth_credentials: HTTPAuthorizationCredentials | None = Depends(security),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    [Cj(찬진) 파트] 로그아웃: Access Token 블랙리스트 등록 & 해당 유저의 Refresh Token 삭제
    """
    if auth_credentials and auth_credentials.credentials:
        token = auth_credentials.credentials
        try:
            payload = decode_token(token)
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                now_ts = int(datetime.now(timezone.utc).timestamp())
                ttl = max(1, exp - now_ts)
                # Access Token을 블랙리스트에 등록하여 남은 만료 시간 동안 차단
                await redis.set(f"blacklist:{jti}", "1", ex=ttl)
        except Exception:
            pass

    # 유저의 모든 Refresh Token 삭제
    pattern = f"refresh_token:{current_user.id}:*"
    keys = await redis.keys(pattern)
    if keys:
        await redis.delete(*keys)

    return {"message": "성공적으로 로그아웃되었습니다."}
