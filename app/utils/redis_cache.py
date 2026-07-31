import functools
import json
import logging
from typing import Any, Callable, Optional
import redis
import redis.asyncio as aioredis
from app.config import settings

logger = logging.getLogger("uvicorn.error")

DEFAULT_TTL_SECONDS = 86400  # 기본 24시간


class RedisCacheManager:
    """
    [Redis 전역 캐싱 유틸리티]
    다른 파트 팀원들이 간단하게 JSON 캐싱, Look-Aside 패턴 및 키 관리를 수행할 수 있는 통합 도우미
    """

    def __init__(self, redis_client: Optional[aioredis.Redis] = None):
        self.redis = redis_client

    # ------------------------------------------------------------------
    # 1. 비동기 (Async) 메서드 (FastAPI 라우터 및 Async 서비스용)
    # ------------------------------------------------------------------
    async def get_json(self, key: str) -> Optional[Any]:
        """
        [비동기] Redis에서 키를 조회하여 JSON 데코딩 객체로 반환
        """
        if not self.redis:
            return None
        try:
            val = await self.redis.get(key)
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"⚠️ [RedisCacheManager.get_json Error] key={key}: {e}")
        return None

    async def set_json(
        self, key: str, value: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> bool:
        """
        [비동기] 객체를 JSON 문자열로 직렬화하여 TTL과 함께 저장
        """
        if not self.redis:
            return False
        try:
            payload = json.dumps(value, ensure_ascii=False)
            await self.redis.set(key, payload, ex=ttl_seconds)
            return True
        except Exception as e:
            logger.error(f"⚠️ [RedisCacheManager.set_json Error] key={key}: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """[비동기] 키 삭제"""
        if not self.redis:
            return False
        try:
            await self.redis.delete(key)
            return True
        except Exception as e:
            logger.error(f"⚠️ [RedisCacheManager.delete Error] key={key}: {e}")
            return False

    # ------------------------------------------------------------------
    # 2. 동기 (Sync) 메서드 (백그라운드 스레드 및 일반 파이썬 모듈용)
    # ------------------------------------------------------------------
    @staticmethod
    def get_json_sync(key: str) -> Optional[Any]:
        """
        [동기] Redis에서 키를 조회하여 JSON 데코딩 객체로 반환
        """
        try:
            r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            val = r.get(key)
            r.close()
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"⚠️ [RedisCacheManager.get_json_sync Error] key={key}: {e}")
        return None

    @staticmethod
    def set_json_sync(
        key: str, value: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> bool:
        """
        [동기] 객체를 JSON 직렬화하여 TTL과 함께 저장
        """
        try:
            r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            payload = json.dumps(value, ensure_ascii=False)
            r.set(key, payload, ex=ttl_seconds)
            r.close()
            return True
        except Exception as e:
            logger.error(f"⚠️ [RedisCacheManager.set_json_sync Error] key={key}: {e}")
            return False

    @staticmethod
    def delete_sync(key: str) -> bool:
        """[동기] 키 삭제"""
        try:
            r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            r.delete(key)
            r.close()
            return True
        except Exception as e:
            logger.error(f"⚠️ [RedisCacheManager.delete_sync Error] key={key}: {e}")
            return False


# ------------------------------------------------------------------
# 3. 파이썬 데코레이터 (Look-Aside Caching Decorator)
# ------------------------------------------------------------------
def redis_cache(prefix: str, ttl_seconds: int = DEFAULT_TTL_SECONDS):
    """
    함수 결과값을 Redis에 자동으로 캐싱해주는 파이썬 데코레이터
    사용예:
    @redis_cache(prefix="geocode", ttl_seconds=86400)
    def geocode_address(address: str):
        ...
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 캐시 키 생성 (prefix + 인자값 조합)
            args_str = "_".join(str(a) for a in args)
            kwargs_str = "_".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
            key = f"cache:{prefix}:{args_str}_{kwargs_str}".strip("_")

            # 1. Redis 캐시 조회
            cached_val = RedisCacheManager.get_json_sync(key)
            if cached_val is not None:
                return cached_val

            # 2. 캐시 미스 시 실제 함수 실행
            result = func(*args, **kwargs)

            # 3. 결과값 Redis에 저장 후 리턴
            if result is not None:
                RedisCacheManager.set_json_sync(key, result, ttl_seconds)

            return result

        return wrapper

    return decorator
