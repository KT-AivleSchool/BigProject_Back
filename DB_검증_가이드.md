# 흡연부스 입지 DB — 설계 검증 가이드 (초보자용)

이 문서는 SQL을 잘 몰라도 그대로 따라 칠 수 있도록 만든 가이드입니다.
목표는 두 가지입니다.

1. **성능 검증** — 우리 스키마가 분석 쿼리를 얼마나 빠르게 처리하는지 숫자로 확인
2. **정확성 검증** — 우리가 만든 "제외구역/수요" 로직이 실제 현실(기존 흡연구역 76곳)과 얼마나 맞아떨어지는지 확인

모든 쿼리는 DBeaver SQL 편집기에 그대로 붙여넣고 `Ctrl+Enter`로 실행하면 됩니다.

---

## 0. 사전 준비 — 결과를 눈으로 볼 수 있게 좌표계 하나 추가하기

지금 우리 `geom` 컬럼은 **위경도(도 단위, EPSG:4326)** 로 저장되어 있습니다.
문제는 "이 지점에서 200m 이내"처럼 **실제 거리(미터)** 로 계산하려면 미터 단위 좌표계가 따로 있어야 정확하다는 점입니다. 도 단위로 그냥 계산하면 오차가 생깁니다.

그래서 검증을 시작하기 전에, 딱 필요한 3개 테이블에만 **미터 단위 좌표계(`geom_5186`, 한국 중부원점)** 를 하나 추가하겠습니다.

```sql
-- candidate_lands, smoking_areas, commercial_shops에 미터 단위 좌표계 추가
ALTER TABLE candidate_lands  ADD COLUMN IF NOT EXISTS geom_5186 geometry(Geometry, 5186);
ALTER TABLE smoking_areas    ADD COLUMN IF NOT EXISTS geom_5186 geometry(Point, 5186);
ALTER TABLE commercial_shops ADD COLUMN IF NOT EXISTS geom_5186 geometry(Point, 5186);
ALTER TABLE smoking_area_polygons ADD COLUMN IF NOT EXISTS geom_5186 geometry(Geometry, 5186);

UPDATE candidate_lands  SET geom_5186 = ST_Transform(geom, 5186) WHERE geom_5186 IS NULL;
UPDATE smoking_areas    SET geom_5186 = ST_Transform(geom, 5186) WHERE geom_5186 IS NULL;
UPDATE commercial_shops SET geom_5186 = ST_Transform(geom, 5186) WHERE geom_5186 IS NULL;
UPDATE smoking_area_polygons SET geom_5186 = ST_Transform(geom, 5186) WHERE geom_5186 IS NULL;

-- 인덱스도 추가 (검색 속도용)
CREATE INDEX IF NOT EXISTS idx_candidate_lands_geom_5186 ON candidate_lands USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_smoking_areas_geom_5186 ON smoking_areas USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_commercial_shops_geom_5186 ON commercial_shops USING GIST (geom_5186);
CREATE INDEX IF NOT EXISTS idx_smoking_area_polygons_geom_5186 ON smoking_area_polygons USING GIST (geom_5186);
```

> 이 작업은 한 번만 하면 됩니다. 실행 후 에러 없이 끝나면 다음 단계로 넘어가세요.

---

## 1. 성능 검증 — `EXPLAIN ANALYZE` 사용법

### 1-1. 기본 개념

`EXPLAIN ANALYZE`는 "이 쿼리를 실제로 실행하면서, 얼마나 걸렸는지 + 어떻게 검색했는지"를 알려주는 명령어입니다.
쿼리 맨 앞에 `EXPLAIN ANALYZE`만 붙이면 됩니다.

```sql
EXPLAIN ANALYZE
SELECT * FROM candidate_lands LIMIT 10;
```

실행하면 결과창에 이런 텍스트가 나옵니다 (숫자는 예시):

```
Limit  (cost=0.00..0.35 rows=10 width=...) (actual time=0.015..0.021 rows=10 loops=1)
Planning Time: 0.123 ms
Execution Time: 0.045 ms
```

**초보자가 봐야 할 건 딱 2줄입니다:**
- `Execution Time` — 실제로 쿼리가 걸린 시간 (ms = 밀리초, 1000ms = 1초). **이 숫자가 작을수록 좋습니다.**
- `Seq Scan` 이라는 단어가 보이면 → 테이블 전체를 처음부터 끝까지 훑었다는 뜻 (느림)
- `Index Scan` 이라는 단어가 보이면 → 인덱스를 이용해서 빠르게 찾았다는 뜻 (빠름)

### 1-2. 비교 실험 — 도 단위(4326) vs 미터 단위(5186)

아래 쿼리는 **"흡연부스 후보지 중, 상가에서 100m 이내에 있는 곳"** 을 찾는 쿼리입니다. 같은 결과를 두 가지 방식으로 구해서 시간을 비교합니다.

**방식 A — 기존 방식 (위경도 도 단위로 근사 계산, 부정확)**

```sql
EXPLAIN ANALYZE
SELECT COUNT(*)
FROM candidate_lands c
JOIN commercial_shops s
  ON ST_DWithin(c.geom, s.geom, 0.001)  -- 0.001도 ≈ 100m (부정확한 근사치)
WHERE c.geom IS NOT NULL;
```

**방식 B — 새로 추가한 미터 단위 좌표계 사용 (정확)**

