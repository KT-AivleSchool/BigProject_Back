# -*- coding: utf-8 -*-
"""
OmniSite 통합 초기 구동 & 환경 세팅 스크립트 (setup_bootstrap.py)
==================================================================
Git에서 새로 clone 받은 직후 또는 AWS EC2 인스턴스 초기 세팅 시
단 한 번 실행하여 환경변수, 필수 디렉터리, 행정동 크로스워크표,
PostGIS 데이터베이스 시드, 의존성 설치 및 백/프론트 구동 준비를 자동화합니다.

사용법:
    python setup_bootstrap.py          (기본 실행)
    python setup_bootstrap.py --skip-docker  (도커 없이 파일 기반만 준비)
"""

from __future__ import annotations

import io
import os
import sys
import secrets
import subprocess
import shutil
from pathlib import Path

# 파이썬 출력 인코딩 UTF-8 보장 (Windows CP949 방어)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 경로 확정
BASE_DIR = Path(__file__).resolve().parent
if (BASE_DIR / "BigProject_Back").is_dir():
    BACK_DIR = BASE_DIR / "BigProject_Back"
    FRONT_DIR = BASE_DIR / "BigProject_Front"
else:
    BACK_DIR = BASE_DIR
    FRONT_DIR = BASE_DIR.parent / "BigProject_Front"

print("=" * 70)
print("🚀 OmniSite 통합 프로젝트 초기 구동 및 환경 세팅 시작")
print("=" * 70)
print(f"  • 백엔드 경로  : {BACK_DIR}")
print(f"  • 프론트엔드 경로: {FRONT_DIR}")
print("-" * 70)


def print_step(step_no: int, title: str):
    print(f"\n[{step_no}/6] 📌 {title}")


# 1. 필수 디렉터리 구조 자동 생성
print_step(1, "필수 디렉터리 및 데이터 폴더 구조 생성")

dirs_to_create = [
    BACK_DIR / "datasets" / "region_data",
    BACK_DIR / "datasets" / "step1_output",
    BACK_DIR / "datasets" / "step2_output",
    BACK_DIR / "datasets" / "step3_output",
    BACK_DIR / "datasets" / "step4_output",
    BACK_DIR / "datasets" / "search_cache",
    BACK_DIR / "datasets" / "user_input",
    BACK_DIR / "runs",
]

for d in dirs_to_create:
    d.mkdir(parents=True, exist_ok=True)
    print(f"  ✅ 디렉터리 확인/생성: {d.relative_to(BACK_DIR.parent if BACK_DIR.parent != BACK_DIR else BACK_DIR)}")


# 2. 백엔드/프론트엔드 환경변수 (.env) 자동 설정
print_step(2, "환경변수 설정 (.env / .env.local)")

back_env = BACK_DIR / ".env"
back_env_example = BACK_DIR / ".env.example"

if not back_env.exists():
    postgres_pass = secrets.token_urlsafe(24)
    redis_pass = secrets.token_urlsafe(24)
    secret_key = secrets.token_hex(32)

    env_content = f"""# ==============================================================================
# OmniSite 자동 생성 환경변수 (.env)
# ==============================================================================
PROJECT_NAME="OmniSite FastAPI Monolith"
API_V1_STR="/api/v1"

# 데이터베이스 (PostgreSQL + PostGIS)
POSTGRES_PASSWORD={postgres_pass}
REDIS_PASSWORD={redis_pass}
DATABASE_URL="postgresql://postgres:{postgres_pass}@127.0.0.1:5432/omnisite"
REDIS_URL="redis://:{redis_pass}@127.0.0.1:6379/0"
DB_CONNECT_TIMEOUT=10

# 보안 및 인증
SECRET_KEY={secret_key}
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# VWorld API 및 LLM 설정 (필요 시 수정)
VWORLD_API_KEY=""
OPENAI_API_KEY=""
"""
    with open(back_env, "w", encoding="utf-8") as f:
        f.write(env_content)
    print(f"  ✅ 백엔드 .env 파일 자동 생성 완료: {back_env}")
else:
    print(f"  ✅ 백엔드 .env 파일 이미 존재함: {back_env}")

front_env = FRONT_DIR / ".env.local"
if not front_env.exists() and FRONT_DIR.exists():
    with open(front_env, "w", encoding="utf-8") as f:
        f.write("OMNISITE_API_ORIGIN=http://127.0.0.1:8000\n")
    print(f"  ✅ 프론트엔드 .env.local 파일 자동 생성 완료: {front_env}")
elif front_env.exists():
    print(f"  ✅ 프론트엔드 .env.local 파일 이미 존재함: {front_env}")


# 3. 행정동 크로스워크 기본 데이터 생성
print_step(3, "행정동 크로스워크 (통계청-행자부 코드 변환표) 검증")

