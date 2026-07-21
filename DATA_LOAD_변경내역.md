# 정제데이터 DB 적재 — 변경 내역

> 용산구 흡연부스 입지선정 프로젝트 / 17종 데이터 PostGIS 적재
> 대상 DB: PostgreSQL + PostGIS 3.3 (도커 `omnisite-postgres-db`, dbname=`omnisite`)
> 작업 브랜치: `data-load`

---

## 0. 요약

`schema_cleaned_data_add.sql`과 `load_cleaned_data.py`를 실행해 17종 데이터를 적재했다.
실행 **전 사전 검증**에서 **팀 스키마 ↔ 로더 간 지오메트리 타입 충돌**(그대로 실행 시 candidate_lands
전건 롤백)을 발견해, 승인을 받아 수정한 뒤 적재했다. 최종 결과는 기댓값 17종 전부 일치, invalid 0건.

수정 파일은 총 **2개**(`schema_cleaned_data_add.sql`, `load_cleaned_data.py`).
**팀 base 스키마 파일 `schema_cleaned_data.sql`은 원본 그대로 두었다.**

---

## 1. 🔴 핵심 변경 — candidate_lands 지오메트리 타입 (Polygon → MultiPolygon)

### 문제
- 팀 스키마 `schema_cleaned_data.sql`에서 `candidate_lands.geom`은 **엄격한 `GEOMETRY(Polygon, 4326)`**.
- 로더는 invalid 폴리곤 22건을 `ST_MakeValid()`로 복구하는데, **실측상 그중 10건이 `MULTIPOLYGON`으로 복구**된다
  (자기교차(self-intersection)가 여러 조각으로 분리되기 때문).
- `MULTIPOLYGON`을 `Polygon` 컬럼에 넣으면:
  `ERROR: Geometry type (MultiPolygon) does not match column type (Polygon)`
- `psycopg2.execute_values`는 6,524건을 **단일 INSERT 문**으로 보내므로, 문제 10건 때문에
  **candidate_lands 전체(6,524건)가 롤백**되고 스크립트가 중단된다.

> 검증 근거 (실제 데이터):
> - 원본 invalid 22건 / 전체 6,524건
> - `ST_MakeValid`→`ST_CollectionExtract(...,3)` 변환 후: **POLYGON 6,514 / MULTIPOLYGON 10**

### 결정
**MultiPolygon으로 확장** (사용자 승인). 지오메트리를 손실 없이 전건 보존하고,
`ST_Area`/`ST_OrientedEnvelope` 등 공간연산도 그대로 정확하게 동작한다.
타입 변경은 팀 base 스키마 파일이 아니라 **`schema_cleaned_data_add.sql`의 `ALTER`로 처리**했다.

### 변경 ① `schema_cleaned_data_add.sql` — [보완 1]에 ALTER 추가

```sql
-- [보완 1-b] geom 타입을 MultiPolygon으로 확장  ★필수
-- invalid 복구(ST_MakeValid) 시 self-intersection이 여러 폴리곤으로 분리되어
-- 단일 Polygon 컬럼엔 삽입 실패함(실측 6,524건 중 10건이 MULTIPOLYGON).
ALTER TABLE candidate_lands
  ALTER COLUMN geom TYPE geometry(MultiPolygon, 4326) USING ST_Multi(geom);
```

### 변경 ② `schema_cleaned_data_add.sql` — [보완 2] geom_5186 타입 일치

```diff
- ALTER TABLE candidate_lands  ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(Polygon, 5186)
+ ALTER TABLE candidate_lands  ADD COLUMN IF NOT EXISTS geom_5186 GEOMETRY(MultiPolygon, 5186)
    GENERATED ALWAYS AS (ST_Transform(geom, 5186)) STORED;
```
> `geom`이 MultiPolygon이 되었으므로, `ST_Transform` 결과를 담는 생성컬럼도 MultiPolygon이어야 한다.

### 변경 ③ `load_cleaned_data.py` — candidate_lands INSERT를 ST_Multi로 래핑

```diff
  execute_values(
      cur,
      "INSERT INTO candidate_lands (land_wkt, geom) VALUES %s",
      rows,
-     template="(%s, ST_CollectionExtract("
-              "ST_MakeValid(ST_SetSRID(ST_GeomFromText(%s),4326)),3))",
+     template="(%s, ST_Multi(ST_CollectionExtract("
+              "ST_MakeValid(ST_SetSRID(ST_GeomFromText(%s),4326)),3)))",
  )
```
> 유효했던 단일 폴리곤 6,514건도 `ST_Multi`로 MultiPolygon으로 통일해 컬럼 타입과 일치시킨다.
> **`ST_MakeValid` 복구 로직 자체는 지시대로 그대로 유지**했다.

