# 데이터팀 DB 구조 · 파일 · 흐름 가이드

> 작성 기준: 2026-08-06 · 데이터팀(김규민/혜성) 소관 파일과 DB 흐름 정리.
> 대상 DB: `omnisite` (docker `omnisite-postgres-db`, PostGIS 3.3.4 + pgvector 0.5.1)

---

## 1. 저장소 구성 (컨테이너 2개, 논리 저장소 3개)

```
omnisite-postgres-db (PostGIS + pgvector)
 ├─ ① PostGIS 테이블   공간·관계 데이터 (geom 4326 + geom_5186)
 └─ ② pgvector        조례 RAG (langchain_pg_embedding, statutes_collection)
omnisite-redis-cache (Redis, AOF 영속화)
 └─ ③ 캐시            지오코딩·지목·LLM 결과 (volatile-lru + AOF)
```

---

## 2. 데이터팀이 쓰는 파일 인벤토리

### 2-1. 스키마 (DDL `.sql`)
| 파일 | 정의 | 소관 | 상태 |
|---|---|---|---|
| `schema_cleaned_data.sql` | STEP2 정제 도메인 16종 | 데이터팀 | 🟢 주력 |
| `schema_cleaned_data_add.sql` | 국유부동산 + **geom_5186 생성컬럼 보완** + 면적/폭 + booth_candidates | 데이터팀 | 🟢 활성 (좌표계 규약 원조) |
| `schema_region_boundaries.sql` | 경계 3종 + 크로스워크 | 본인(comet) | 🟢 신규 |
| `schema_cadastral.sql` | 연속지적도 `cadastral_lands` | 본인(comet) | 🟢 신규 |
| `schema.sql` | 앱 기본(users/districts/dong_boundaries…) + 레거시 공간테이블 | jcm0314 | 🟡 일부만 사용 |

> ⚠️ `schema.sql`의 공간테이블(nosmoking_zones·childcare_centers·cadastral_lands 등)과
> `app/db/models/spatial.py`(ORM, jcm0314 7/9 작성)는 **레거시** — 실 DB(정제본 계열)와 대부분 불일치.
> 데이터팀 소관 아님. 특히 ORM `CadastralLand`는 실제 `cadastral_lands`와 컬럼이 다름.

### 2-2. 적재 로더 (`scripts/`)
| 파일 | 소스 | 대상 테이블 | 짝 스키마 |
|---|---|---|---|
| `scripts/load_region_boundaries.py` | `region_data/BND_*.shp` + 크로스워크 | sido/sigungu/adm_dong_boundaries, admin_crosswalk | `schema_region_boundaries.sql` |
| `scripts/load_cadastral.py` | `region_data/LSMD_*.shp` | cadastral_lands | `schema_cadastral.sql` |
| `scripts/README.md` | — | 사전준비·실행순서·재현 가이드 | — |

