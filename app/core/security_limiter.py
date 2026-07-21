import time
import redis.asyncio as aioredis
from fastapi import Request, Depends, HTTPException, status
from app.api.deps import get_redis

class LoginLockoutManager:
    """
    [로그인 보안] Redis 기반 로그인 5회 실패 시 5분 동안 계정을 잠그는 기능 (Lockout)
    """
    def __init__(self, redis_client: aioredis.Redis):
        self.r = redis_client
        self.max_attempts = 5
        self.lock_seconds = 300  # 5분 잠금

    async def check_if_locked(self, email: str):
        block_key = f"lockout:block:{email}"
        is_blocked = await self.r.exists(block_key)
        if is_blocked:
            remaining_time = await self.r.ttl(block_key)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"비밀번호 5회 오류로 계정이 잠겼습니다. {remaining_time // 60}분 {remaining_time % 60}초 후에 다시 시도해 주세요."
            )

    async def record_fail_attempt(self, email: str):
        count_key = f"lockout:count:{email}"
        attempts = await self.r.incr(count_key)
        
        if attempts == 1:
            await self.r.expire(count_key, self.lock_seconds)
            
        if attempts >= self.max_attempts:
            block_key = f"lockout:block:{email}"
            await self.r.setex(block_key, self.lock_seconds, "1")
            await self.r.delete(count_key)
            return True
        return False

    async def reset_attempts(self, email: str):
        count_key = f"lockout:count:{email}"
        block_key = f"lockout:block:{email}"
        await self.r.delete(count_key, block_key)


async def rate_limiter(request: Request, redis = Depends(get_redis)):
    """
    [보안 Rate Limiter] 1분당 특정 API 호출을 최대 5회로 제한하는 의존성 주입 가드
    """
    # 로그인 정보가 존재하면 email/ID 기준, 없으면 접속 IP 기준
    user_identifier = getattr(request.state, "user_id", request.client.host)
    endpoint = request.url.path
    
    current_minute = int(time.time() // 60)
    limit_key = f"rate_limit:{user_identifier}:{endpoint}:{current_minute}"
    
    requests_count = await redis.incr(limit_key)
    if requests_count == 1:
        await redis.expire(limit_key, 60)
        
    if requests_count > 5:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="API 요청 허용량(분당 5회)이 초과되었습니다. 1분 후에 다시 시도해 주세요."
        )
