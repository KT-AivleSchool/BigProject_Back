#!/usr/bin/env bash
# ==============================================================================
# OmniSite AWS EC2 인스턴스 원클릭 자동 세팅 & 구동 스크립트 (setup_aws_ec2.sh)
# ==============================================================================
# 지원 OS: Ubuntu 20.04/22.04/24.04 LTS, Debian 11/12, Amazon Linux 2023
#
# 사용법 (EC2 SSH 접속 후):
#   chmod +x setup_aws_ec2.sh
#   ./setup_aws_ec2.sh
# ==============================================================================

set -e

echo "======================================================================"
echo "🚀 OmniSite AWS EC2 인스턴스 초기 환경 자동 세팅을 시작합니다."
echo "======================================================================"

# 1. 루트/일반 사용자 권한 확인 및 OS 패키지 매니저 분기
if [ "$EUID" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

echo "📌 [1/6] 시스템 필수 패키지 및 패키지 매니저 업데이트..."
if command -v apt-get &> /dev/null; then
  $SUDO apt-get update -y
  $SUDO apt-get install -y docker.io docker-compose-v2 python3 python3-pip python3-venv nodejs npm git curl gdal-bin libgdal-dev build-essential
elif command -v dnf &> /dev/null; then
  $SUDO dnf update -y
  $SUDO dnf install -y docker python3 python3-pip nodejs npm git curl gdal gdal-devel gcc gcc-c++
  if ! command -v docker-compose &> /dev/null; then
    $SUDO dnf install -y docker-compose-plugin || true
  fi
else
  echo "⚠️ 알 수 없는 패키지 매니저입니다. 패키지 설치를 건너끕니다."
fi

# 2. Docker 데몬 시작 및 현재 사용자 그룹 등록
echo "📌 [2/6] Docker 데몬 구동 및 사용자 권한 설정..."
$SUDO systemctl enable --now docker
CURRENT_USER=${SUDO_USER:-$USER}
if [ -n "$CURRENT_USER" ] && [ "$CURRENT_USER" != "root" ]; then
  $SUDO usermod -aG docker "$CURRENT_USER" || true
  echo "  ✅ 사용자 '$CURRENT_USER' Docker 그룹 등록 완료."
fi

# 3. Python 빠른 패키지 매니저 (uv) 설치
echo "📌 [3/6] Python uv 패키지 매니저 설치..."
python3 -m pip install --upgrade pip
pip3 install uv || curl -sSf https://astral.sh/uv/install.sh | sh

# 4. 프론트엔드 Node.js 의존성 설치
echo "📌 [4/6] Next.js 프론트엔드 (BigProject_Front) 의존성 설치..."
if [ -d "BigProject_Front" ]; then
  (cd BigProject_Front && npm install)
elif [ -d "../BigProject_Front" ]; then
  (cd ../BigProject_Front && npm install)
fi

# 5. 도커 컴포즈 상시 구동 및 서비스 빌드
echo "📌 [5/6] 전체 서비스 (프론트/백엔드/DB/Redis) 도커 컨테이너 구동..."
if command -v docker-compose &> /dev/null; then
  docker-compose up -d --build
else
  docker compose up -d --build
fi

# 6. 백엔드 DB 부트스트랩 및 자동 검증 테스트 (setup_bootstrap.py)
echo "📌 [6/6] DB 스키마 생성, 시드 적재 및 통합 헬스체크 검증..."
if [ -f "setup_bootstrap.py" ]; then
  python3 setup_bootstrap.py
elif [ -f "BigProject_Back/scripts/bootstrap_db.py" ]; then
  python3 BigProject_Back/scripts/bootstrap_db.py --yes
fi

echo ""
echo "======================================================================"
echo "🎉 AWS EC2/Lightsail 인스턴스 초기 세팅 및 데이터 적재/테스트가 성공적으로 완료 되었습니다!"
echo "======================================================================"
echo "📌 서비스 접속 정보:"
echo "   - 프론트엔드: http://<서버IP>:3000"
echo "   - 백엔드 API: http://<서버IP>:8000/docs"
echo "======================================================================"


