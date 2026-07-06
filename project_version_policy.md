# 🛡️ OmniSite 프로젝트 개발 환경 및 버전 통일 규정 (Why This Version)

본 문서는 개발 생산성 저하와 로컬 환경 빌드 크래시를 방지하기 위해, 프론트엔드/백엔드/인프라/AI 파트원 전원이 의무적으로 준수해야 하는 **프로젝트 기술 스택 표준 버전 명세서**입니다.

---

## 📊 기술 스택 표준 버전 요약표

| 영역 | 기술 도구 (Technology) | 표준 권장 버전 (Version) | 도입 의미 및 비고 |
| :--- | :--- | :--- | :--- |
| **백엔드 코어** | **Python** | **`3.12.x`** | 최신 3.13/3.14 빌드 시 GIS 라이브러리 컴파일 에러 방지용 표준 |
| | **FastAPI** | **`0.100.x ~ 0.110.x`** | Pydantic v2 연동 최적화 버전 |
| | **SQLAlchemy** | **`2.0.x`** | 신규 비동기 DB 커넥션 표준 |
| **프론트엔드** | **Node.js** | **`v20.x (LTS)`** | Next.js 14 App Router 최적화 컴파일 버전 |
| | **NPM** | **`10.x`** | 패키지 락 파일(`package-lock.json`) 무결성 보장 |
| | **Next.js** | **`14.2.x`** (App Router) | 안정적인 다크테마 및 글래스모피즘 지원 표준 |
| | **Mapbox GL JS** | **`3.x`** (또는 Leaflet 1.9.x) | 대용량 지적도 폴리곤 고속 렌더링 지원 버전 |
| **데이터베이스** | **PostgreSQL** | **`15.x` 또는 `16.x`** | pgvector 및 PostGIS 확장팩의 메모리 최적화 표준 |
| | **PostGIS** | **`3.x`** | 공간 차집합(`ST_Difference`) 및 GIS 쿼리 연산자 필수 버전 |
| | **pgvector** | **`0.5.x ~ 0.7.x`** | RAG용 벡터 유사도 고속 코사인 연산 지원 |
| **AI 및 RAG** | **LangGraph** | **`0.1.x`** | Multi-Agent 상태 전이 및 루프 제어 프레임워크 |
| | **LangChain OpenAI** | **`0.1.x`** | GPT-4o 플래그십 LLM API 호출 모듈 |
| **인프라/배포** | **Docker Engine** | **`24.x ~ 26.x`** | 개발 서버 및 로컬 컨테이너 패키징 |
| | **Docker Compose** | **`v2.x`** | 로컬 PostgreSQL + pgvector 컴포즈 멀티 컨테이너 |
| | **Kubernetes** | **`1.29` 또는 `1.30`** | AWS EKS 배포 시 노드 격리 지원 버전 |

---

## ⚙️ 영역별 세부 설정 가이드 & 선정 사유 (Why This Version)

### 1. Python 가상환경 구성
*   모든 백엔드 개발자는 로컬 환경에 **`Python 3.12`** 가상환경(`.venv`)을 개설해야 합니다.
*   의존성 설치 시 반드시 루트의 `requirements.txt` 명세를 준수하여 `pip install -r requirements.txt`로 가동합니다.
*   임의의 패키지 설치 시 PM(장천명 님)의 승인을 거쳐 `requirements.txt`에 기입한 뒤 커밋해야 합니다.
*   💡 **선정 사유 (Why Python 3.12?)**:
    1.  `3.13 / 3.14` 극최신 버전: Windows나 일부 M시리즈 Mac OS 환경에서 공간 연산 패키지(`psycopg2` C-Extension 컴파일, `GeoPandas` 의존 휠 파일) 빌드 시 컴파일러 오류가 뿜어져 나와 빌드가 아예 불가능합니다.
    2.  `3.10` 이하 버전: LangGraph 및 최신 LangChain 라이브러리의 비동기 전송 스펙 및 파이썬 타입 힌트 문법이 호환되지 않는 문제를 예방합니다.
    3.  따라서, **안정성과 최신 AI/비동기 라이브러리 지원력을 동시에 충족하는 3.12**를 단일 표준으로 고정합니다.

### 2. Node.js 및 프론트엔드 패키지
*   Node.js 버전 충돌을 방지하기 위해 로컬에 **`nvm`** 설치를 권장합니다. (`nvm use 20`)
*   패키지 추가 시 `yarn` 대신 **`npm install`**을 표준으로 사용하여 `package-lock.json` 형상을 동결합니다.
*   💡 **선정 사유 (Why Node.js v20.x?)**:
    1.  `v18` 이하 구버전: Next.js 14 App Router의 서버 컴포넌트 비동기 스트리밍 연동 시 호환성 이슈 및 런타임 오류 가능성이 존재합니다.
    2.  `v21 / v22` 비-LTS 버전: 공식 안정성 서포트가 만료되거나 제한되어 상용 배포 플랫폼(Vercel, AWS)에 업로드 시 예기치 못한 빌드 크래시를 유발합니다.
    3.  따라서, **장기 지원 표준(LTS) 버전인 v20.x**을 채택하여 E2E 배포 리스크를 원천 차단합니다.

### 3. PostgreSQL 및 PostGIS 설치 (Local)
*   **MacOS**: `brew install postgresql@16` 및 `brew install postgis` 실행 권장.
*   **Windows**: PostgreSQL Installer 실행 시 **Stack Builder**를 통해 `PostGIS 3.x Bundle`을 반드시 체크하여 설치 완료하십시오.
*   **공용 설정**: DB 생성 후 반드시 SQL 콘솔에서 아래 두 명령을 실행하여 확장팩을 설치해야 합니다.
    ```sql
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS pgvector;
    ```
*   💡 **선정 사유 (Why PostGIS 3.x & pgvector?)**:
    1.  **PostGIS 3.x**: 대용량 지적 필지 데이터셋(MultiPolygon)의 `ST_Difference` 차집합 및 공간 인덱싱(`GIST`) 연산 속도가 이전 2.x 대비 40% 이상 빠르며, 메모리 누수가 없습니다.
    2.  **pgvector**: 별도의 비싼 외부 Vector DB(Pinecone 등)를 임차해 쓰지 않고, 3단계 가중치 데이터베이스 내에 조례집 임베딩 벡터를 함께 적재해 트랜잭션 무결성을 지키는 데 최적이기 때문입니다.
