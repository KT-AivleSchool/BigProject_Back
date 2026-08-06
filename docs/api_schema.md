# OmniSite API — JSON 스키마 명세서

> 대상: 프론트엔드-백엔드 통신 규격
> 기준: `app/schemas/*.py`(pydantic), `app/api/v1/*`, **STEP4 실측 산출물**(`step4_output.zip`, `흡연_weight_set.json`, 2026-07-29 · 최승헌님 제공), 이슈 **#179 / #189** 반영
> 버전: **v1.0 (최종)** — STEP4 실측 반영 · 좌표계 5186 확정 · #189 저장 요건 반영

---

## 좌표계 정책 (확정)

| 용도 | 좌표계 |
|---|---|
| API 응답 / 표출 | **EPSG:4326** (GeoJSON은 `CRS84`, lng/lat 순) |
| 거리·면적·공간 연산 | **EPSG:5186** (중부원점, meter) |
| geometry **저장**(감사·재연산) | **EPSG:5186** 보관 (표출본은 4326) — #189 |

- **STEP4 파이프라인·데이터팀 DB 모두 5186** — 통일 작업 불필요. (srid 5179 컬럼은 **존재하지 않음**, live DB 확인. 과거 문서의 "DB=5179"는 오기)
- **#189 요건**: STEP4 geometry 산출물(topN/exclusion 등)은 표출용 4326과 별개로 **공간 재연산·감사를 위해 5186 geometry도 보관**. 표출본 `topN.geojson`은 4326.
- **우리 DB가 이미 충족**: 공간 테이블에 `geom`(4326) + `geom_5186`(`GENERATED ALWAYS AS ST_Transform(geom,5186) STORED`) → 4326 저장 시 5186 자동 유지.

---

