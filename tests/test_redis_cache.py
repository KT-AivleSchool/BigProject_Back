import pytest
from app.utils.redis_cache import RedisCacheManager, redis_cache


def test_redis_cache_sync_manager():
    """
    RedisCacheManager 동기 JSON 덤프 & 조회 단위 테스트
    """
    test_key = "test_unit_sync_key"
    test_payload = {"status": "success", "items": ["A", "B", "C"]}

    # 1. 캐시 저장
    saved = RedisCacheManager.set_json_sync(test_key, test_payload, ttl_seconds=60)
    assert saved is True

    # 2. 캐시 조회
    cached = RedisCacheManager.get_json_sync(test_key)
    assert cached is not None
    assert cached["status"] == "success"
    assert cached["items"] == ["A", "B", "C"]

    # 3. 캐시 삭제
    deleted = RedisCacheManager.delete_sync(test_key)
    assert deleted is True

    # 4. 삭제 확인
    assert RedisCacheManager.get_json_sync(test_key) is None


def test_redis_cache_decorator():
    """
    @redis_cache 파이프라인 데코레이터 자동 캐싱 테스트
    """
    call_counter = {"count": 0}

    @redis_cache(prefix="test_dec", ttl_seconds=60)
    def heavy_calculation(val: int):
        call_counter["count"] += 1
        return {"input": val, "result": val * 100}

    # 1회차 호출: 실제 함수 실행
    res1 = heavy_calculation(5)
    assert res1["result"] == 500
    assert call_counter["count"] == 1

    # 2회차 호출: 함수 재실행 없이 Redis 캐시에서 리턴
    res2 = heavy_calculation(5)
    assert res2["result"] == 500
    assert call_counter["count"] == 1  # 카운터 증가하지 않음!

    # Cleanup
    RedisCacheManager.delete_sync("cache:test_dec:5")
