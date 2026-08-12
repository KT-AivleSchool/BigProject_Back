"""
OmniSite Redis Raw Bytes Data Seeder / Staging Restorer
=======================================================
`datasets/<도메인>/` 아래 원본 파일을 **바이트 그대로** Redis 에 넣고,
다른 곳에서 스테이징 폴더로 복원한다. 정본 파서(`gam2_profile._read_csv` 등)를
그대로 재사용하기 위해 형식을 바꾸지 않는다.

🔴 **실행 경로에 배선돼 있지 않다. 수동 도구다**(2026-08-10, PR #220 통합 판단).
   원 PR 은 ① 서버 부팅 시 `"흡연"` 을 자동 시딩하고 ② `_child_env` 가 Redis 에
   키가 있으면 `OMNISITE_DATA_ROOT` 를 스테이징 폴더로 바꾸도록 배선했는데,
   그러면 **화면1 로 올린 파일이 파이프라인에 안 들어간다** — 업로드는 디스크
   (`app/api/v1/upload.py`)에 쓰는데 파이프라인은 부팅 시점 스냅샷을 읽는다.
   예외가 안 나고 값만 옛것이 된다. 2026-08-10 에 업로드→STEP0~4→토론→PDF
   관통(`r_20260810_002`, 9분 11초)을 확인한 경로라 되돌리지 않았다.
   쓰려면 `app/tools/seed_redis.py` 로 **명시적으로** 넣고 꺼낸다.

이 모듈이 원 PR 과 다른 점 — 전부 원칙 1(조용한 실패 금지) 때문이다.
  · 도메인 기본값 `"흡연"` 제거 (원칙 2)
  · Redis 접속 실패·파일 실패를 `warning` 이 아니라 `raise`
  · `__manifest__` 키에 (상대경로 → 바이트수) 를 남기고, 복원 시 **전량·크기 대조**.
    안 맞으면 `raise` — 반만 복원된 채 파이프라인이 완주하면 지표가 조용히 틀린다.
  · 시딩 키에 **TTL 을 준다**. compose 정책이 `volatile-lru` 라 무TTL 키는
    evict 대상이 아니다 → 한도(2gb)에 닿는 순간 지오코딩 캐시 쓰기까지 OOM 이 난다.
"""

from __future__ import annotations

import json
import logging
import os

import redis

from app.config import DOMAIN_ROOT, settings

logger = logging.getLogger("uvicorn.error")

_REDIS_KEY_PREFIX = "omnisite:raw_bytes"
_MANIFEST_SUFFIX = "__manifest__"

# 기본 24시간. 시딩은 「지금 이 데이터를 저쪽에서 꺼내 쓴다」는 일회성 운반이지
# 영구 저장이 아니다. 영구로 두면 원본이 바뀌어도 옛 바이트가 계속 나간다.
DEFAULT_TTL_SEC = 24 * 3600


def get_redis_client() -> redis.Redis:
    """동기 Redis 클라이언트. 붙지 않으면 `raise` — 조용히 건너뛰지 않는다."""
    client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=False)
    client.ping()
    return client


def _make_redis_key(domain: str, rel_path: str) -> str:
    return f"{_REDIS_KEY_PREFIX}:{domain}:{rel_path.replace(os.sep, '/')}"


