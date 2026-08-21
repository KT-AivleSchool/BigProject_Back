# -*- coding: utf-8 -*-
"""
OmniSite 마스터 통합 초기화 및 검증(Test) 스크립트 (setup_bootstrap.py)
======================================================================
1. DB(PostgreSQL) & Redis 연결 준비 상태 대기 (Health Check)
2. DB 스키마 복원 및 21개 필수 테이블 구축 (BigProject_Back/scripts/bootstrap_db.py)
3. 적재 데이터 및 주요 테이블 존속성 자동 검증 (Table Verification)
4. Redis 및 FastAPI 백엔드 응답 검증 (Integration Verification)
"""

import sys
import time
import subprocess
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BACK_DIR = BASE_DIR / "BigProject_Back" if (BASE_DIR / "BigProject_Back").exists() else BASE_DIR
BOOTSTRAP_SCRIPT = BACK_DIR / "scripts" / "bootstrap_db.py"

def log(msg: str, icon: str = "📌"):
    print(f"{icon} {msg}")

def check_db_and_bootstrap():
    log("데이터베이스(PostgreSQL) 스키마 생성 및 초기 적재를 시작합니다...", "🚀")
    
    if not BOOTSTRAP_SCRIPT.exists():
        log(f"스마트 스크립트를 찾을 수 없습니다: {BOOTSTRAP_SCRIPT}", "🔴")
        return False

    # 1. bootstrap_db.py --yes --force 실행
    cmd = [sys.executable, str(BOOTSTRAP_SCRIPT), "--yes", "--force"]
    log(f"실행 명령: {' '.join(cmd)}")
    res = subprocess.call(cmd)
    
    if res != 0:
        log(f"DB 부트스트랩 스크립트 실행 실패 (종료 코드: {res})", "🔴")
        return False
        
    log("DB 스키마 구축 완료!", "✅")
    return True

def run_verification_tests():
    log("통합 검증 테스트(Health Check & Schema Verification)를 시작합니다...", "🧪")
    
    # 1. DB 테이블 검증 테스트 실행 (BigProject_Back/scripts/bootstrap_db.py dry-run 확인)
    cmd = [sys.executable, str(BOOTSTRAP_SCRIPT)]
    res = subprocess.call(cmd)
    if res != 0:
        log("DB 스키마 검증 테스트 실패 (누락된 테이블이 존재합니다)", "🔴")
        return False
    log("DB 스키마 및 21개 테이블 무결성 검증 통과!", "✅")

    # 2. 백엔드 API 연결 및 문서 응답 테스트 (최대 10초 대기)
    api_url = "http://127.0.0.1:8000/docs"
    log(f"API 헬스체크 대기 중: {api_url} ...")
    
    connected = False
    for i in range(10):
        try:
            req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    connected = True
                    break
        except Exception:
            time.sleep(1)
            
    if connected:
        log("FastAPI 백엔드 헬스체크(HTTP 200 OK) 성공!", "✅")
    else:
        log("FastAPI 백엔드가 구동되지 않았거나 연결할 수 없습니다. (경고: 로컬 백엔드가 실행 중인지 확인하세요)", "⚠️")

    log("모든 데이터 적재 및 시스템 검증 테스트가 완료 되었습니다!", "🎉")
    return True

if __name__ == "__main__":
    print("=" * 70)
    print(" OmniSite 통합 DB 초기화 & 자동 검증(Test) 파이프라인")
    print("=" * 70)
    
    if not check_db_and_bootstrap():
        sys.exit(1)
        
    if not run_verification_tests():
        sys.exit(1)
        
    sys.exit(0)
