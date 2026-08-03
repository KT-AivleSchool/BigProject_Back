# scripts/ — 데이터 적재 스크립트 (재현용)

STEP2 정제 산출물·경계 원본을 PostGIS(`omnisite`)에 적재하는 멱등 스크립트 모음.
모두 **대상 테이블이 0행일 때만 적재**하므로 재실행해도 중복되지 않는다.

## 사전 준비
- `.env` 의 `DATABASE_URL` 설정 (없으면 `postgresql://postgres:postgres@localhost:5432/omnisite` 기본값)
- 가상환경에 `geopandas`, `pyogrio`, `psycopg`, `pandas`, `python-dotenv` 설치
- **원본 데이터 배치** (아래 경로는 `.gitignore` 대상 → 각자 Teams/감리팀에서 받아 배치):
  - `data_임시/region_data/` — `BND_{SIDO,SIGUNGU,ADM_DONG}_PG.shp`, `행정동_크로스워크.csv`
  - `data_임시/step2_output/흡연_1차/` — `흡연_clean_*.gpkg`

## 실행 순서
```bash
# 1) 1계층 경계 3종 + 크로스워크 (테이블 신설 + 적재)
python scripts/load_region_boundaries.py            # DRY-RUN (건수 확인)
python scripts/load_region_boundaries.py --commit    # DDL(schema_region_boundaries.sql) + 적재

# 2) 도메인 데이터 (흡연/용산 gpkg → 0행 테이블)
python scripts/load_domain_from_gpkg.py             # DRY-RUN
python scripts/load_domain_from_gpkg.py --commit
```

## 적재 결과 (2026-08-03 실측)
| 스크립트 | 테이블 | 행수 |
|---|---|---|
| load_region_boundaries | sido_boundaries / sigungu_boundaries / adm_dong_boundaries / admin_crosswalk | 17 / 252 / 3,559 / 3,555 |
| load_domain_from_gpkg | smoking_areas / commercial_shops / street_trash_bins | 8 / 15,722 / 280 |

## 좌표계 규약
- 저장: `geom` = EPSG:4326(표출) / `geom_5186` = `GENERATED ALWAYS AS ST_Transform(geom,5186) STORED`(연산)
- 경계 원본 SHP 는 5186 → 적재 시 4326 변환, invalid 폴리곤은 `ST_MakeValid` 처리.

## 아직 안 된 것
- 0행 도메인 테이블 7종(cctv/공중화장실/소방용수/공영주차장/문화행사/담배꽁초/흡연폴리곤): 흡연 gpkg에 소스 없음. 원본 CSV 확보 필요.
- 기존 `dong_boundaries`(app 전용): `geom_5186` 미보유.
