# api_schema.md 최종본 설명 (팀 공유용)

> [api_schema.md](api_schema.md) v1.0이 어떻게 확정됐는지, 무엇이 바뀌었고 무엇이 남았는지 정리. 팀원 공유 전 읽는 안내문.
> 작성일: 2026-07-30

---

## 1. 무엇인가
- [api_schema.md](api_schema.md) = **프론트-백엔드 API 계약 최종본(v1.0)**.
- 근거: `app/schemas/*.py`(실제 pydantic 모델) + **STEP4 실제 산출물 샘플**(`step4_output.zip`, `흡연_weight_set.json`) + 이슈 **#179 / #189**.
- 기존 API(auth/lands/ahp/simulations/audit)는 코드와 대조해 정확함을 확인했고, STEP4는 추정본을 **실측으로 전면 교체**함.

## 2. 버전 히스토리
| 버전 | 상태 | 비고 |
|---|---|---|
| api_schema_k (v1) | 폐기 | STEP4 추정 + 좌표계 5179 오기 |
| api_schema_k2 (v0.3) | 폐기(승격) | STEP4 실측 반영, 단 5179 잔존 |
| **api_schema.md (v1.0)** | **최종** | 5179→5186 확정, #189 저장 요건 반영 |

## 3. 확정된 핵심 결정 3가지

### ① 좌표계 = 5186 (5179는 오기였음) ✅
- 과거 문서가 "데이터팀 DB = EPSG:5179 / `geom_5179`"라고 적었으나 **사실이 아님**.
- **live DB 실측**: srid 5179 컬럼 0개, `geom_5179` 컬럼 0개. 실제는 **`geom_5186`**(`GENERATED ALWAYS AS ST_Transform(geom,5186) STORED`). srid 분포 = 4326(17)·5186(17)뿐.
- 이슈 #189도 **"SRID 5186 등록 여부"**를 물음 → 5186 재확인. **STEP4·데이터팀 DB 모두 5186이라 통일 작업 자체가 불필요.**
- ⇒ 과거 "5179 vs 5186 좌표계 불일치, 통일 필요"라는 **최우선 블로커는 존재하지 않는 문제였음.**

### ② 좌표계 저장 정책 (#189 반영) ✅
- **표출/API = 4326, 연산 = 5186** (양쪽 이슈 일치).
- #189 ⚠️: STEP4 geometry(topN/exclusion)는 표출본 4326과 별개로 **감사·재연산용 5186 geometry도 보관**. `topN.geojson` 표출본은 4326 유지.
- 우리 DB의 `geom`(4326)+`geom_5186`(자동생성) 이중 구조가 이 요건을 **이미 충족**.

### ③ STEP4 산출물 = 실행 단위(execution-level) (#189) ✅
- topN/score_grid/exclusion은 파이프라인 **실행마다** 생성 → 매니페스트에 `execution_id` + 입력 4종 참조(`audit_result_reviewed`, `clean_report`, `candidate gpkg`, `weight_set`) 포함.
- 저장 계층: PostGIS(STEP2 geometry 8종) · Redis(중간·HITL) · 영속(감사 자료) · 대용량 파일(_preview.csv 15MB 등)은 **DB 미적재**.

## 4. 추정 → 실측으로 바로잡힌 것 (STEP4)
| 파일 | 추정본(구) | 실측(최종) |
|---|---|---|
| score_grid | `grid_spec{origin,rows,cols}`+객체 cells | `{spacing_m, count, score_min/max, cells:[[lng,lat,score,flag]]}` |
| exclusion | `{exclusion_id, legal_basis, ...}` | `{dataset_id, type, radius_m, label}` |
| topN | 영문(`rank,score,dong_code8`) | **한글**(`순위,점수,법정동코드,내접폭,국유_지분율`…) |
| report | `{ceiling_reason, gap_report}` | `{coverage{n_max,cumulative,marginal,reach,knee,ceiling,unreached}, data_gap}` |
| 누락 | — | `weight_set.json` + CSV 2종 추가 |

## 5. 아직 열려 있는 항목 (STEP4팀 회신 필요)
api_schema.md **§5-8** 참조. 요약:
1. score_grid `flag`(4번째 값 0/1)의 의미
2. exclusion simplify 메타 / `type`·`radius_m` 정의
3. **`법정동코드` ↔ 행정동 매핑** — topN은 법정동(10자리), 크로스워크는 행정동 → 직접 조인 불가(다대다). **지역 매칭 시 실제 사고 소지, 우선 확인.**
4. `ceiling_reason` 텍스트 필드 추가 여부
5. CSV 2종(`topN_min` vs `topN_min_pool0`) 차이
6. 실행 식별자·생성 메타 표준 포함 여부(#189)
7. 감사용 5186 geometry 보관 형식(별도 파일 vs PostGIS)

## 6. 정리한 파일 (docs)
| 파일 | 처리 |
|---|---|
| `api_schema.md` | ✅ **최종본(v1.0)** — 유지 |
| `api_schema_최종본_설명.md` | ✅ 본 문서 — 유지 |
| `api_schema_k.md` | 🗑 삭제(폐기: 추정+5179 오기) |
| `api_schema_k2.md` | 🗑 삭제(내용이 최종본으로 승격) |
| `api_schema_불일치_정리.md` | 🗑 삭제(§1 전제가 거짓이었고, 유효 항목은 최종본 §5-8로 이관) |

## 7. 다음 액션
- **프론트/AI팀 공유** → api_schema.md v1.0 기준.
- **§5-8 7개 항목**을 최승헌님께 회신 요청.
- 백엔드: `/api/v1/results/{domain}` 매니페스트 + 패스스루 엔드포인트 스텁 구현.