## 목차
1. [인증 (auth)](#1-인증-auth)
2. [토지/지적도 (lands)](#2-토지지적도-lands)
3. [AHP 가중치 (ahp)](#3-ahp-가중치-ahp)
4. [AI 모의 심의 시뮬레이션 (simulations)](#4-ai-모의-심의-시뮬레이션-simulations)
5. [STEP4 위치선정 파이프라인 산출물 (실측)](#5-step4-위치선정-파이프라인-산출물-실측)
6. [감리 (audit)](#6-감리-audit)
7. [공통 규칙](#7-공통-규칙)

---

## 1. 인증 (auth)

### `POST /auth/register` — 요청
```json
{ "email": "user@example.com", "password": "Passw0rd!", "username": "김규민" }
```
| 필드 | 타입 | 필수 | 비고 |
|---|---|---|---|
| `email` | string(email) | ✅ | ID 역할 |
| `password` | string | ✅ | 영문/숫자/특수문자 중 2종류 이상. 2종류=10자↑, 3종류=8자↑. 공백 불가 |
| `username` | string | ✅ | 실무자 이름 |

### 로그인/회원가입 — 응답 (`TokenResponse`)
```json
{ "access_token": "eyJhbGciOiJIUzI1NiIs...", "token_type": "bearer", "expires_in_minutes": 10080 }
```

### 유저 정보 — 응답 (`UserResponse`)
```json
{ "id": 1, "email": "user@example.com", "username": "김규민", "is_active": true }
```

---

## 2. 토지/지적도 (lands)

### 파일 업로드 — 응답 (`UploadResponse`)
```json
{
  "status": "success",
  "summary": { "filename": "용산구_지적도.shp", "file_type": "SHP",
    "total_records": 6524, "imported_records": 6500, "failed_records": 24 }
}
```

### 좌표 보정 (HITL) — 요청 (`HitlCoordinateCorrection`)
```json
{ "parcel_id": 123, "corrected_address": "서울특별시 용산구 이태원동 123-45",
  "corrected_lat": 37.5344, "corrected_lng": 126.9941 }
```
> `corrected_lat`: -90~90 / `corrected_lng`: -180~180 검증됨.

### 필지 상세 — 응답 (`LandDetailResponse`)
```json
{
  "parcel_id": 123, "address": "서울특별시 용산구 이태원동 123-45",
  "geometry_geojson": { "type": "Polygon", "coordinates": [[[126.9941, 37.5344], "..."]] },
  "is_excluded": false, "exclusion_reason": null, "lat": 37.5344, "lng": 126.9941
}
```
> `geometry_geojson`은 dict 그대로 패스스루(재모델링 금지 — STEP4와 동일 관례). `exclusion_reason` 예: `"어린이보호구역 200m 이내"`.

### CSV 감리(AI 분석) — 응답 (`CsvAuditResponse`)
```json
{
  "status": "success",
  "audit_reason": "지번 컬럼 결측 12건, 학교보건법 규제구역 3건 발견",
  "user_intent": "유동인구 밀집 지역 우선 탐색",
  "extracted_weights": { "소방시설 거리": 5, "배후 주거인구": 5, "이용 편의성": 5 }
}
```
> `extracted_weights`는 dict. 실제 엔드포인트는 요인별 기본값 `5` 반환(LLM 도출 요인명 + 고정 가중치).

### 자치구 경계 검증 — 요청/응답
```json
// 요청
{ "district_id": 1, "lat": 37.5344, "lng": 126.9941 }
// 응답
{ "district_id": 1, "is_contained": true }
```

### 뷰포트 기반 지적도 조회 — 요청 (`SimplifiedLandsRequest`)
```json
{ "min_lat": 37.520, "max_lat": 37.545, "min_lng": 126.960, "max_lng": 126.995, "tolerance": 0.0001 }
```

---

## 3. AHP 가중치 (ahp)

### 쌍대비교 요청 (`AhpWeightsRequest`)
```json
{ "matrix_size": 3, "pairwise_matrix": [[1.0, 3.0, 5.0], [0.333, 1.0, 2.0], [0.2, 0.5, 1.0]] }
```

### 가중치 계산 — 응답 (`AhpCalculateResponse`)
```json
{ "status": "success", "consistency_ratio": 0.08, "is_locked_allowed": true, "weights": [0.633, 0.260, 0.106] }
```

### 가중치 저장 — 요청/응답
```json
// 요청
{ "pairwise_matrix": [[1.0, 3.0], [0.333, 1.0]], "weights": [0.75, 0.25], "consistency_ratio": 0.0 }
// 응답
{ "ahp_model_id": 5, "is_locked": true, "saved_at": "2026-07-28T10:00:00" }
```

---

## 4. AI 모의 심의 시뮬레이션 (simulations)

### `POST /simulations/stream` — 요청 (`StreamRequest`)
```json
{
  "parcel_id": 123, "facility_type": "흡연부스",
  "audit_data": {
    "facility_inference": { "facility": "흡연부스", "region": "용산구" },
    "results": [
      { "summary": "보행혼잡도로 인한 갈등 요인",
        "roles": [
          { "role": "negative_factor", "weight": -0.4, "rationale": "..." },
          { "role": "hard_exclusion", "rationale": "학교 30m 이내", "source": "국민건강증진법 제9조제6항제3호" }
        ] }
    ]
  }
}
```

### 응답 (SSE, `SseMessagePacket`)
```json
{ "sender": "주민대표", "message": "이 지역은 유동인구가 많아...", "is_finished": false }
{ "sender": "시스템", "message": "🎉 모의 심의 토론이 최종 종료되었습니다...", "is_finished": true }
```
| 필드 | 타입 | 비고 |
|---|---|---|
| `sender` | string | `"주민대표"`, `"상인대표"`, `"조정공무원"`, `"시스템"` |
| `message` | string | 토큰 단위 실시간 텍스트 |
| `is_finished` | boolean | 종료 신호 |

에러 시: `{ "error_code": "OPENAI_QUOTA_EXCEEDED", "message": "...", "is_finished": true }`

### `GET /simulations/results/{parcel_id}` — 응답 (`SimulationResultResponse`)
```json
{
  "parcel_id": 123, "conflict_sensitivity_score": 6.8,
  "conflict_factors": { "보행혼잡도": 0.4, "소음민감도": 0.3, "상권활성화": 0.3 },
  "scenario": {
    "scenario": "B", "scenario_description": "조건부 수용", "final_acceptance_score": 0.62,
    "reason": "...", "summary": "...", "conflict_risk_index": 45.0, "risk_reason": "..."
  },
  "debate_logs": [{ "sender": "시스템", "text": "..." }]
}
```
**에러**: `404`(이력 없음), `503 OPENAI_QUOTA_EXCEEDED`

### `GET /simulations/results/{parcel_id}/pdf`
`application/pdf` 바이너리. **에러**: `404`, `422 GEOCODING_FAILED`, `503 AI_SCORE_UNAVAILABLE`, `500`

### `POST /simulations/run`
```json
{ "parcel_id": 123, "ahp_model_id": 5 }
```

---

## 5. STEP4 위치선정 파이프라인 산출물 (실측)

> **최승헌님 제공 실제 샘플(`step4_output.zip`, `흡연_weight_set.json`) 기준.**
> 좌표계: 응답 지오메트리 **EPSG:4326**(GeoJSON `CRS84`), 계산·저장 **EPSG:5186**(위 "좌표계 정책" 참조).
> **산출물은 실행 단위(execution-level)** — 파이프라인 실행마다 생성. 매니페스트에 실행 식별자·입력 참조 포함(#189).

### 5-1. 산출물 목록
| 파일 | 형식 | 크기 | 표출/용도 |
|---|---|---|---|
| `<도메인>_score_grid.json` | JSON | ~215KB | 점수 히트맵(격자) |
| `<도메인>_exclusion.geojson` | GeoJSON | ~330KB | 배제구역 |
| `<도메인>_topN.geojson` | GeoJSON(Point) | ~10KB | Top-N 후보 핀 |
| `<도메인>_report.json` | JSON | ~15KB | 커버 곡선·갭·요약 |
| `<도메인>_preview.html` | HTML | ~515KB | 자기완결 미리보기(점검용) |
| `<도메인>_topN_min.csv` | CSV | ~4KB | Top-N 표(비지도용) |
| `<도메인>_topN_min_pool0.csv` | CSV | ~4KB | Top-N(원풀) 표 |
| `<도메인>_weight_set.json` | JSON | ~5KB | 가중치 세트(근거 포함) |

`{domain}` ∈ `흡연 | 재활용 | EV`

### 5-2. 엔드포인트
| 메서드 | 경로 | 반환 |
|---|---|---|
| GET | `/api/v1/results/{domain}` | 매니페스트(최신 실행) |
| GET | `/api/v1/results/{domain}/{execution_id}` | 특정 실행 매니페스트 |
| GET | `/api/v1/results/{domain}/score_grid` | `score_grid.json` |
| GET | `/api/v1/results/{domain}/exclusion` | `exclusion.geojson` |
| GET | `/api/v1/results/{domain}/topN` | `topN.geojson` |
| GET | `/api/v1/results/{domain}/topN.csv` | `topN_min.csv` |
| GET | `/api/v1/results/{domain}/report` | `report.json` |
| GET | `/api/v1/results/{domain}/weight_set` | `weight_set.json` |
| GET | `/api/v1/results/{domain}/preview` | `preview.html` |

**매니페스트 예시** (실행 단위 식별 — #189)
```json
{
  "domain": "흡연", "facility": "흡연부스", "region": "서울특별시 용산구",
  "execution_id": "wm-1.0-20260729-1757",
  "generated_at": "2026-07-29T17:57:00Z", "engine_version": "wm-1.0",
  "inputs": {
    "audit_result_reviewed": "...", "clean_report": "...",
    "candidate_gpkg": "...", "weight_set": "..."
  },
  "artifacts": {
    "score_grid": "/api/v1/results/흡연/score_grid",
    "exclusion": "/api/v1/results/흡연/exclusion",
    "topN": "/api/v1/results/흡연/topN",
    "topN_csv": "/api/v1/results/흡연/topN.csv",
    "report": "/api/v1/results/흡연/report",
    "weight_set": "/api/v1/results/흡연/weight_set",
    "preview": "/api/v1/results/흡연/preview"
  }
}
```
**서빙 원칙**: 사전 계산 파일을 얇은 엔드포인트로 패스스루. GeoJSON은 dict 그대로 반환(재모델링 금지 — `LandDetailResponse.geometry_geojson: dict` 관례와 동일).

### 5-3. `score_grid.json` — 격자 점수
폴리곤이 아니라 **셀 중심점 배열**. 각 셀은 `spacing_m` 정사각형으로 프론트가 복원.
```json
{
  "spacing_m": 50.0, "crs": "EPSG:4326", "count": 6797,
  "score_min": 0.0, "score_max": 0.8043,
  "cells": [ [126.977354, 37.516165, 0.2024, 0] ]
}
```
`cells[]` 원소는 **배열**(객체 아님): `[lng, lat, score, flag]`
| 인덱스 | 필드 | 타입 | 설명 |
|---|---|---|---|
| 0 | lng | float | 셀 중심 경도(4326) |
| 1 | lat | float | 셀 중심 위도(4326) |
| 2 | score | float | 셀 점수(`score_min`~`score_max`) |
| 3 | flag | int(0/1) | 셀 분류 플래그. 의미 미확인 — **확인 요청(§5-8)** (샘플 0:6493 / 1:304) |

**복원 규칙**: 중심(lng,lat)에서 미터 기준 ±`spacing_m`/2. 도(degree) 환산 시:
```
d_lat = (spacing_m/2) / 111320
d_lng = (spacing_m/2) / (111320 * cos(lat))
```
격자가 5186에서 생성되어 중심만 4326으로 변환된 형태 → 미터 기준 렌더가 정확.

### 5-4. `exclusion.geojson` — 배제구역
표준 GeoJSON `FeatureCollection`, dict 패스스루.
```json
{
  "type": "FeatureCollection", "name": "흡연_exclusion",
  "crs": { "type": "name", "properties": { "name": "urn:ogc:def:crs:OGC:1.3:CRS84" } },
  "features": [
    { "type": "Feature",
      "properties": { "dataset_id": "01", "type": "polygon", "radius_m": 30, "label": "01 polygon 30m" },
      "geometry": { "type": "MultiPolygon", "coordinates": [] } }
  ]
}
```
| properties | 타입 | 값 예시 | 설명 |
|---|---|---|---|
| `dataset_id` | string | "01","05","06","07","11" | 배제 근거 데이터셋 ID |
| `type` | string | `polygon` \| `radius` | 배제 형태(면 버퍼 / 반경) |
| `radius_m` | int | 30, 10 | 버퍼/반경(m) |
| `label` | string | "01 polygon 30m" | 표시용 라벨 |
> 표출본은 **simplify(1.0) 적용됨**(감리팀 확인, #179). 판정은 원본 union 사용이라 표출 단순화가 정확도에 영향 없음. simplify 메타는 properties에 없음. 감사용 5186 union 별도 보관(#189) — §5-8.

### 5-5. `topN.geojson` — Top-N 후보
`FeatureCollection`, geometry = Point, feature 20개. **properties 필드명이 한글**.
```json
{
  "type": "FeatureCollection", "name": "흡연_topN",
  "crs": { "type": "name", "properties": { "name": "urn:ogc:def:crs:OGC:1.3:CRS84" } },
  "features": [
    { "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [126.97, 37.53] },
      "properties": {
        "순위": 1, "점수": 0.746, "커버기여": 68.118, "누적커버율": 0.0304,
        "parcel_idx": 23236, "PNU": "1117012500104220000", "JIBUN": "422 대",
        "지목": "대", "면적": 11934.4497, "내접폭": 73.2987, "법정동코드": "1117012500",
        "국유_건수": 0, "국유_지분면적": 0.0, "국유_지분율": 0.0, "국유_지번일치": 0, "from_rep": 0
      } }
  ]
}
```
| 필드 | 타입 | 범위/단위 | 설명 |
|---|---|---|---|
| `순위` | int | 1..N | 순위(1=최상위) |
| `점수` | float | 0.0~1.0 | 종합 점수 |
| `커버기여` | float | ≥0 | 한계 커버 기여값(절대치) |
| `누적커버율` | float | 0.0~1.0, 단조증가 | 순위순 누적 커버 |
| `parcel_idx` | int | — | 내부 후보 인덱스 |
| `PNU` | string(19) | — | 필지고유번호 |
| `JIBUN` | string | — | 지번 |
| `지목` | string | — | 지목(대/전/답 등) |
| `면적` | float | m² | 필지 면적 |
| `내접폭` | float | m | 최대 내접원 지름(부스 적치 폭 근거) |
| `법정동코드` | string(10) | — | **법정동 코드**(행정동 아님 — 크로스워크 조인 주의, §5-8) |
| `국유_건수` | int | — | 국유 지분 필지 수 |
| `국유_지분면적` | float | m² | 국유 지분 면적 |
| `국유_지분율` | float | 0.0~1.0 | 국유 지분율(프론트 %) |
| `국유_지번일치` | int/bool | 0/1 | 국유 지번 일치 |
| `from_rep` | int/bool | 0/1 | 대표점 유래 여부 |

> ⚠️ `법정동코드`는 **법정동(10자리)** 기준. `행정동_크로스워크.csv`는 **행정동** 기준이라 직접 조인 불가(법정동↔행정동은 다대다). 지역 매칭 시 별도 매핑 필요 — §5-8.
> 표출본 geometry는 4326. **감사·재연산용 5186 geometry는 별도 보관(#189).**
> `topN_min.csv`는 위 필드를 표 형식으로 담은 것.

### 5-6. `report.json` — 커버 리포트
```json
{
  "domain": "흡연", "facility": "흡연부스",
  "weight_set": { "alpha": 0.3, "decay": { "func": "gaussian", "sigma_ratio": 0.333 },
    "scale": "log", "n_candidates": 42216,
    "indicators": [ { "id": "07+02", "w_final": 0.1802, "radius_m": 150 } ] },
  "facility_params": { "설치_소요_폭_m": 2.0, "서비스_반경_m": 300.0, "최소_이격_m": 200.0 },
  "counts": { "parcels": 42216, "points": 66915, "survive": 57023 },
  "coverage": {
    "n_max": 100, "cumulative": [0.0304, 0.0592], "marginal": [68.118, 64.552],
    "reach": { "0.5": 24, "0.7": 38, "0.8": 47, "0.9": 60 },
    "knee": 45, "ceiling": 0.9997, "unreached_n": 4, "unreached_val": 0.00034
  },
  "topn": [],
  "data_gap": [
    { "kind": "주변이격_미적용", "target": "철, 천, 구, 학, 유, 주, 사, 묘, 수",
      "detail": "해당 지목 필지 자체만 후보에서 제외했다",
      "impact": "이 시설들의 '주변 이격거리' 규제가 있다면 미반영이다. 적용하려면 해당 시설 데이터셋을 감리에 태워야 한다" }
  ]
}
```
**`coverage` 필드**
| 필드 | 타입 | 설명 |
|---|---|---|
| `n_max` | int | 커버 곡선 산출 최대 후보 수 |
| `cumulative[]` | float[] 0~1 | k개 선택 시 누적 커버율(단조증가) |
| `marginal[]` | float[] | k번째 후보 한계 기여(절대치) |
| `reach{}` | map | 목표 커버 도달 필요 후보 수 |
| `knee` | int | 커버 곡선 무릎점(효율 변곡) |
| `ceiling` | float 0~1 | 도달 가능 커버 상한 |
| `unreached_n` | int | 미도달 대상 수 |
| `unreached_val` | float | 미도달분 값 |
> **`ceiling`이 100% 아닌 사유**: `unreached_n`/`unreached_val`이 근거. 별도 `ceiling_reason` 텍스트 필드 없음 — 안내 문구를 이 수치로 구성할지, 텍스트 필드 추가 요청할지 §5-8.

**`data_gap[]`** — 지리적 미커버가 아니라 **파이프라인이 반영 못 한 규제/데이터 항목** 목록
| 필드 | 설명 |
|---|---|
| `kind` | 갭 종류 |
| `target` | 영향 대상 |
| `detail` | 처리 내용 |
| `impact` | 영향·보완 방법 |

### 5-7. `weight_set.json` — 가중치 세트
지표별 최종 가중치·산출 근거(HITL·크리틱). 표출보다 설명/감사(audit)용.
```json
{
  "domain": "흡연", "facility": "흡연부스", "region": "서울특별시 용산구",
  "engine_version": "wm-1.0", "alpha": 0.3,
  "n_candidates": 42216, "candidate_unit": "국유부동산 필지",
  "indicators": [
    { "id": "07+02", "kind": "point_sum", "direction": "benefit",
      "components": { "geo": "07", "val": "02" }, "radius_m": 150,
      "radius_rationale": "버스정류장은 유동인구가 많아 ... 넓은 반경이 필요하다.",
      "seed_rationale": "...", "sparse_excluded": false,
      "w_human": 0.1807, "w_critic": 0.2094,
      "w_critic_ci": { "mean": 0.2094, "std": 0.00054, "ci_low": 0.2084, "ci_high": 0.2105 },
      "w_final": 0.1802 }
  ]
}
```
| 필드 | 설명 |
|---|---|
| `id` | 지표 ID(예: `07+02`, `04`) |
| `kind` | `point_sum` \| `admin` 등 |
| `direction` | `benefit`(가점) \| `cost`(감점) |
| `components` | `{geo, val}` 구성 데이터셋 ID |
| `radius_m` | 영향 반경(m). `admin`은 null |
| `radius_rationale`/`seed_rationale` | 반경·시드 근거(HITL) |
| `sparse_excluded` | 희소 데이터 제외 여부 |
| `w_human`/`w_critic` | 인간/크리틱 가중치 |
| `w_critic_ci` | 크리틱 신뢰구간 `{mean,std,ci_low,ci_high}` |
| `w_final` | 최종 적용 가중치 |

### 5-8. STEP4팀 확인 요청 (미해결)
1. **score_grid `flag`(4번째 값)** — 0/1 의미? (샘플 0:6493 / 1:304)
2. **exclusion `type`/`radius_m` 정의** — `type`(polygon/radius)·`radius_m` 정확한 정의. (simplify 적용 여부는 **해결**: simplify(1.0) 적용·판정은 원본 union — #179)
3. **`법정동코드` ↔ 행정동 매핑** — topN은 법정동(10자리) 기준, 크로스워크는 행정동 기준. 지역 매칭용 매핑 제공 가능 여부
4. **`ceiling_reason` 텍스트 필드** 추가 가능 여부
5. **CSV 두 종 차이** — `topN_min.csv` vs `topN_min_pool0.csv`(원풀?)
6. **생성 메타·실행 식별자** — 각 산출물에 `execution_id`·`generated_at`·`engine_version`·`region` 표준 포함 여부(#189)
7. **감사용 5186 geometry** — topN/exclusion의 5186 geometry 보관 형식(별도 파일 vs PostGIS 테이블)

### 5-9. 실측·이슈로 확정된 항목
- 점수·국유_지분율 정규화 → **0~1**
- 누적커버율 단조증가 → **확정**
- score_grid 원점/배열순서 개념 → **불필요**(cells 명시 배열)
- 격자 간격 → **미터(`spacing_m`)**
- 좌표계 연산 → **5186**(STEP4·데이터팀 DB 공통), 표출 **4326** (5179 아님, #189 재확인)

### 5-10. 저장 요건 (#189)
STEP4/파이프라인 산출물의 저장 위치·제외 규칙.
| 저장소 | 대상 |
|---|---|
| **PostGIS** | geometry 있는 STEP2 정제 산출물 8종 (5186 등록) |
| **Redis** | STEP 간 중간 산출물, HITL 결과 |
| **영속 저장** | 감사 자료: `audit_result_reviewed.json`, `weight_set.json`, `report.json`, `clean_report.json` |
| **감사 대상(실행단위)** | `topN`·`score_grid`·`exclusion`(union) — 약 0.56MB, 5186 geometry 보관 |

**적재 제외(대용량)**: `_preview.csv`(15.1MB), `candidates` 레이어(5.04MB), R-tree 인덱스(4.77MB) — DB 미적재.
**실행 추적**: 실행 단위 식별자 + 입력 4종 참조(`audit_result_reviewed`, `clean_report`, `candidate gpkg`, `weight_set`).
**데이터 조직 계층**: 지적/경계/크로스워크 = 지역 단위 · STEP2 정제본 = 도메인×지역 · topN/격자/배제 = **실행 단위**.

---

## 6. 감리 (audit)

### 공문서 검증 — 응답 (`AuditVerifyResponse`)
```json
{
  "ocr_success": true,
  "extracted_text_snippet": "본 공사는 2026년 3월 준공 검사를 완료하였으며...",
  "matched_scenario": "B", "similarity_score": 0.87, "classification_status": "COMPLIANT",
  "parsed_metadata": {
    "parsed_jibun": "서울특별시 용산구 이태원동 123-45", "parsed_date": "2026-03-15",
    "facility_type": "흡연부스", "document_no": "용산구-2026-0123"
  }
}
```
| 필드 | 값 종류 |
|---|---|
| `matched_scenario` | `"A"` \| `"B"` \| `"C"` \| `null` |
| `classification_status` | `"COMPLIANT"` \| `"DEVIATED"` \| `"WARNING"` \| `"UNCLASSIFIED"` |
| `similarity_score` | 0.0 ~ 1.0 |

### 감리 결과 저장 — 응답 (`AuditSaveResponse`)
```json
{ "audit_id": 42, "is_feedback_loop_isolated": true, "saved_at": "2026-07-28T10:00:00" }
```

---

## 7. 공통 규칙

### 좌표계
| 용도 | 좌표계 | 비고 |
|---|---|---|
| API 응답(전체) | **EPSG:4326** (GeoJSON은 `CRS84` lng/lat) | 지도 라이브러리 호환 |
| 내부 계산 (STEP4 + 데이터팀 DB 공통) | **EPSG:5186** (`geom_5186`) | 미터 단위, 자동생성 컬럼 |
| geometry 저장(감사·재연산) | **EPSG:5186** | 표출본은 4326 (#189) |
| 연속지적도 원본 | **EPSG:5186**(중부원점) | `prj` 없는 배포본 주의 |

### 필수/선택 필드
- pydantic `Field(...)` → 필수 / `Optional[...] = Field(None, ...)` → 선택, 생략 시 `null`

### 에러 응답 공통 포맷
```json
{ "detail": "필지 ID 123에 대한 기존 모의 심의 시뮬레이션 이력이 존재하지 않습니다." }
```
`detail` 안에 `[ERROR_CODE]` 접두어 포함 예: `[OPENAI_QUOTA_EXCEEDED]`, `[GEOCODING_FAILED]`

STEP4 산출물 API 공통 에러:
| 상태 | 의미 |
|---|---|
| 404 | 해당 도메인/실행 산출물 없음 |
| 409 | 산출물 재생성 중 |
| 500 | 서버 내부 오류 |

### 캐시 (Redis)
```
key: simulation:result:{parcel_id}
value: result_json (전체 JSON 문자열)
ttl: 600초
```

### 서빙 원칙 (백엔드 메모)
- 사전 계산 산출물(STEP4, geometry_geojson 등)은 엔드포인트가 파일을 읽어 **패스스루**. Pydantic 모델은 검증·문서화용이며 GeoJSON 지오메트리는 재모델링하지 않고 dict 그대로 반환.
- STEP4는 매니페스트(`/results/{domain}`)로 도메인·실행·URL 일원 관리. 재생성 시 `execution_id`·`generated_at`·`engine_version` 갱신.

### 데이터 계층 구조 (참고 · #189)
| 계층 | 내용 | 적재 단위/시점 |
|---|---|---|
| 1계층 | 행정동/시군구/시도 경계, 행정동 크로스워크 | 지역 단위, 상시 적재 |
| 2계층 | 연속지적도(구별), 국유부동산(지오코딩 필요) | 지역 단위, 사용자 선택 시 |
| STEP2 정제본 | geometry 8종 | 도메인×지역, PostGIS |
| STEP4 산출물 | topN/score_grid/exclusion | **실행 단위**, 감사 대상 |
| 3계층 | 도메인별(흡연 11종/재활용 9종 등) | 사용자 업로드 시 |
