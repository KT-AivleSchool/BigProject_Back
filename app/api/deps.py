import redis.asyncio as aioredis
from typing import AsyncGenerator
from app.config import settings
from app.db.session import get_db

# Redis Connection Pool Singleton Instance
redis_pool = aioredis.ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)


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


__all__ = ["get_db", "get_redis"]
