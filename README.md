# 🛡️ OmniSite 백엔드 개발자 협업 가이드라인 (README)

본 문서는 스마트시티 SDSS 플랫폼 **OmniSite** 백엔드 개발에 참여하는 팀원(장천명, 배종현, 찬진, 동현, 규민, 혜성, 승헌)의 원활한 병렬 개발과 기술 표준 준수를 위한 로컬 개발 가이드라인이자 형상관리 지침서입니다.

---

## 📅 1. 개발 환경 요구사항 및 기동 절차

### ➊ Python 가상환경 셋업
본 프로젝트는 **Python 3.14+** 최신 환경의 휠(Wheel) 호환성을 위해 `psycopg` (psycopg3) 및 `shapely` 완화 설치를 지원합니다.
```bash
# 가상환경 활성화 (Mac OS / Linux)
source ../.venv/bin/activate

# 의존성 설치
pip install --upgrade pip setuptools
pip install -r requirements.txt
```

### ➋ 로컬 PostGIS + pgvector 컨테이너 기동
Docker를 활용해 지리 정보 공간 데이터베이스(PostGIS) 및 RAG 벡터 DB(pgvector)가 통합 장착된 DB 인프라를 가동합니다.
```bash
# Docker Compose 백그라운드 실행
docker compose up -d --build

# 16대 물리 테이블 DDL 주입 상태 확인
docker exec -i omnisite-db psql -U admin -d omnisite < schema.sql
```
*   **로컬 DB 접속 정보**: 포트 `5432` / 사용자 `admin` / 비밀번호 `admin1234` / DB명 `omnisite`
*   *주의*: pgvector 확장 제어 선언은 `CREATE EXTENSION vector;` 문법을 사용해야 합니다.

### ➌ FastAPI 백엔드 개발 서버 실행
핫 리로드(`--reload`) 옵션을 주어 소스코드 변경 사항이 uvicorn 좀비 프로세스 교착 현상 없이 즉각 반영되도록 실행합니다.
```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
*   **Swagger API 문서**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

---

## ⚙️ 2. 핵심 구현 아키텍처 및 공용 모듈 사용법

### ➊ 비동기 SQLAlchemy 2.0 세션 주입 (`get_db`)
모든 API 엔드포인트에서 데이터베이스 커넥션을 맺을 때는 반드시 `app/db/session.py`에 선언된 비동기 제너레이터 `get_db`를 주입받아 사용해야 합니다.
```python
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db

@router.get("/example")
async def read_data(db: AsyncSession = Depends(get_db)):
    # db 객체는 asyncpg 비동기 드라이버 세션입니다.
    # SQL 실행 시 await db.execute(...) 형태로 호출해야 합니다.
```
*   `.env` 내의 DB 주소는 비동기 구동을 위해 `postgresql+asyncpg://` 프로토콜 형식을 유지합니다.

### ➋ 4대 조례 RAG 관리 API 규격
RAG에 필요한 법규 PDF와 파싱 텍스트 캐시의 라이프사이클을 보존하기 위해 구현된 규격입니다.
*   **다중 조례 업로드**: `POST /api/v1/upload/regulation` (PDF 다중 파일 저장 및 캐시 자동 생성)
*   **중복 업로드 방지 가드**: 동일 파일명이 이미 업로드된 경우 `400 Bad Request` 예외 반환.
*   **조례 목록 리스팅**: `GET /api/v1/upload/regulations` (파일명 및 KB 크기 반환)
*   **물리 삭제 Deletion Engine**: `DELETE /api/v1/upload/regulations/{filename}` (PDF 원본 및 `data/regulations/cache/[파일명].txt` 캐시 텍스트 동시 소거)

---

## 🤝 3. 코드 작성 규칙 및 깃(Git) 협업 모델

### 🚨 코드 주석 작성 규칙 (MANDATORY)
*   **원칙**: **모든 작성 코드 라인에 한글 설명 주석을 꼼꼼하게 달아주세요.**
*   **사유**: 주니어 팀원들의 코드 리딩 편의와, 기획자-개발자 간의 지리 공간 연산 알고리즘(AHP 가중치, PostGIS 차집합 등) 이해도를 동기화하기 위한 필수 행동 지침입니다. 주석이 누락된 코드는 코드 리뷰 시 병합 반려(Request Changes) 대상입니다.

### 🌿 브랜치 전략 및 PR 정책
*   `main` 브랜치는 항상 동작 가능한 알파 빌드 상태를 유지하며 보호됩니다.
*   신규 기능 개발 시 반드시 깃허브 이슈(Issues)에 태스크를 매핑하고 아래 브랜치 규칙을 따릅니다:
    ```bash
    # 예시: 2주차 PostGIS ST_Difference 기능 개발 시
    git checkout -b feature/26-postgis-difference
    ```
*   기능 개발 완료 후 main 브랜치로 Pull Request를 날리면, 로컬 import 및 컴파일 검증 통과 여부를 확인한 후 승인 병합합니다.
