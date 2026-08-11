# -*- coding: utf-8 -*-
"""
OmniSite Redis 캐시 조회 도구
=============================
  • 특정 주소 지오코딩 조회 : python app/tools/get_cache.py geocode "서울특별시 용산구 이촌동 301-10"
  • 특정 지목 판정 결과 조회: python app/tools/get_cache.py jimok 대
  • 전체 캐시 키 목록 조회  : python app/tools/get_cache.py list
"""

import sys
import os
import json
import argparse

# 저장소 루트를 PYTHONPATH 에 넣는다 — cwd 가 루트가 아니면 `app.config` 를 못 찾는다.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import redis  # noqa: E402
from app.config import settings  # noqa: E402

def get_redis_client():
    try:
        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.ping()
        return r
    except Exception as e:
        print(f"🔴 Redis 연결 실패: {e}")
        return None

def get_geocode_cache(address: str):
    r = get_redis_client()
    if not r:
        print("🔴 Redis 연결 실패")
        return

    # 명세서 규격: 1차 Key = geocode (Redis Hash), Field = 정규화 주소
    res_str = r.hget("geocode", address)
    
    print("=" * 70)
    print(f" 📍 [지오코딩 캐시 HGET 조회] 1차 Key: 'geocode' | Field(주소): '{address}'")
    print("=" * 70)
    if res_str:
        res = json.loads(res_str)
        print("  • Match 여부 : ✅ HIT")
        print(f"  • 위도 (lat) : {res.get('lat')}")
        print(f"  • 경도 (lng) : {res.get('lng')}")
        print(f"  • 매칭 유형   : {res.get('type')}")
        print(f"  • 매칭 주소   : {res.get('matched')}")
        print("-" * 70)
        print("  [전체 JSON 상세]")
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        # 폴백: 단일 String geocode_vworld_cache 확인
        alt_str = r.get("geocode_vworld_cache")
        if alt_str and address in json.loads(alt_str):
            res = json.loads(alt_str)[address]
            print("  • Match 여부 : ✅ HIT (구형 String 캐시)")
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print("  • Match 여부 : ❌ MISS (캐시에 없는 주소입니다)")
    print("=" * 70)

def get_jimok_cache(facility: str = "흡연부스"):
    r = get_redis_client()
    if not r:
        print("🔴 Redis 연결 실패")
        return

    # 명세서 규격: 1차 Key = jimok_role_cache (Redis Hash), Field = 시설명
    res_str = r.hget("jimok_role_cache", facility)
    if not res_str:
        res_str = r.get("jimok_role_cache")

    print("=" * 70)
    print(f" 🏞️ [지목 판정 캐시 HGET 조회] 1차 Key: 'jimok_role_cache' | Field(시설명): '{facility}'")
    print("=" * 70)
    
    if res_str:
        try:
            res = json.loads(res_str)
            print(json.dumps(res, indent=2, ensure_ascii=False))
        except Exception:
            print(res_str)
    else:
        print("  • ❌ MISS (캐시에 해당 시설명이 없습니다)")
    print("=" * 70)

def list_keys():
    r = get_redis_client()
    if not r:
        print("🔴 Redis 연결 실패")
        return
    keys = r.keys("*")
    print("=" * 70)
    print(f" 🔑 [Redis 키 목록] 총 {len(keys)}개")
    print("=" * 70)
    for k in sorted(keys):
        k_type = r.type(k)
        print(f"  • [{k}] ({k_type})")
    print("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="OmniSite Redis Cache Viewer")
    parser.add_argument("cmd", choices=["geocode", "jimok", "list"], help="조회할 캐시 종류")
    parser.add_argument("query", nargs="?", default=None, help="검색할 주소 또는 지목코드")
    args = parser.parse_args()

    if args.cmd == "list":
        list_keys()
    elif args.cmd == "geocode":
        if not args.query:
            print("사용법: python app/tools/get_cache.py geocode \"서울특별시 용산구 이촌동 301-10\"")
            return
        get_geocode_cache(args.query)
    elif args.cmd == "jimok":
        get_jimok_cache(args.query)

if __name__ == "__main__":
    main()
