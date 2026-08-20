# OmniSite — 실행 안내

> **B2G 공간의사결정지원 시스템(SDSS).** 갈등시설 입지 선정 —
> GIS 최적화(MCLP) + 다중 에이전트 공청회 시뮬레이션.
> MVP: 서울 용산구 흡연부스 / 2차: 성동구 재활용정거장.
>
> **이 문서 하나만 읽고 끝까지 실행할 수 있게 썼습니다.** 다른 문서를 찾아갈 필요가 없습니다.

| 구성요소 | 저장소 | 브랜치 | 주소 |
|---|---|---|---|
| 백엔드 (FastAPI) | `BigProject_Back` | `back_deploy` | http://127.0.0.1:8000 |
| 프런트엔드 (Next.js) | `BigProject_Front` | `main_deploy` | http://localhost:3000 |

---

## 0. 준비물

| 필요한 것 | 확인 명령 | 비고 |
|---|---|---|
| **Docker Desktop** | `docker --version` | 백엔드는 전부 도커 안에서 돕니다 — **로컬 파이썬 불필요** |
| **Node.js 20 이상** | `node --version` | 프런트엔드용 (Next.js 16) |
| **데이터셋 ZIP** | — | 아래 [1단계](#1-데이터셋-배치) 참조 |

> 🔴 **Docker Desktop 이 실행 중**이어야 합니다. 아이콘이 초록색인지 먼저 보세요.
> 안 켜져 있으면 `docker compose` 가 `cannot connect to the Docker daemon` 으로 죽습니다.

---

## 1. 데이터셋 배치

원본 데이터는 용량이 커서 소스코드와 **따로** 배포합니다.

```
BigProject_Back/
└── datasets/          ← 여기에 ZIP 내용물을 풉니다
    ├── 흡연/           (MVP 도메인 — 원본 데이터 + 조례)
    ├── 재활용/         (2차 도메인)
    ├── 흡연_FIX/       (회귀 기준선 — 5단계 자체점검이 이걸 씁니다)
    ├── 재활용_FIX/
    ├── region_data/    (지적도 · 행정경계 · 크로스워크)
    ├── search_cache/
    └── step1_output/ ~ step4_output/   (정본 산출물)
```

**받는 곳** — 실행 가이드에 공지된 Google Drive:
<https://drive.google.com/file/d/1TAxlK0tCu_a1T4bZffj-yaKXggm0P68A/view?usp=drive_link>

### 🔴 제출본 ZIP 을 쓰는 경우 — 경계 SHP 3개를 따로 넣어야 합니다

제출 규정(100MB 상한)에 맞추려고 제출본에서는 **공개 행정경계 파일 3개를 뺐습니다.**
우리가 만든 데이터가 아니라 통계청이 배포하는 원본이고, 이 셋만 304.7MB 입니다.

| 빼놓은 파일 | 크기 | 필수 |
|---|---|---|
| `datasets/region_data/BND_ADM_DONG_PG.shp` | 129MB | ✅ 필수 |
| `datasets/region_data/BND_SIGUNGU_PG.shp` | 93MB | ✅ 필수 |
| `datasets/region_data/BND_SIDO_PG.shp` | 83MB | 선택 |

같은 폴더의 `.dbf`·`.shx`·`.prj` 는 그대로 들어 있으니 **`.shp` 3개만** 위 Drive 링크의
`datasets.zip` 에서 꺼내 같은 자리에 놓으면 됩니다.

> **없으면 어떻게 되나**: 서버는 정상 기동하고 화면도 뜹니다. 대신 파이프라인 STEP2 의
> 공간조인이 대상 폴리곤을 못 찾아 **지표가 전부 0** 이 됩니다. 안 터지고 값만 틀리므로
> 5단계 자체점검(`check_fixture.py`)을 꼭 돌려 확인하세요.

---

## 2. 환경변수 `.env`

`.env.example` 을 복사한 뒤 값을 채웁니다. **이 파일이 없으면 서버가 기동조차 못 합니다**
(`DATABASE_URL`·`REDIS_URL`·`SECRET_KEY` 는 기본값을 일부러 두지 않았습니다 —
기본값이 있으면 빠뜨렸을 때 조용히 약한 설정으로 뜹니다).

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

### 반드시 채워야 하는 5개

| 키 | 값 | 설명 |
|---|---|---|
| `POSTGRES_PASSWORD` | 임의의 긴 문자열 | 도커 DB 비밀번호. **`postgres` 같은 약한 값 금지** |
| `REDIS_PASSWORD` | 임의의 긴 문자열 | 도커 Redis 비밀번호 |
| `DATABASE_URL` | `postgresql://postgres:<POSTGRES_PASSWORD>@127.0.0.1:5432/omnisite` | 위 비밀번호와 **같은 값**을 넣습니다 |
| `REDIS_URL` | `redis://:<REDIS_PASSWORD>@127.0.0.1:6379/0` | 위와 동일 |
| `SECRET_KEY` | 임의의 64자 hex | JWT 서명 키 |

비밀번호·키 생성 한 줄 (아무 데서나):

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 선택 — 기능별로 필요한 키

| 키 | 없으면 못 하는 것 |
|---|---|
| `OPENAI_API_KEY` | 감리 AI(STEP1)·공청회 토론(화면5). **`fixture` 모드 시연에는 불필요** |
| `VWORLD_KEY` / `VWORLD_API_KEY` | 주소→좌표 지오코딩(새 지역 데이터를 넣을 때만) |
| `KAKAO_REST_API_KEY` | 지도 로드뷰 |
| `LAW_GO_KR_OC` / `DATA_GO_KR_KEY` | 상위법 자동 검색 |

> 🔴 **`localhost` 대신 `127.0.0.1` 을 쓰세요.** `localhost` 는 IPv6(`::1`)로 먼저 풀리는데
> 도커가 IPv4 에만 바인딩해서 **요청 하나가 130초** 걸린 적이 있습니다.
> (코드가 자동 교정하고 경고 로그를 남기지만, 애초에 안 겪는 게 낫습니다.)

---

## 3. 백엔드 실행 — 2줄

```bash
cd BigProject_Back

docker compose up -d --build
docker compose run --rm api python scripts/bootstrap_db.py --yes
```

* 1줄째: DB(PostgreSQL+PostGIS) · Redis · API 컨테이너 3개를 빌드하고 띄웁니다.
  첫 실행은 이미지 빌드 때문에 몇 분 걸립니다.
  **빈 DB 라면 `omnisite_seed.sql.gz` 가 자동으로 복원**되므로 따로 할 일이 없습니다.
* 2줄째: 시드 이후에 추가된 테이블(`run_records` 등)을 만들고 스키마를 맞춥니다.
  **시드를 복원했어도 반드시 한 번 칩니다** — 안 치면 조용히 몇 개가 빠집니다.

> 🔴 **2줄째가 `🔴 건너뛴 단계 ['5']` 를 찍고 0 이 아닌 코드로 끝나는 것은 정상입니다.**
> 시드에 `national_properties`(2,486행)가 이미 들어 있어서, 그 행을 지우는 단계를
> 도구가 **일부러 거부**한 것입니다. 그 단계가 만들 테이블 2개는 시드에 이미 있으므로
> 실제로 빠지는 것은 없습니다. **`--force` 는 치지 마세요** — 2,486행이 삭제됩니다.

열립니다 → **<http://127.0.0.1:8000/docs>**

> `docker compose` 가 없다는 오류가 나면 구버전입니다. `docker-compose`(하이픈)로 바꿔 치세요.
> 운영 배포 스크립트도 하이픈 쪽을 씁니다.

**중지 / 재시작**

```bash
docker compose logs -f api     # 로그 보기
docker compose restart api     # API 만 재시작
docker compose down            # 전부 정지 (데이터는 볼륨에 남습니다)
```

---

## 4. 프런트엔드 실행 — 3줄

```bash
cd BigProject_Front

copy .env.example .env.local     # macOS/Linux: cp .env.example .env.local
npm install
npm run dev
```

열립니다 → **<http://localhost:3000>**

`.env.local` 은 키가 **하나뿐**이고 기본값이 이미 맞습니다:

```
OMNISITE_API_ORIGIN=http://127.0.0.1:8000
```

> 브라우저는 백엔드 주소를 모릅니다 — 항상 같은 출처(`/api/v1/...`)로만 부르고
> Next.js rewrite 가 위 주소로 넘깁니다. 백엔드를 다른 호스트에 두더라도
> **이 값 한 줄만** 바꾸면 됩니다.

---

## 5. 제대로 떴는지 확인 (3분)

```bash
# ① API 문서가 열리는가
curl http://127.0.0.1:8000/docs

# ② 참조 데이터가 다 있는가  → [] 가 나와야 정상
docker compose exec api python -c "from app.config import missing_reference_files; print(missing_reference_files(required_only=True))"

# ③ 회귀 기준선과 값이 같은가 → 57/57 이 나와야 정상
docker compose exec api python app/tools/check_fixture.py 흡연
```

②에서 `행정동 경계 SHP`·`시군구 경계 SHP` 가 나오면 **1단계의 `.shp` 3개를 안 넣은 것**입니다.

③은 LLM 을 한 번도 부르지 않는 순수 대조입니다(비용 0). 후보 필지 수·배제 면적·
가중치까지 기준선과 대조하므로, 여기가 57/57 이면 파이프라인 전체가 정상입니다.

---

## 6. 화면 흐름

| 화면 | 하는 일 | 필요한 것 |
|---|---|---|
| 1 업로드 | 도메인별 원본 데이터·조례 업로드 | — |
| 2~3 감리·정제 | 감리 AI 가 데이터 의도·배제반경 제안 → **사람이 확정(HITL)** | `OPENAI_API_KEY` |
| 4 입지 선정 | MCLP 로 후보지 Top-N 산출, 지도에서 선택 | 경계 SHP |
| 5 공청회 | 선택한 입지로 다중 에이전트 토론 (A 대립형 / B 다인형) | `OPENAI_API_KEY` |
| 6 보고서 | PDF · HWPX 다운로드 | — |

> 🔴 **시드에는 계정도 후보점도 들어 있지 않습니다.** 처음 띄우면 `users` 는 0행이라
> 로그인이 401 이고, 화면 4·5 가 읽는 `booth_candidates` 도 비어 있어 404 입니다.
> 계정은 `POST /api/v1/auth/register`(`/docs` 에서 바로 칠 수 있습니다)로 만들고,
> 후보점은 아래 `fixture` 모드를 한 번 돌리면 채워집니다. **둘 다 정상 동작입니다** —
> 남의 계정과 남의 실행 결과를 배포본에 넣지 않았습니다.

**API 키 없이 시연하려면** 파이프라인 실행 모드를 `fixture` 로 두세요 —
미리 고정해 둔 기준선을 그대로 재생하므로 LLM 을 부르지 않고 화면 5까지 갑니다
(흡연 약 95초). 모드는 실행 요청(`POST /api/v1/pipeline/runs`)의 `mode` 필드입니다.

| 모드 | 뜻 | LLM |
|---|---|---|
| `fixture` | 고정 기준선 재생 (시연용, 대기 없음) | 호출 0회 |
| `hitl` | 프리셋 데이터 + 사람이 게이트에서 값 확정 | 호출함 |
| `full` | 업로드한 데이터로 STEP0부터 전부 | 호출함 |

---

## 7. 자주 나는 오류

| 증상 | 원인 | 처치 |
|---|---|---|
| 기동 시 `RuntimeError: … .env.example 을 복사…` | `.env` 없음/키 누락 | 2단계 |
| `POSTGRES_PASSWORD 를 .env 에 설정할 것` | compose 가 비밀번호를 못 찾음 | 2단계 |
| `cannot connect to the Docker daemon` | Docker Desktop 미실행 | 도커 켜기 |
| `bind: … forbidden by its access permissions` (8000) | Windows 가 8000 을 예약 구간에 넣음(`netsh int ipv4 show excludedportrange protocol=tcp` 로 확인) | 관리자 PowerShell 에서 `net stop winnat && net start winnat`, 또는 Docker Desktop·PC 재시작 |
| 3단계 2줄째가 `건너뛴 단계 ['5']` 로 끝남 | 시드에 이미 데이터가 있어 삭제를 거부한 것 | 정상. 3단계 주의문 참고 (`--force` 금지) |
| 화면은 뜨는데 **지표가 전부 0** | 경계 `.shp` 3개 없음 | 1단계 · 5단계 ② |
| `relation "…" does not exist` | 부트스트랩 안 함 | 3단계 2줄째 |
| 요청 하나가 **130초** 걸림 | `.env` 에 `localhost` 를 씀 | `127.0.0.1` 로 |
| 화면5 가 **HTTP 500** 인데 백엔드는 멀쩡 | 프런트 프록시 타임아웃 | `next.config.ts` 의 `proxyTimeout` 확인 |
| 프런트 `npm install` 실패 | Node 버전 | Node 20 이상 |
| 콘솔에 `UnicodeEncodeError` | Windows cp949 콘솔 | `set PYTHONIOENCODING=utf-8` |

---

## 8. 더 읽을 것 (실행에는 필요 없음)

| 문서 | 내용 |
|---|---|
| `CLAUDE.md` | 설계 원칙 · **실제로 겪은 함정 목록** |
| `docs/개발자_협업_가이드.md` | 팀 내부 협업 규칙 (옛 README) |
| `docs/api_schema.md` | API 스키마 |
| `pipeline_run_contract.md` | 파이프라인 실행 API 계약 (프런트↔백엔드) |

---

## 9. 기술 스택

**백엔드** FastAPI · SQLAlchemy 2.0 (asyncpg) · PostgreSQL + PostGIS · Redis ·
GeoPandas / Shapely · LangChain + LangGraph · pgvector · playwright(PDF)

**프런트엔드** Next.js 16 · React 19 · TypeScript · Tailwind CSS 4 · Leaflet

**인프라** Docker Compose · GitHub Actions → AWS EC2 · AWS Amplify
