import os
import sys

# 프로젝트 루트 경로를 sys.path에 추가 (python 직접 실행 시 ModuleNotFoundError 방지)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

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


if __name__ == "__main__":
    print("\n==================================================")
    print("🚀 RedisCacheManager 및 @redis_cache 테스트 직접 실행 중...")
    print("==================================================")

    print("\n1. 동기 캐시 덤프/조회 테스트 실행...")
    test_redis_cache_sync_manager()
    print("  ✅ 동기 캐시 저장, 조회, 삭제 100% 성공!")

    print("\n2. @redis_cache 데코레이터 자동 캐싱 테스트 실행...")
    test_redis_cache_decorator()
    print("  ✅ 2회차 호출 시 함수 재실행 없이 Redis 캐시 리턴 100% 성공!")

    print("\n🎉 모든 테스트가 깔끔하게 완료되었습니다!\n")