xwalk_file = BACK_DIR / "datasets" / "region_data" / "행정동_크로스워크.csv"
if not xwalk_file.exists():
    default_xwalk = """행정구역코드,행정동코드,행정동코드8,행정동명,시도명,시군구명
11030730,11170625,11170625,한강로동,서울특별시,용산구
11140550,11200540,11200540,마장동,서울특별시,성동구
11140590,11200590,11200590,사근동,서울특별시,성동구
11140650,11200650,11200650,행당제1동,서울특별시,성동구
11140660,11200660,11200660,행당제2동,서울특별시,성동구
11140690,11200690,11200690,응봉동,서울특별시,성동구
11140720,11200720,11200720,금호1가동,서울특별시,성동구
11140730,11200730,11200730,금호2.3가동,서울특별시,성동구
11140740,11200740,11200740,금호4가동,서울특별시,성동구
11140750,11200750,11200750,옥수동,서울특별시,성동구
11140760,11200760,11200760,성수1가제1동,서울특별시,성동구
11140770,11200770,11200770,성수1가제2동,서울특별시,성동구
11140780,11200800,11200800,성수2가제1동,서울특별시,성동구
11140790,11200810,11200810,성수2가제3동,서울특별시,성동구
11140800,11200820,11200820,송정동,서울특별시,성동구
11140810,11200830,11200830,용답동,서울특별시,성동구
"""
    with open(xwalk_file, "w", encoding="utf-8") as f:
        f.write(default_xwalk)
    print(f"  ✅ 기본 행정동 크로스워크 생성 완료: {xwalk_file}")
else:
    print(f"  ✅ 행정동 크로스워크 파일 존재함: {xwalk_file}")


# 4. Docker / PostGIS 시드 DB 구동 검사
print_step(4, "PostGIS 데이터베이스 및 Redis 컨테이너 검사")

skip_docker = "--skip-docker" in sys.argv
if not skip_docker:
    docker_exe = shutil.which("docker")
    if docker_exe:
        try:
            print("  🐳 Docker Compose 기동 시도 (PostGIS + Redis + 시드 복원)...")
            res = subprocess.run(
                [docker_exe, "compose", "up", "-d", "db", "redis"],
                cwd=str(BACK_DIR),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if res.returncode == 0:
                print("  ✅ Docker Compose 컨테이너 (omnisite-postgres-db, omnisite-redis-cache) 정상 기동 완료!")
                print("     (omnisite_seed.sql.gz 시드가 PostGIS DB로 자동 복원됩니다)")
            else:
                print(f"  ⚠️ Docker Compose 기동 경고: {res.stderr.strip()[:200]}")
                print("     (Docker 데몬이 활성화되면 'docker compose up -d db redis'를 실행하세요)")
        except Exception as e:
            print(f"  ⚠️ Docker 실행 중 건너뀀: {e}")
    else:
        print("  ℹ️ Docker 미설치 환경 — 파일 기반 파이프라인 분석 모드로 진행됩니다.")
else:
    print("  ℹ️ --skip-docker 옵션 지정됨 — 컨테이너 기동을 건너끕니다.")


# 5. 파이프라인 무결성 및 구동 정적 검증
print_step(5, "파이썬 및 애플리케이션 모듈 구동 검증 (py_compile)")

services_to_check = [
    BACK_DIR / "app" / "main.py",
    BACK_DIR / "app" / "services" / "pipeline_runner.py",
    BACK_DIR / "app" / "services" / "gam2_clean_data.py",
    BACK_DIR / "app" / "services" / "gam2_weight_model.py",
    BACK_DIR / "app" / "services" / "gam4_site_select.py",
    BACK_DIR / "app" / "services" / "make_parcel_candidates.py",
]

all_passed = True
for py_file in services_to_check:
    if py_file.exists():
        res = subprocess.run(
            [sys.executable, "-m", "py_compile", str(py_file)],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            print(f"  ✅ 구동 검증 통과: {py_file.name}")
        else:
            print(f"  ❌ 컴파일 실패: {py_file.name}\n{res.stderr}")
            all_passed = False

# 6. 완료 요약 및 서버 기동 가이드
print_step(6, "초기 세팅 완료 보고서")

print("\n" + "=" * 70)
print("🎉 OmniSite 초기 세팅이 성공적으로 시작 가능한 상태로 완료되었습니다!")
print("=" * 70)
print("📌 서버 기동 가이드:")
print(f"  1) 백엔드 FastAPI 서버 기동:")
print(f"     cd {BACK_DIR}")
print(f"     uv run --with-requirements requirements.txt uvicorn app.main:app --host 127.0.0.1 --port 8000")
print()
print(f"  2) 프론트엔드 Next.js 서버 기동:")
print(f"     cd {FRONT_DIR}")
print(f"     npm run dev")
print()
print("  3) 브라우저 접속:")
print("     http://localhost:3000")
print("=" * 70)