> `smoking_area_polygons`(08번)는 631건 전부 유효한 단일 Polygon(invalid 0)이라 **변경 없이 Polygon 유지**.

---

## 2. 🟡 실행을 위한 부수 변경

### 변경 ④ `load_cleaned_data.py` — BASE 데이터 경로 수정

```diff
- BASE = "./04.에이블_최종_데이터"
+ BASE = r"c:/Users/User/Projects/BigProject_Back/app/data/04.최종_데이터"
```
> 스크립트 기본값의 폴더명(`04.에이블_최종_데이터`)·위치가 실제 데이터 폴더(`app/data/04.최종_데이터`)와
> 달라 그대로면 `FileNotFoundError`.

### DSN (파일 미변경)
- `.env`가 없어 파일의 placeholder(`password=본인비번`)를 하드코딩하지 않고,
  실행 시 환경변수로 주입:
  ```
  DATABASE_URL="postgresql://postgres:postgres@localhost:5432/omnisite"
  ```
  (자격증명 출처: `docker-compose.yml`)

### 의존성
- `openpyxl` 설치 (공원 데이터 `.xlsx` 읽기용, 미설치 상태였음).
  `psycopg2`, `pandas`는 기설치.

---

## 3. 실행 순서

DB에 팀 16종 스키마가 **아직 적용되어 있지 않았다**(구 `schema_v2` 테이블만 존재).
로더 문서의 1단계가 빠져 있어 아래 순서로 적용했다.

```bash
# 1) 팀 16종 스키마 (신규 16 테이블만 DROP/CREATE — 구 테이블 영향 없음)
psql ... < schema_cleaned_data.sql

# 2) 17번 국유부동산 + 보완 1~5 (+ 위 MultiPolygon ALTER)
psql ... < schema_cleaned_data_add.sql

# 3) 17종 데이터 적재
DATABASE_URL="postgresql://postgres:postgres@localhost:5432/omnisite" python load_cleaned_data.py
```

---

## 4. 적재 결과 (기댓값 17종 전부 일치)

| 테이블 | 건수 | 테이블 | 건수 |
|---|---:|---|---:|
| bus_stop_passenger_stats | 304 | fire_water_facilities | 2,456 |
| street_trash_bins | 277 | cultural_event_locations | 12 |
| parks | 44 | public_parking_lots | 17 |
| cigarette_litter_hotspots | 8 | national_properties | 2,486 |
| smoking_areas | 76 | candidate_lands | 6,524 |
| commercial_shops | 6,509 | smoking_area_polygons | 631 |
| cctv_locations | 189 | subway_station_passenger_stats | 15 |
| public_wifi_locations | 699 | living_population_stats | 16 |
| public_toilets | 94 | | |

- **invalid 폴리곤: 0건**
- **필지 폭 분포 — 3m미만 822 / 3~15m 4,421 / 15m초과(차도추정) 1,281**

### 독립 검증 (DB 직접 조회)
- `candidate_lands.geom`: 6,524건 전부 `MULTIPOLYGON`, SRID **4326**
- `candidate_lands.geom_5186`: 6,524건 전부 채워짐, SRID **5186** → 공간연산은 5186 사용
- `NOT ST_IsValid(geom_5186)` = 0건
- 멀티파트(2조각 이상) = **4건** → 자기교차 복구 시 실제로 분리된 필지가 손실 없이 보존됨
  (Polygon 컬럼이었다면 삽입 실패했을 행들)

---

## 5. 유지된 원칙 / 미변경 사항

- **팀 명명규칙 유지**: 테이블=복수형 snake_case, 컬럼=영문 snake_case, longitude/latitude + geom + created_at.
- **`geom`은 4326 저장, 공간연산은 `geom_5186`(생성컬럼) 사용** 구조 그대로.
- **인코딩 자동감지 로직**(utf-8-sig / cp949 혼재) — 미변경.
- **`ST_MakeValid` 복구 로직** — 미변경(래핑만 추가).
- **팀 base 스키마 파일 `schema_cleaned_data.sql`** — 미변경.