```sql
EXPLAIN ANALYZE
SELECT COUNT(*)
FROM candidate_lands c
JOIN commercial_shops s
  ON ST_DWithin(c.geom_5186, s.geom_5186, 100)  -- 정확히 100미터
WHERE c.geom_5186 IS NOT NULL;
```

### 1-3. 결과 기록표

두 쿼리를 각각 실행하고, `Execution Time` 값을 아래 표에 적어보세요.

| 방식 | Execution Time | Seq Scan / Index Scan | COUNT 결과값 |
|---|---|---|---|
| A (도 단위, 근사) | ___ ms | ___ | ___ |
| B (미터 단위, 정확) | ___ ms | ___ | ___ |

**해석 방법:**
- B가 A보다 빠르면서 결과값(COUNT)도 다르게 나온다면 → **A는 부정확했고, B가 정확한 답**입니다. 지금까지 A방식만 썼다면 거리 계산이 살짝 틀려있었다는 뜻입니다.
- B가 A보다 확연히 느리면 → 인덱스가 제대로 안 걸린 것일 수 있습니다. 위 0단계의 `CREATE INDEX` 구문을 다시 확인해보세요.

---

## 2. 정확성 검증 — 기존 흡연구역(정답지)과 대조하기

우리에겐 이미 정답에 가까운 데이터가 있습니다: **`smoking_areas`** (용산구가 실제로 설치한 흡연구역 76곳). 이건 이미 여러 규정(학교 이격거리 등)을 통과해서 실제로 설치된 곳이니, "정답"으로 쓸 수 있습니다.

우리가 만든 **제외구역 로직**이 맞는지 확인하는 방법은 간단합니다:
> "기존에 합법적으로 설치된 흡연구역 76곳이, 우리가 만든 제외구역(`smoking_area_polygons`) 안에 들어가 있으면 안 된다."

만약 하나라도 들어가 있다면, 우리 제외구역 로직에 오류가 있다는 뜻입니다.

### 2-1. 검증 쿼리

```sql
SELECT
    sa.id,
    sa.installation_location,
    p.facility_type,
    p.restriction_standard
FROM smoking_areas sa
JOIN smoking_area_polygons p
  ON ST_Intersects(sa.geom, p.geom)   -- 흡연구역이 제외구역 폴리곤과 겹치는지 확인
ORDER BY sa.id;
```

### 2-2. 결과 해석

- **결과가 0행(비어있음)** → 완벽합니다. 기존 76개 흡연구역이 전부 우리 제외구역 로직을 통과합니다. 우리 제외구역 데이터를 새 후보지에도 안심하고 적용할 수 있습니다.
- **결과가 1행 이상 나옴** → 둘 중 하나입니다.
  1. 우리 `smoking_area_polygons` 데이터(이격거리 기준)가 실제 조례보다 더 엄격하게 그려져 있을 가능성
  2. 혹은 그 흡연구역이 나중에 조례가 바뀌기 전에 설치된 "예외 케이스"일 가능성
  
  어느 쪽이든, **몇 개가 나왔는지, 어떤 시설 기준(`facility_type`) 때문인지**를 확인해서 원인을 파악하면 됩니다.

### 2-3. 추가 검증 — 수요 로직이 현실을 잘 반영하는가

이번엔 반대로, "수요가 있는 곳에 실제로 흡연구역이 있는가"를 확인합니다. 기존 흡연구역 76곳 중, 상가(`commercial_shops`)가 근처에 있는 비율을 확인합니다.

```sql
SELECT
    COUNT(*) AS 전체_흡연구역_수,
    COUNT(*) FILTER (
        WHERE EXISTS (
            SELECT 1 FROM commercial_shops s
            WHERE ST_DWithin(sa.geom_5186, s.geom_5186, 100)
        )
    ) AS 상가_100m_이내_있는_흡연구역_수
FROM smoking_areas sa;
```

**해석 방법:**
- `상가_100m_이내_있는_흡연구역_수` ÷ `전체_흡연구역_수` × 100 = **일치율(%)**
- 이 비율이 높을수록(예: 70~80% 이상) → "상가 근처에 흡연구역을 두는 것"이 실제 용산구 배치 패턴과 잘 맞는다는 뜻이고, 우리가 `commercial_shops`를 수요 점수에 반영하려는 계획이 타당하다는 근거가 됩니다.
- 비율이 낮게 나오면(예: 30% 이하) → 상가 밀집도보다 다른 요인(유동인구, 지하철역 등)이 더 중요할 수 있다는 신호입니다. 같은 방식으로 `bus_stop_passenger_stats`, `subway_station_passenger_stats`에 대해서도 똑같이 돌려서 비교해보면, 어떤 요인이 실제로 더 잘 맞는지 순위를 매길 수 있습니다.

---

## 3. 요약 — 오늘 확인한 것

| 검증 항목 | 사용한 쿼리 | 판단 기준 |
|---|---|---|
| 성능 | `EXPLAIN ANALYZE` (4326 vs 5186) | Execution Time이 작고, Index Scan을 쓰는 쪽이 우수 |
| 제외구역 정확성 | 기존 흡연구역이 제외구역과 겹치는지 | 0행이 나와야 정상 |
| 수요 로직 타당성 | 기존 흡연구역 주변 상가 밀집도 | 일치율이 높을수록 그 요인을 점수에 반영할 근거가 강함 |

이 세 가지 결과를 캡처해두시면, "왜 이렇게 설계했는가"를 나중에 설명할 때 근거 자료로도 바로 쓸 수 있습니다.


