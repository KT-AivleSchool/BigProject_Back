# scripts/ — 데이터 적재 스크립트 (재현용)

STEP2 정제 산출물·경계 원본을 PostGIS(`omnisite`)에 적재하는 멱등 스크립트 모음.
모두 **대상 테이블이 0행일 때만 적재**하므로 재실행해도 중복되지 않는다.

## 사전 준비
- `.env` 의 `DATABASE_URL` 설정 — **기본값은 없다. 없으면 `SystemExit`** 이다(2026-08-09).
  예전 기본값이 `postgres:postgres` 였고 2026-08-07 로컬 DB 침해가 정확히 그 조합이었다.
- 가상환경에 `geopandas`, `pyogrio`, `psycopg`, `pandas`, `python-dotenv` 설치
- **원본 데이터 배치** (아래 경로는 `.gitignore` 대상 → 각자 Teams/감리팀에서 받아 배치):
  - `data_임시/region_data/` — `BND_{SIDO,SIGUNGU,ADM_DONG}_PG.shp`, `행정동_크로스워크.csv`

## 실행 순서
```bash
# 1계층 경계 3종 + 크로스워크 (테이블 신설 + 적재)
python scripts/load_region_boundaries.py            # DRY-RUN (건수 확인)
python scripts/load_region_boundaries.py --commit    # DDL(schema_region_boundaries.sql) + 적재
```

## 적재 결과 (2026-08-03 실측)
| 스크립트 | 테이블 | 행수 |
|---|---|---|
| load_region_boundaries | sido_boundaries / sigungu_boundaries / adm_dong_boundaries / admin_crosswalk | 17 / 252 / 3,559 / 3,555 |

🔴 **`load_domain_from_gpkg.py` 는 2026-08-11 에 지웠다.** 적재 대상이던
`smoking_areas`·`commercial_shops`·`street_trash_bins` 를 포함한 흡연 도메인 데이터셋
8개를 DB 에서 제거했기 때문이다 — 프리셋 원본은 이제 DB 가 아니라 **디스크**에 둔다
(이슈 #215 계층 구분: 1계층 전국 경계·크로스워크 / 2계층 지역단위 지적·공유지 / 산출물).
파이프라인은 그 테이블들을 읽은 적이 없다(`app/services/poi_context.py` 가 STEP2 산출물
파일을 읽는다). 삭제 직전 덤프는 `data_임시/_db_dump_20260811/*.csv` 에 있다.

## 좌표계 규약
- 저장: `geom` = EPSG:4326(표출) / `geom_5186` = `GENERATED ALWAYS AS ST_Transform(geom,5186) STORED`(연산)
- 경계 원본 SHP 는 5186 → 적재 시 4326 변환, invalid 폴리곤은 `ST_MakeValid` 처리.

## 아직 안 된 것
- 기존 `dong_boundaries`(app 전용): `geom_5186` 미보유.

🔴 여기 「0행 도메인 테이블 7종(cctv/공중화장실/소방용수/공영주차장/문화행사/담배꽁초/
흡연폴리곤) 원본 CSV 확보 필요」라고 적어뒀던 건 **폐기한다**(2026-08-11). 그 7종은
2026-08-11 에 DB 에서 지웠다 — 채울 게 아니라 **애초에 DB 에 둘 것이 아니었다**.
남겨두면 다음 사람이 없는 원본을 찾으러 다닌다.
