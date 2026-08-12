#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OmniSite Redis 원본 데이터 운반 도구 (수동)
==========================================
`datasets/<도메인>/` 를 Redis 에 바이트로 넣고(seed), 다른 곳에서 폴더로 꺼낸다(stage).
파일이 없는 기계에서 파이프라인을 돌려야 할 때 쓴다.

  적재:  python app/tools/seed_redis.py seed  <도메인> [--ttl 86400]
  복원:  python app/tools/seed_redis.py stage <도메인> <복원폴더>
  목록:  python app/tools/seed_redis.py list  <도메인>

🔴 **자동으로 안 돈다.** 서버 부팅이나 파이프라인 실행이 이걸 부르지 않는다 —
   이유는 `app/utils/redis_data_seeder.py` 첫 주석에 있다(업로드가 안 보이게 된다).
   복원한 폴더를 쓰려면 `OMNISITE_DATA_ROOT` 를 **직접** 그 폴더로 지정한다.
   그러면 「내가 옛 스냅샷을 쓰고 있다」가 명령줄에 보인다.
"""

import argparse
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.utils.redis_data_seeder import (  # noqa: E402
    DEFAULT_TTL_SEC,
    seed_domain_data_to_redis,
    stage_domain_data_from_redis,
)


def _human(n: int) -> str:
    return f"{n:,} bytes ({n / 1024 / 1024:.1f} MB)"


def main() -> None:
    p = argparse.ArgumentParser(description="OmniSite Redis 원본 데이터 운반")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="도메인 폴더 → Redis")
    s.add_argument("domain", help="도메인명 (예: 흡연). 기본값 없음 — 명시할 것")
    s.add_argument("--dir", default=None, help="도메인 폴더 직접 지정 (기본: DOMAIN_ROOT/<도메인>)")
    s.add_argument(
        "--ttl",
        type=int,
        default=DEFAULT_TTL_SEC,
        help=f"초. 기본 {DEFAULT_TTL_SEC}. 0 이면 무TTL — "
        "🔴 volatile-lru 정책에선 evict 대상이 아니라 다른 캐시 쓰기를 OOM 으로 막는다",
    )

    t = sub.add_parser("stage", help="Redis → 폴더 복원 (매니페스트 전량 대조)")
    t.add_argument("domain")
    t.add_argument("staging_dir", help="복원 위치. <복원폴더>/<도메인>/ 아래로 풀린다")

    ls = sub.add_parser("list", help="적재된 매니페스트 확인")
    ls.add_argument("domain")

    a = p.parse_args()

    if a.cmd == "seed":
        ttl = a.ttl if a.ttl > 0 else None
        if ttl is None:
            print("⚠️  무TTL 로 적재한다. volatile-lru 에선 걷어낼 수 없다.")
        res = seed_domain_data_to_redis(a.domain, domain_dir=a.dir, ttl_sec=ttl)
        print(f"✅ 적재 {len(res)}개 · {_human(sum(res.values()))}")

    elif a.cmd == "stage":
        res = stage_domain_data_from_redis(a.domain, a.staging_dir)
        print(f"✅ 복원 {len(res)}개 · {_human(sum(res.values()))} → {a.staging_dir}")
        print(f"   쓰려면: set OMNISITE_DATA_ROOT={os.path.abspath(a.staging_dir)}")

    elif a.cmd == "list":
        import json

        from app.utils.redis_data_seeder import (
            _MANIFEST_SUFFIX,
            _make_redis_key,
            get_redis_client,
        )

        raw = get_redis_client().get(_make_redis_key(a.domain, _MANIFEST_SUFFIX))
        if not raw:
            # 조용히 "0개" 라고 하지 않는다 — 시딩 안 함과 TTL 만료를 구분 못 한다.
            raise SystemExit(f"🔴 매니페스트 없음: {a.domain} (시딩 안 했거나 TTL 만료)")
        man = json.loads(raw.decode("utf-8"))
        for k, v in sorted(man.items()):
            print(f"  {v:>14,}  {k}")
        print(f"— {len(man)}개 · {_human(sum(man.values()))}")


if __name__ == "__main__":
    main()
