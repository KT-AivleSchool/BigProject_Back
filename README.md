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
---

## 🖨️ 4. WeasyPrint 로컬 환경 설치 안내

PDF 보고서 발급 기능(`GET /api/v1/simulations/results/{parcel_id}/pdf`)은 **WeasyPrint** 라이브러리를 사용합니다.
WeasyPrint는 Python 패키지 외에도 **OS 수준의 C 라이브러리(Pango, Cairo)**가 필요합니다.

### macOS
```bash
brew install pango cairo
```

### Ubuntu / Debian (Docker 포함)
```bash
apt-get install -y \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libffi-dev \
    libjpeg-dev \
    libopenjp2-7
```

> **💡 폰트 번들링 안내**: 한글 깨짐 방지를 위해 NanumGothic TTF 폰트가 `app/static/fonts/NanumGothic-Regular.ttf`에 번들링되어 있습니다.
> OS에 별도 한글 폰트를 설치하지 않아도 PDF가 정상 렌더링됩니다.


## 🗺️ 5. PostGIS / GeoAlchemy2 개발 주의사항

### ⚠️ Alembic 오토마이그레이션 도입 시 필수 설정

`geoalchemy2` 기반의 공간 컬럼(`Geometry`, `MULTIPOLYGON` 등)을 포함한 ORM 모델이 존재합니다.
향후 Alembic 오토마이그레이션(`alembic revision --autogenerate`)을 도입할 경우, `alembic/env.py` 파일 **최상단**에 아래 두 줄을 반드시 추가해야 합니다:

```python
import geoalchemy2          # PostGIS Geometry 컬럼 Alembic 인식을 위한 선행 임포트 (필수)
import geoalchemy2.types    # 커스텀 공간 타입 서브클래스 완전 등록
```

> 이 두 줄이 없으면 `alembic revision --autogenerate` 실행 시 Geometry 컬럼을 인식하지 못하여 마이그레이션 스크립트가 비정상 생성됩니다.

### 📅 통계 테이블 날짜 필드 포맷 컨벤션

`stats.py` 내 통계 테이블의 날짜 필드는 **의도적으로 `String` 타입**으로 관리합니다:

| 필드명 | 타입 | 포맷 예시 | 사유 |
|:---|:---|:---|:---|
| `analysis_ym` | `String(6)` | `"202412"` | 행정안전부·통계청 공공 API 원본 포맷 일치 |
| `analysis_year` | `String(4)` | `"2024"` | 공공 데이터 CSV 원본 포맷 일치 |

범위 조회가 필요할 경우 `BETWEEN '202404' AND '202406'` 형태로 처리합니다.
향후 시계열 분석 기능 추가 시 `Date` 타입으로 마이그레이션을 검토합니다.