def seed_domain_data_to_redis(
    domain: str,
    domain_dir: str | None = None,
    ttl_sec: int | None = DEFAULT_TTL_SEC,
) -> dict[str, int]:
    """
    `datasets/<domain>/` 아래 모든 파일을 바이트 그대로 Redis 에 넣는다.

    반환: {상대경로: 바이트수}. 하나라도 실패하면 `raise` 한다 —
    부분 적재는 복원 쪽에서 「데이터가 좀 적네」로 보일 뿐 터지지 않는다.
    """
    if not domain:
        raise ValueError("domain 은 필수다. 기본값을 두면 남의 도메인에 덮어쓴다.")

    redis_cli = get_redis_client()

    # 🔴 폴더 위치는 `config.DOMAIN_ROOT` 에서만 온다. 여기서 `__file__` 로 다시
    #    계산하면 `OMNISITE_DATA_ROOT` 주입을 무시해 정본 폴더를 본다(원 PR 이 그랬다).
    if not domain_dir:
        domain_dir = str(DOMAIN_ROOT / domain)

    if not os.path.isdir(domain_dir):
        raise FileNotFoundError(f"도메인 폴더 없음: {domain_dir}")

    # 옛 키 정리 — 매니페스트까지 같이 지운다(남으면 대조가 옛 목록을 본다).
    old_keys = redis_cli.keys(f"{_REDIS_KEY_PREFIX}:{domain}:*")
    if old_keys:
        redis_cli.delete(*old_keys)

    manifest: dict[str, int] = {}
    logger.info("[RedisSeeder] %s 시딩 시작 → %s", domain, domain_dir)

    for root, _, files in os.walk(domain_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, domain_dir)
            key = _make_redis_key(domain, rel_path)

            with open(fpath, "rb") as f:
                raw = f.read()

            # 빈 파일도 넣는다. 건너뛰면 복원 쪽에서 「없는 파일」이 되어
            # FileNotFoundError 의 원인이 시딩이라는 게 안 보인다.
            try:
                redis_cli.set(key, raw, ex=ttl_sec)
            except redis.exceptions.ResponseError as e:
                # maxmemory 초과가 여기로 온다. 삼키면 부분 적재가 된다.
                raise RuntimeError(
                    f"Redis 적재 실패: {rel_path} ({len(raw):,} bytes) — {e}\n"
                    f"  maxmemory 초과일 수 있다. docker-compose 의 --maxmemory 를 확인할 것."
                ) from e

            manifest[rel_path.replace(os.sep, "/")] = len(raw)
            logger.info("  ✅ %s (%s bytes)", key, f"{len(raw):,}")

    if not manifest:
        raise RuntimeError(f"적재할 파일이 하나도 없다: {domain_dir}")

    redis_cli.set(
        _make_redis_key(domain, _MANIFEST_SUFFIX),
        json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        ex=ttl_sec,
    )
    total = sum(manifest.values())
    logger.info("[RedisSeeder] %s 완료 — %d개 · %s bytes", domain, len(manifest), f"{total:,}")
    return manifest


def stage_domain_data_from_redis(domain: str, staging_dir: str) -> dict[str, int]:
    """
    Redis 의 바이트를 `<staging_dir>/<domain>/` 아래로 원형 복원한다.

    매니페스트와 **전량·크기 대조**해서 하나라도 어긋나면 `raise`.
    TTL 만료·evict 로 일부가 사라진 경우가 여기서 잡힌다.
    """
    if not domain:
        raise ValueError("domain 은 필수다.")

    redis_cli = get_redis_client()

    manifest_raw = redis_cli.get(_make_redis_key(domain, _MANIFEST_SUFFIX))
    if not manifest_raw:
        raise RuntimeError(
            f"매니페스트 없음: {_make_redis_key(domain, _MANIFEST_SUFFIX)}\n"
            f"  시딩을 안 했거나 TTL 이 지났다. `python app/tools/seed_redis.py {domain}` 으로 다시 넣을 것."
        )
    manifest: dict[str, int] = json.loads(manifest_raw.decode("utf-8"))

    target_dir = os.path.join(staging_dir, domain)
    os.makedirs(target_dir, exist_ok=True)

    restored: dict[str, int] = {}
    missing: list[str] = []
    mismatched: list[str] = []

    for rel_path, expect_size in manifest.items():
        raw = redis_cli.get(_make_redis_key(domain, rel_path))
        if raw is None:
            missing.append(rel_path)
            continue
        if len(raw) != expect_size:
            mismatched.append(f"{rel_path} ({len(raw):,} ≠ {expect_size:,})")
            continue

        out_path = os.path.join(target_dir, *rel_path.split("/"))
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(raw)
        restored[rel_path] = len(raw)

    if missing or mismatched:
        raise RuntimeError(
            f"스테이징 복원 불완전 — 매니페스트 {len(manifest)}개 중 {len(restored)}개만 복원됐다.\n"
            f"  누락 {len(missing)}: {missing[:5]}\n"
            f"  크기불일치 {len(mismatched)}: {mismatched[:5]}\n"
            f"  TTL 만료·evict 가 흔한 원인이다. 반만 있는 채로 파이프라인을 돌리면 "
            f"지표가 조용히 틀린다(원칙 1·4)."
        )

    logger.info(
        "[RedisStaging] %s 복원 완료 — %d개 · %s bytes → %s",
        domain,
        len(restored),
        f"{sum(restored.values()):,}",
        target_dir,
    )
    return restored