> 🔴 `scripts/load_domain_from_gpkg.py`(`step2_output/*.gpkg` → smoking_areas·
> commercial_shops·street_trash_bins)는 **2026-08-11 에 지웠다.** 세 대상 테이블을 포함한
> 흡연 도메인 데이터셋 8개를 DB 에서 제거했기 때문이다 — 프리셋 원본은 이제 DB 가 아니라
> **디스크**에 둔다(이슈 #215 계층 구분). 파이프라인은 그 테이블들을 읽은 적이 없다.

> 모든 로더는 **멱등 가드**(대상 0행/시군구 미적재일 때만) — 재실행해도 중복 안 됨.
> 실행: `python scripts/load_XXX.py`(DRY-RUN) → `--commit`(실제 적재).
> ❌ `dummy/load_cleaned_data.py`는 옛 CSV(`app/data/04.최종_데이터/`) 소실로 **작동 불가(폐기)**.
> 2026-08-10 에 루트에서 `dummy/` 로 옮겼다(폐기물이 루트에 있으면 현역으로 읽힌다).

### 2-3. 팀 공유 (seed)
| 파일 | 역할 |
|---|---|
| `omnisite_seed.sql.gz` (9.3MB) | DB seed — 팀원이 `docker compose up`으로 자동 복원. 경계 3종 데이터 제외(640MB) |
| `docker-compose.yml` | Redis 영속화(AOF+RDB+볼륨+volatile-lru) + DB 자동복원 마운트 |

### 2-4. 원본 데이터 (`.gitignore` — 로컬 전용, 각자 배치)
```
data_임시/region_data/
  BND_{SIDO,SIGUNGU,ADM_DONG}_PG.shp   경계 3종 (EPSG:5186)
  행정동_크로스워크.csv                 코드 매핑 3,555행
  LSMD_CONT_LDREG_11170_202607.shp     연속지적도 44,459필지 (용산)
  국유부동산_위경도_v2.csv              공유지 2,486건 (좌표 포함)
data_임시/step2_output/{흡연_1차,재활용_1차}/*.gpkg   STEP2 정제본 (EPSG:4326)
{도메인}/law/*.pdf,*.txt                조례 원본 (RAG용)
```

### 2-5. 설정
| 파일 | 비고 |
|---|---|
| `.env` | `DATABASE_URL`, `VWORLD_KEY`/`VWORLD_API_KEY`(같은 값 둘 다), `OPENAI_API_KEY`. gitignore |
| `requirements.txt` | **`pyarrow` 필수**(없으면 parquet→csv 폴백, 행정동코드 앞자리 0 유실) |

---

## 3. DB 흐름

### 3-1. 인입 (원본 → DB)
```
[원본]                       [DDL]                        [로더]                     [테이블]
region_data/BND_*.shp(5186) → schema_region_boundaries → load_region_boundaries.py → 경계 3종
행정동_크로스워크.csv        → (동일)                    → (동일)                    → admin_crosswalk
region_data/LSMD_*.shp(5186) → schema_cadastral         → load_cadastral.py         → cadastral_lands
국유부동산.csv               → schema_cleaned_data_add   → (CSV 로더)                → national_properties
{도메인}/law/*.pdf           → statute 파서              → add_statute_chunks(임베딩) → langchain_pg_embedding

  🔴 `step2_output/*.gpkg → 도메인 테이블` 줄은 2026-08-11 에 없앴다. STEP2 정제본은
     DB 로 안 들어간다 — 파이프라인도 화면5 POI 문맥도 **파일에서 직접 읽는다**.

  공통: 5186 원본 → geom(4326) 저장 → geom_5186(GENERATED 자동변환) → GiST 인덱스
```

### 3-2. 소비 (DB → 앱/파이프라인)
```
FastAPI 앱     → PostGIS   : 필지 조회, 자치구 경계 검증(ST_Contains), 뷰포트 지적도
STEP1 감리     → crosswalk : 지역코드 검증 (없으면 검증 꺼짐)
STEP2~4 파이프 → Redis캐시 : 지오코딩·지목 재사용 (AOF로 보존)
                region_data SHP 직접 읽음 (경계는 아직 DB 아닌 파일 사용)
AI 시뮬레이션  → pgvector  : 조례 검색 (facility_type 필터)
                거리연산   : geom_5186 (미터, GiST 인덱스)
```

### 3-3. 팀 공유 (로컬 DB → 팀)
```
내 로컬 DB ──pg_dump(경계 제외)──► omnisite_seed.sql.gz ──git push──►
팀원 git pull ──► docker compose up (빈 볼륨) ──► /docker-entrypoint-initdb.d/ 자동 복원
   (이미 데이터 있는 볼륨엔 실행 안 됨 → 기존 데이터 안전)
```

---

## 4. 좌표계 규약 (전 공간테이블 공통)
| 컬럼 | 좌표계 | 용도 |
|---|---|---|
| `geom` | **EPSG:4326** | 표출·API (GeoJSON CRS84) |
| `geom_5186` | **EPSG:5186** (중부원점, m) | 거리·면적·공간조인 (`GENERATED ALWAYS AS ST_Transform(geom,5186) STORED`) |

- 원본 SHP(5186) → 적재 시 4326 변환, invalid는 `ST_MakeValid`.
- ⚠️ `ADM_CD`/`SIGUNGU_CD`는 **통계청 코드**(행자부와 다름, 11170=행자부 용산/통계청 구로).
  파이프라인 산출물(행자부)과 조인 시 `admin_crosswalk` 경유(직접 조인 금지).

---

## 5. 현재 적재 현황 (2026-08-06)
| 테이블 | 행수 | seed 포함 |
|---|---|---|
| cadastral_lands (연속지적도) | 44,459 | ✅ |
| commercial_shops (상가) | 15,722 | ✅ |
| candidate_lands (후보부지) | 6,524 | ✅ |
| admin_crosswalk (크로스워크) | 3,555 | ✅ |
| national_properties (공유지) | 2,486 | ✅ |
| langchain_pg_embedding (조례 RAG) | 222 | ✅ |
| bus_stop_passenger_stats | 304 | ✅ |
| street_trash_bins / smoking_areas | 280 / 8 | ✅ |
| public_wifi / parks / 인구·지하철 통계 | 699 / 44 / 16·15 | ✅ |
| **adm_dong/sigungu/sido_boundaries (경계 3종)** | 3,559 / 252 / 17 | ❌ (용량, 스크립트로 재적재) |
| dong_boundaries (앱 전용, 용산) | 16 | ✅ |

> 조례 RAG: 흡연부스 178청크(국민건강증진법·용산 금연조례 등) + 전기차충전소 44청크.
> 재활용은 0청크 = 정상(이격거리 규정 없음).

---

## 6. 팀원 온보딩 (새로 받는 사람)
```
1) git clone
2) .env.example → .env 복사 + 키 채우기 (VWORLD_KEY·OPENAI_API_KEY 등)
3) docker compose up      → seed 자동 복원 (경계 제외 전부)
4) (경계 필요 시) region_data 배치 후 python scripts/load_region_boundaries.py --commit
```

---

## 7. 데이터팀 소관 아닌 것 (혼동 방지)
- `app/db/models/spatial.py` — jcm0314(백엔드) ORM, 레거시. 실 DB와 불일치.
- `schema.sql`의 공간테이블 — 위와 동일 레거시.
- STEP4 결과 서빙 API(`results.py`) — 백엔드 미구현.
- Redis 키 무효화 유틸 — 백엔드/파이프라인.
