#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OmniSite Redis Data Seeding CLI Tool

사용법:
  python app/tools/seed_redis.py [도메인]
  예) python app/tools/seed_redis.py 흡연
"""

import sys
import os

# 저장소 루트를 PYTHONPATH에 추가
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.utils.redis_data_seeder import seed_domain_data_to_redis

def main():
    domain = sys.argv[1] if len(sys.argv) > 1 else "흡연"
    print(f"🚀 [Redis Seeding] 도메인 '{domain}' 데이터 시딩을 시작합니다...")
    res = seed_domain_data_to_redis(domain)
    success_count = sum(1 for v in res.values() if v)
    print(f"✅ [완료] {len(res)}개 항목 중 {success_count}개 성공적으로 Redis 적재됨.")

if __name__ == "__main__":
    main()
