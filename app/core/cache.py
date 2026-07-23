import functools
import hashlib
import json
import logging
from typing import Callable
import redis.asyncio as aioredis
from app.api.deps import redis_pool

logger = logging.getLogger("uvicorn.error")


def _generate_cache_key(
    func: Callable, args: tuple, kwargs: dict, prefix: str = ""
) -> str:
    """
    모듈명, 함수명, 인자값을 조합하여 고유한 캐시 키(Cache Key)를 생성합니다.
    """
    filtered_kwargs = {}
    for k, v in kwargs.items():
        if k in ("db", "session", "request", "response", "current_user"):
            continue
        filtered_kwargs[k] = v

    raw_key = f"{func.__module__}:{func.__qualname__}:{args}:{sorted(filtered_kwargs.items())}"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]

    prefix_part = f"{prefix}:" if prefix else "fastapi_cache:"
    return f"{prefix_part}{func.__name__}:{key_hash}"


def cache(expire: int = 300, prefix: str = "cache"):
    """
    [Redis API 응답 캐싱 데코레이터]
    - expire: 캐시 만료 시간(초 단위, 기본 300초 / 5분)
    - prefix: 캐시 키 네임스페이스 접두사
    GET 엔드포인트에 적용 시 캐시 HIT 시 Redis에서 즉시 응답하며, MISS 시 로직 실행 후 결과 캐싱.
    Redis 서버 장애 시 에러 없이 기존 로직을 통과시키는 Graceful Fallback 탑재.
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            cache_key = _generate_cache_key(func, args, kwargs, prefix=prefix)

            # 1. Redis 캐시 조회 시도 (Cache HIT 파이프라인)
            try:
                async with aioredis.Redis(connection_pool=redis_pool) as redis:
                    cached_data = await redis.get(cache_key)
                    if cached_data:
                        logger.info(f"⚡ [Cache HIT] key='{cache_key}' (TTL={expire}s)")
                        return json.loads(cached_data)
            except Exception as e:
                logger.warning(
                    f"⚠️ [Cache Error] Redis connection failed, bypassing cache: {e}"
                )

            # 2. Cache MISS: 원본 비동기 함수/로직 실행
            result = await func(*args, **kwargs)

            # 3. 결과 캐싱 시도 (JSON 직렬화 및 TTL 설정)
            if result is not None:
                try:
                    async with aioredis.Redis(connection_pool=redis_pool) as redis:
                        serializable_result = (
                            result.model_dump()
                            if hasattr(result, "model_dump")
                            else (result.dict() if hasattr(result, "dict") else result)
                        )
                        json_str = json.dumps(
                            serializable_result, ensure_ascii=False, default=str
                        )
                        await redis.set(cache_key, json_str, ex=expire)
                        logger.info(
                            f"💾 [Cache MISS -> STORED] key='{cache_key}' (ex={expire}s)"
                        )
                except Exception as e:
                    logger.warning(
                        f"⚠️ [Cache Store Warning] Failed to write cache: {e}"
                    )

            return result

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return wrapper
        return sync_wrapper

    return decorator


async def invalidate_cache_prefix(prefix_pattern: str):
    """
    특정 키 패턴(예: 'cache:list_regulations:*')에 해당하는 모든 캐시를 일괄 파기합니다.
    데이터 CUD(생성/수정/삭제) 발생 시 캐시 무효화 헬퍼 함수.
    """
    try:
        async with aioredis.Redis(connection_pool=redis_pool) as redis:
            keys = await redis.keys(f"{prefix_pattern}*")
            if keys:
                await redis.delete(*keys)
                logger.info(
                    f"🧹 [Cache Invalidate] Cleared {len(keys)} keys matching pattern '{prefix_pattern}*'"
                )
    except Exception as e:
        logger.warning(
            f"⚠️ [Cache Invalidate Warning] Failed to clear cache pattern: {e}"
        )
