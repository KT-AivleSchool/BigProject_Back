"""
OmniSite Redis Raw Bytes Data Seeder & Staging File Loader

로컬의 data_임시/<도메인>/ 하위 파일들(data/, fixture/profiles.json 등)을
원형 바이너리 바이트 그대로 Redis에 적재하고,
파이프라인 실행 시 임시 스테이징 디렉터리로 복원하여
정본 파서(gam2_profile._read_csv 등)를 100% 동일하게 재사용할 수 있도록 지원하는 모듈입니다.
"""

import os
import logging
import redis
from app.config import settings

logger = logging.getLogger("uvicorn.error")

_REDIS_KEY_PREFIX = "omnisite:raw_bytes"


def get_redis_client() -> redis.Redis | None:
    """동기식 Redis 클라이언트 인스턴스 획득"""
    try:
        url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        client = redis.Redis.from_url(url, decode_responses=False)
        client.ping()
        return client
    except Exception as e:
        logger.warning(f"[RedisSeeder] Redis 연결 실패: {e}")
        return None


def _make_redis_key(domain: str, rel_path: str) -> str:
    normalized_path = rel_path.replace("\\", "/")
    return f"{_REDIS_KEY_PREFIX}:{domain}:{normalized_path}"


def seed_domain_data_to_redis(domain: str = "흡연", domain_dir: str | None = None) -> dict[str, bool]:
    """
    지정한 도메인의 data_임시/<domain>/ 디렉터리 내 모든 raw 파일(data/, fixture/, law/ 등)을
    원형 바이너리 바이트(raw bytes) 그대로 Redis에 적재합니다.
    """
    redis_cli = get_redis_client()
    if not redis_cli:
        logger.warning("[RedisSeeder] Redis 클라이언트를 생성할 수 없습니다. 적재를 건너뜁니다.")
        return {}

    if not domain_dir:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        domain_dir = os.path.join(base_dir, "data_임시", domain)

    if not os.path.exists(domain_dir):
        logger.warning(f"[RedisSeeder] 데이터 디렉터리가 존재하지 않습니다: {domain_dir}")
        return {}

    # 기존 오래된 패턴 키 삭제 정리
    pattern = f"{_REDIS_KEY_PREFIX}:{domain}:*"
    old_keys = redis_cli.keys(pattern)
    if old_keys:
        redis_cli.delete(*old_keys)

    results = {}
    logger.info(f"[RedisSeeder] {domain} 도메인 바이너리 시딩 시작 -> {domain_dir}")

    for root, _, files in os.walk(domain_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, domain_dir)
            key = _make_redis_key(domain, rel_path)

            try:
                with open(fpath, "rb") as f:
                    raw_bytes = f.read()

                if raw_bytes:
                    redis_cli.set(key, raw_bytes)
                    results[rel_path] = True
                    logger.info(f"  ✅ [Redis raw bytes 적재 성공] {key} ({len(raw_bytes):,} bytes)")
            except Exception as e:
                results[rel_path] = False
                logger.error(f"  ❌ [Redis raw bytes 적재 실패] {rel_path}: {e}")

    return results


def stage_domain_data_from_redis(domain: str, staging_dir: str) -> dict[str, bool]:
    """
    Redis에 적재된 raw 바이너리 바이트 데이터를 읽어 지정된 staging_dir 하위의
    <domain>/ 폴더 트리에 원형 파일들(data/, fixture/ 등)을 그대로 복원합니다.
    """
    redis_cli = get_redis_client()
    if not redis_cli:
        logger.warning("[RedisSeeder] Redis 클라이언트를 연결할 수 없습니다. 스테이징을 건너뜁니다.")
        return {}

    target_domain_dir = os.path.join(staging_dir, domain)
    os.makedirs(target_domain_dir, exist_ok=True)

    pattern = f"{_REDIS_KEY_PREFIX}:{domain}:*"
    keys = redis_cli.keys(pattern)
    results = {}

    if not keys:
        logger.warning(f"[RedisSeeder] Redis 패턴 '{pattern}'에 매칭되는 데이터가 없습니다.")
        return {}

    prefix = f"{_REDIS_KEY_PREFIX}:{domain}:"
    for key_bytes in keys:
        key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else str(key_bytes)
        rel_path = key[len(prefix):]
        raw_bytes = redis_cli.get(key)
        if raw_bytes:
            out_path = os.path.join(target_domain_dir, rel_path)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(raw_bytes)
            results[rel_path] = True

    logger.info(f"⚡ [RedisStaging] {domain} 도메인 raw 파일 {len(results)}개 스테이징 복원 완료 -> {target_domain_dir}")
    return results
