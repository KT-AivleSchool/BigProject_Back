# pipeline_run_contract — 파이프라인 실행 API 계약

> **이 문서가 유일한 기준이다.**
> 백엔드 세션(BigProject_Back)과 프런트 세션(BigProject_Front)이 서로를 볼 수 없으므로,
> 두 세션은 이 문서에 적힌 필드명·값·구조를 그대로 따른다.
> 바꿔야 할 이유가 있으면 **구현하지 말고 사람에게 먼저 말한다.**

작성 2026-08-04 · 갱신 2026-08-04(백엔드 구현·실측 반영) · 범위: 픽스처 재실행(STEP2~4)만

> **갱신 내역** — 사람이 승인한 3건 + 실측 1건. 프런트 세션은 이 판을 기준으로 한다.
> ① 산출물 화이트리스트 4개 → 6개 + `clean_NN`(화면4가 `score_grid`·`topN_min` 을 읽는데 구판에 없었다)
> ② `steps[].label` 확정 (아래 2절, 실측)
> ③ STEP1 출력도 run 별로 가른다 (5절)
> ④ 로그·진행률 필드는 **넣지 않는다** — `steps[].sec` 로 충분하다는 판단(6절)

---

## 1. 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/v1/pipeline/runs` | 실행 시작 |
| GET | `/api/v1/pipeline/runs/{run_id}` | 상태 조회 (폴링) |
| GET | `/api/v1/pipeline/runs/{run_id}/log` | 실행 로그 (마스킹본) |
| GET | `/api/v1/pipeline/runs/{run_id}/artifacts/{name}` | 산출물 전달 |

### POST /api/v1/pipeline/runs

```json
// 요청
{"domain": "흡연", "mode": "fixture"}

// 202 Accepted
{"run_id": "r_20260804_001"}
```

- `mode` 는 현재 `"fixture"` 하나뿐이다. 다른 값은 400.
- 같은 `domain` 이 이미 `running` 이면 **409**.
- `run_id` 는 **백엔드가 만든다.** 프런트가 생성하지 않는다.

### GET /api/v1/pipeline/runs/{run_id}

아래 3절의 `status.json` 을 그대로 반환. 없는 run_id 는 404.

### GET /api/v1/pipeline/runs/{run_id}/log

자식 프로세스의 stdout+stderr 원본을 **마스킹해서** 내보낸다.
`text/plain; charset=utf-8` · 없는 `run_id` 는 404.

- `?tail=N` — 마지막 N 줄만. 폴링 tail 용. `N` 은 1 이상(0 은 422).
- **실행 중에도 읽힌다.** 쓰는 중인 파일을 읽으므로 **마지막 줄이 잘려 있을 수 있다.**
  로그의 성질상 허용한다 — 락을 걸면 자식 프로세스 출력이 막힌다.
- run 은 있는데 로그가 아직 없으면 **200 + 빈 본문**이다. 404 가 아니다 —
  "없는 run" 과 "아직 안 찍혔다"는 다른 사실이고 폴링하는 쪽은 구분할 수 있어야 한다.
- 흡연 픽스처 재실행 기준 **442줄 / 약 22KB**(마스킹 후).

🔴 **이 응답만 원본이 아니다.** 산출물은 "가공하지 않고 그대로"가 원칙인데(4절)
로그는 예외다. 이유: 우리가 무엇이 찍힐지 통제하지 않는다 — 파이프라인 모듈이 찍고,
예외 트레이스백이 찍고, 서드파티(pyogrio·geopandas)가 경고를 찍는다.

지운 자리는 **비워두지 않고 표시를 남긴다.** 조용히 없애면 원본인 척하게 된다(원칙 4).

| 원본 | 나가는 값 | 왜 |
|---|---|---|
| 저장소 절대경로 | `<repo>` | 서버 파일시스템 구조 |
| 사용자 홈 | `<home>` | **OS 계정명이 그대로 드러난다** |
| 인터프리터 폴더 | `<python>` | site-packages 경고가 같은 폴더를 찍는다 |
| 비밀 환경변수 값 | `<마스킹:이름>` | `KEY`·`SECRET`·`TOKEN`·`PASSWORD`·`DSN`·`DATABASE_URL` 이름 규칙 · 8자 이상 |
| `key=`·`token=` 등 쿼리 파라미터 | `<마스킹>` | 위 목록에 없는 출처(모듈에 박힌 키)용 2차 방어 |

실측(2026-08-05, 성공 실행): 절대경로 15+3+4건 치환 · **API 키 0건**.
0건인 것은 성공 실행이라서다 — 지오코딩·VWorld 호출이 실패하면 `key=` 가 붙은 요청
URL 이 트레이스백에 실릴 수 있고, **하필 그때가 로그를 제일 보고 싶은 순간이다.**
"지금 안 보인다"를 "앞으로도 안 나온다"로 읽지 않는다(원칙 5).

### GET /api/v1/pipeline/runs/{run_id}/artifacts/{name}

`name` 허용값 — **8개 + `clean_NN`**

| name | 실제 파일 | Content-Type | 어디서 쓰나 |
|---|---|---|---|
| `reviewed` | `step1/<pre>_audit_result_reviewed.json` | `application/json` | 화면2 감리 판정·HITL |
| `clean_report` | `step2/<pre>_clean_report.json` | `application/json` | 화면2b 정제 요약 |
| `candidates` | `step3/<pre>_후보_지적도필지.gpkg` | `application/geopackage+sqlite3` | 후보 필지 도형 (25MB) |
| `weight_set` | `step3/<pre>_weight_set.json` | `application/json` | 화면3 가중치 |
| `report` | `step4/<pre>_report.json` | `application/json` | 화면4 요약·data_gap |
| `topN` | `step4/<pre>_topN_min.csv` | `text/csv; charset=utf-8` | 화면4 Top-20 |
| `score_grid` | `step4/<pre>_score_grid.json` | `application/json` | 화면4 점수 히트맵 |
| `exclusion` | `step4/<pre>_exclusion.geojson` | `application/geo+json` | **화면2b 최종 판정** · 배제 레이어 도형 |
| `clean_NN` | 데이터셋별 정제 결과 (gpkg/parquet) | 확장자 따라감 | 개별 데이터셋 확인 |

- 🔴 **`candidates` · `clean_NN`(gpkg) 은 바이너리다.** `res.text()` 로 읽으면 조용히
  깨진다 — `arrayBuffer()`/`blob()` 으로 받아라. 2026-08-04 이전에는 이것들이
  `text/plain; charset=utf-8` 로 나갔다(`FileResponse` 에 `media_type` 미지정 →
  `mimetypes` 가 `.gpkg` 를 모름). **헤더가 내용에 대해 거짓말을 하고 있었다(원칙 4).**
  지금은 위 표대로 나가고, 모르는 확장자는 `text/plain` 이 아니라
  `application/octet-stream` 으로 떨어진다.
- 전부 `Content-Disposition: attachment` 다. `<a href>` 로 걸면 다운로드된다.
  `fetch` 로 받아 직접 렌더링할 것.

- `clean_NN` 은 `clean_01` … `clean_11` 형식이며 **`artifacts` 에는 안 들어간다.**
  개수가 도메인마다 다르고, 있는지 없는지는 `clean_report.json` 의 `results[]` 가 알려준다.
  확장자도 데이터셋마다 다르므로 이름으로 추측하지 말고 `results[].output` 을 보고 부른다.
- **`name` 을 경로로 사용하지 않는다.** 화이트리스트 → 실제 경로 매핑으로만 해석한다 (경로 조작 방지).
  `clean_NN` 도 `clean_report.json` 에 실제로 있는 `dataset_id` 만 통과한다.
- 아직 생성되지 않은 산출물, 허용되지 않는 이름 — 둘 다 404.
- **`reviewed` 만 run 생성 직후부터 200** 이다. 단계가 만드는 게 아니라 `_prepare_dirs`
  가 픽스처에서 복사해 넣기 때문이다. `status` 의 `artifacts.reviewed` 도 `queued`
  시점부터 URL 이다 — 화면 2 는 단계 완료를 기다릴 필요가 없다.

  `reviewed` 의 최상위 키는 **`_schema` · `results` · `facility_inference`** 셋이다(실측).
  화면 2 가 쓸 것은 `results[]` 안에 있다 — 데이터셋 11건, 각각
  `dataset_id` · `summary` · `roles[]` · `coord_status` · `cleaning_ops` · `hitl_flags[]`.
  사람 확인 대상은 `hitl_flags[]`(흡연 픽스처 **3건**)와 `roles[].need_review` 다.
  `_schema.필드설명`·`role_types` 에 각 필드의 뜻이 들어 있으니 프런트가 라벨을
  하드코딩하지 말고 그걸 읽으면 된다.

  > 🔴 **`gap` 은 이 파일에 없다.** 화면4 의 `data_gap`(흡연 **6건**)은
  > `report` 산출물(`step4/<pre>_report.json`)의 `data_gap` 이다. 이름이 비슷해
  > 헷갈리기 쉬운데 **출처도 시점도 다르다** — `hitl_flags` 는 STEP1 감리가 낸
  > "사람이 확인해라"이고, `data_gap` 은 STEP4 가 낸 "이건 적용 못 했다"이다.

  `exclusion` 은 **레이어(배제 데이터셋)당 Feature 1개**인 FeatureCollection 이다
  (흡연 픽스처 **5개** · EPSG:4326 · 285KB). geometry 는 `MultiPolygon`,
  properties 는 `dataset_id` · `type` · `type_llm` · `type_source` · `radius_m` · `label`.

  > 🔵 **`type` 과 `type_llm` 을 둘 다 실어 보낸다.** 전자가 최종 판정, 후자가 LLM 제안이고
  > `type_source` 가 누가 정했는지다(픽스처는 5건 전부 `jimok_lift` — 지목 배수 판정, S9).
  > 실제로 흡연 `01`·`11` 은 LLM 이 `polygon` 이라 했는데 코드가 `mixed` 로 바꿨다.
  > **화면2b 는 "AI 가 이렇게 봤고 코드가 이렇게 확정했다"를 둘 다 보여줄 수 있다** —
  > 최종값만 보여주면 HITL 이 무엇을 뒤집는 건지 사람이 알 수 없다.

> ✅ **배제 union 면적은 `report` 에 있다** — `spatial.exclusion_union_km2`(흡연 `1.1107`).
> 같은 `spatial` 안에 `shape_lift`(S9 적용 여부)와 `width_m`(내접폭 분포 · `pass_min_width`)도 있다.
> 이전 판 계약서에 *"어떤 산출물에도 없다"* 고 적혀 있었는데 **틀렸다** — 그 뒤 `gam4_export`
> 에 들어갔다. 화면2b 는 면적을 `report` 에서, 도형·판정을 `exclusion` 에서 읽으면 된다.

---

## 2. 단계 (steps)

단계 id 는 확정된 전체 체계 `0-1/0-2 · 1-1/1-2/1-3 · 2 · 3-1/3-2 · 4-1/4-2/4-3` 를 따른다.
**픽스처 재실행 범위는 아래 6개뿐이다.** STEP0·1 은 실행하지 않으므로 `steps` 에 넣지 않는다.

| id | label (확정) | 실행 주체 | 실측 소요 |
|---|---|---|---|
| `2` | 정제 | `gam2_clean_data.py` | 26.6s |
| `3-1` | 후보 필지 생성 | `make_parcel_candidates.py` | 18.6s |
| `3-2` | 가중치 산정 | `run_weight_model.py` | 18.7s |
| `4-1` | 후보점 생성 | `gam4_site_select.py` `[B]` | 1.7s |
| `4-2` | 점수화·배제 적용 | `gam4_site_select.py` `[C]~[G]` | 12.4s |
| `4-3` | 위치 선정 | `gam4_site_select.py` `[H]~[J]` | 6.1s |

**id 와 개수는 구판 그대로다.** label 만 실제 실행 단계에 맞췄다 — 구판의 `3-1 가중치 산정`
`3-2 가중치 확정` 은 실제와 달랐다. STEP3 은 *후보 필지 생성* 과 *가중치 산정* 두 프로세스이고,
가중치 "확정"이라는 단계는 없다(`--auto-weight` 가 HITL 을 건너뛴다).

> ⚠ **`4-1`·`4-2`·`4-3` 은 프로세스 하나 안에 있다.** 경계는 stdout 문구로만 잡는다.
> 문구는 계약이 아니다 — `gam4_site_select.py` 의 print 가 바뀌면 경계를 놓친다.
> 그때는 프로세스가 정상 종료했으므로 `done` 으로 닫되 **`sec` 은 `null`** 이다.
> 프런트는 `sec: null` 인 `done` 을 정상으로 다뤄야 한다.
>
> 실측 소요는 흡연 도메인 warm 캐시 기준이며 **보증값이 아니다.** 진행률 계산에 쓰려면
> 프런트가 자기 상수로 갖되, 어긋나도 되는 값으로 다뤄라.

---

## 3. status.json

`runs/<run_id>/status.json` 에 기록하고, GET 응답으로 그대로 내보낸다.

```json
{
  "run_id": "r_20260804_001",
  "domain": "흡연",
  "mode": "fixture",
  "status": "running",
  "steps": [
    {"id": "2",   "label": "정제",           "status": "done",    "sec": 26.62},
    {"id": "3-1", "label": "후보 필지 생성",  "status": "running", "sec": null},
    {"id": "3-2", "label": "가중치 산정",     "status": "idle",    "sec": null},
    {"id": "4-1", "label": "후보점 생성",     "status": "idle",    "sec": null},
    {"id": "4-2", "label": "점수화·배제 적용", "status": "idle",   "sec": null},
    {"id": "4-3", "label": "위치 선정",       "status": "idle",    "sec": null}
  ],
  "artifacts": {
    "reviewed": "/api/v1/pipeline/runs/r_20260804_001/artifacts/reviewed",
    "clean_report": "/api/v1/pipeline/runs/r_20260804_001/artifacts/clean_report",
    "candidates": null,
    "weight_set": null,
    "report": null,
    "topN": null,
    "score_grid": null,
    "exclusion": null
  },
  "error": null,
  "started_at": "2026-08-04T14:02:11",
  "finished_at": null
}
```

### 필드 규약

| 필드 | 값 |
|---|---|
| `status` | `queued` \| `running` \| `succeeded` \| `failed` |
| `steps[].status` | `idle` \| `running` \| `done` \| `failed` |
| `steps[].sec` | 완료된 단계의 소요 초(float). 미완료면 `null` |
| `artifacts[name]` | 생성됐으면 **GET URL 문자열**, 아직이면 `null` |
| `error` | `failed` 일 때만 문자열. 그 외 `null` |
| `started_at` / `finished_at` | ISO 8601. 진행 중이면 `finished_at` 은 `null` |

- `artifacts` 에 **서버 내부 파일 경로를 넣지 않는다.** 프런트가 그대로 fetch 할 URL을 넣는다.
- `artifacts` 의 키 8개는 항상 존재한다. 값만 `null` ↔ URL 로 바뀐다.
  (`reviewed` 는 예외적으로 `queued` 때부터 URL — 위 1절 참조)
  `clean_NN` 은 여기 없다 — 개수가 도메인마다 다르다. `clean_report` 를 읽고 부른다.
- **`steps[].sec` 외에 로그·진행률 필드는 없다.** 프런트 명세(v7)의 실시간 로그 패널과
  "실측 소요시간 비율" 진행률은 `steps[].sec` 로 계산한다 — 사람 판단(2026-08-04):
  *"로그도 진행률 보여주려고 넣어둔 거니까 진행률만 떠도 괜찮다."*
  run 로그는 `runs/<run_id>/run.log` 에 남지만 **API 로 내보내지 않는다.**
  필요해지면 그때 필드를 추가한다 — 미리 만들지 않는다.

---

## 4. 양쪽이 함께 지킬 것

- **실패한 run 도 `status.json` 을 남긴다.** `status: "failed"` + `error` 채움.
  프런트는 `failed` 를 정상 상태 중 하나로 다룬다. 예외로 던지지 않는다.
- **폴링은 `succeeded` 또는 `failed` 가 되면 멈춘다.** 간격 1~2초.
- `--auto-weight` 는 방향 판정 충돌이 있으면 `ValueError` 로 죽는다. **이건 정상 동작이다.**
  삼키지 말고 `failed` + `error`(stderr 마지막 줄)로 그대로 노출한다.
- 산출물 값을 라우터에서 **가공하지 않는다.** 파일 그대로 내보낸다.
- `input()` 을 새로 추가하지 않는다. 어떤 이유로도.

---

## 5. 🔴 픽스처 보호

- 실행은 **백엔드 세션만** 한다. 프런트 세션은 파이썬 스크립트를 돌리지 않는다.
- run 마다 `OMNISITE_STEP1_DIR` / `STEP2_DIR` / `STEP3_DIR` / `STEP4_DIR` 를
  `runs/<run_id>/stepN/` 으로 가른다.
  **STEP1 도 가른다** (구판에는 STEP2~4 만 있었다 · 2026-08-04 사람 승인).
  안 가르면 파이프라인이 정본 `step1_output/` 의 `reviewed.json` 을 읽는다. 누가 STEP1 을
  다시 돌리면 같은 `mode:"fixture"` 요청이 **조용히 다른 값**을 낸다. run 준비 때
  `<도메인>_FIX/reviewed.json` 을 이 폴더에 덮어써서 감리 입력을 고정한다.
- **`OMNISITE_DATA_ROOT` 는 바꾸지 않는다.** 바꾸면 `SEARCH_CACHE_DIR` 까지 갈라져
  캐시 이득이 0 이 되고 LLM 호출이 폭증한다.
- 자식 프로세스에 `PYTHONIOENCODING=utf-8` 과 `PYTHONUNBUFFERED=1` 을 **반드시** 넘긴다.
  · 앞의 것이 없으면 cp949 콘솔에서 이모지 출력 순간 `UnicodeEncodeError` 로 죽는다.
  · 뒤의 것이 없으면 stdout 이 8KB 블록 버퍼라 `4-1`·`4-2`·`4-3` 마커가 **종료 직전에
    한꺼번에** 도착한다 — 진행률이 거짓말을 한다(2026-08-04 실측).
- 서버 파이썬과 파이프라인 파이썬이 다르면 `OMNISITE_PYTHON` 으로 후자를 지정한다.
  파이프라인은 geopandas·shapely·pyarrow 를 요구한다.
- `data_임시/흡연/` 에 쓰지 않는다. 회귀 픽스처가 거기 걸려 있다.
- 작업 후 `python 검증용/check_fixture.py 흡연` 이 **57/57** 이어야 한다.
  (스크립트는 저장소 루트가 아니라 `검증용/` 에 있다.)
  46 은 2026-08-03 판 항목 수다. S5(A) 계측이 들어가며 57 로 늘었다 —
  **숫자가 다르면 픽스처가 기준이다.** 여기 적힌 건 사본이다.

---

## 6. 미확정 (사람이 확정한다)

| 항목 | 상태 |
|---|---|
| ~~`steps[].label` 실제 문구~~ | ✅ 확정 (2절) |
| ~~`run_id` 생성 규칙~~ | ✅ `r_YYYYMMDD_NNN` (예 `r_20260804_002`). 백엔드가 만든다 |
| ~~산출물 파일명 프리픽스~~ | ✅ 화이트리스트 매핑 내부에서 처리 |
| ~~배제 union 면적 노출~~ | ✅ **틀린 기록이었다.** `report.json` 의 `spatial.exclusion_union_km2` 에 이미 있다(흡연 `1.1107`) |
| ~~로그 노출~~ | ✅ `GET /runs/{id}/log` (2026-08-05). 진행률 필드는 여전히 안 넣는다 — `steps[].sec` 로 대체 |
| STEP0·1 실행 | ❌ 아직. 화면1 업로드·감리 배선이 선행이다 (`RagVectorStorage` 를 요청 시점 생성으로) |
| HITL 게이트 (`mode: "hitl"`) | 🔵 7절에 설계. 사람 승인 완료(2026-08-05) · 구현 전 |

**되돌린 결정 (지우지 않고 이유를 남긴다)**

- `--auto-radius` 를 쓰려다 **뺐다.** 쓰면 `run_weight_model.py` 가
  `radius_conf["_confirmed"] = True` 를 안 찍는다(같은 파일 283행). 픽스처는 `--radius` 로
  반경을 고정한 실행이라, `--auto-radius` 로 돌리면 조건이 달라진다.
- `--no-diag --bootstrap 0` 도 **뺐다.** 픽스처가 기록한 `조건` 에 없다.
  진단이 가중치를 안 바꾼다고 "알고는" 있지만, 안 재본 것을 같다고 단정하지 않는다.
- 러너에 `--facility` / `--region` 을 넘기려다 **뺐다.** 파이프라인이 `reviewed.json` 에서
  스스로 읽으므로 값은 같지만, 넘기는 순간 도메인 값이 러너에 박힌다(하드코딩 금지).

---

## 7. HITL 게이트 — `mode: "hitl"` (설계 · 구현 전)

작성 2026-08-05 · 사람 승인 완료 · **구현 전이다. 프런트는 이 절을 보고 미리 짜되,
동작 확인 전까지 "된다"고 단정하지 않는다.**

### 7-1. 왜 게이트인가 — 되돌린 설계를 먼저 남긴다

처음에 백엔드는 **"끝까지 돌린 뒤 값을 뒤집고 재실행"** 으로 설계했다. **틀렸다.**
그 모델에서는 뒤집을 때마다 앞 단계를 다시 돌려야 해서 재실행 범위·`reused` 상태·
부분 재실행(복사 / 부모참조 / 전체) 같은 문제가 줄줄이 딸려 나왔다.
**전부 잘못된 전제에서 파생된 가짜 문제였다.**

원인: 그때 러너에 `mode: "fixture"`(무입력 완주) 하나뿐이었고, **백엔드가 자기가 만든
것을 파이프라인의 모습으로 착각**했다. `stdin=subprocess.DEVNULL` 은 러너가 박은
것이지 파이프라인의 성질이 아니다. (사람 지적 2026-08-05)

**실제 파이프라인은 원래 게이트 구조다.** `input()` 호출 수 실측:

| 모듈 | `input()` | 역할 |
|---|---|---|
| `gam2_audit_judgment_test.py` | **4** | 게이트A (STEP1 끝) |
| `run_weight_model.py` | **3** | 게이트B (STEP3 중간) |
| `gam4_site_select.py` | **0** | 없음 |
| `gam2_clean_data.py` · `make_parcel_candidates.py` · `gam2_run_pipeline.py` | **0** | 없음 |

```
STEP0/1 감리 ─▶ ⏸ 게이트A ─▶ STEP2 ─▶ STEP3-1 ─▶ ⏸ 게이트B ─▶ STEP3-2 ─▶ STEP4 ─▶ 끝
                배제반경                             [R] 집계반경
                데이터의도                           [W] 가중치(-1~+1)
                지역코드
```

**재실행은 0회다.** 멈췄다가 이어간다.

### 7-2. `mode` 두 값의 차이

| `mode` | 게이트 | 용도 |
|---|---|---|
| `fixture` | **없음** — 무입력 완주 | 회귀 검증. 지금 있는 것 |
| `hitl` | **있음** — 게이트A·B 에서 멈춤 | 실제 사용 |

🔴 픽스처 모드에 게이트를 넣으면 안 된다. 사람 입력이 끼는 순간
`check_fixture.py` 57/57 이 재현 불가가 된다.

### 7-3. 상태 — `status` 값이 하나 늘어난다

```
queued → running → awaiting_hitl → running → … → succeeded | failed
```

`awaiting_hitl` 일 때 `status.json` 에 `gate` 객체가 붙는다:

```json
{
  "status": "awaiting_hitl",
  "gate": {
    "id": "audit",            // "audit"(게이트A) | "weight"(게이트B)
    "questions": [ ... ]      // 7-4 · 7-5
  }
}
```

- **폴링 종료 조건이 바뀐다.** 지금까지는 `succeeded`/`failed` 였다.
  이제 `awaiting_hitl` 도 멈춰야 한다 — 답을 주기 전까지 영원히 안 바뀐다.
- `gate` 는 `awaiting_hitl` 일 때만 있다. 그 외에는 **키 자체가 없다**(`null` 아님).
- 답을 받으면 즉시 `running` 으로 돌아가고 `gate` 는 사라진다.

### 7-4. 게이트A — `POST /runs/{run_id}/hitl/audit`

`review_hitl`(`gam2_audit_judgment_test.py:1534`)이 처리하는 flag 3종 그대로다.

```json
// 요청
{
  "exclusions": [
    {"dataset_id": "01", "role_index": 0, "radius_m": 10}
  ],
  "intents": [
    {"dataset_id": "12", "choice": 1, "weight": 0.6}
  ],
  "code_prefixes": [
    {"dataset_id": "04", "op_index": 0, "prefix": "11170"}
  ]
}
```

| 필드 | 값 | 주의 |
|---|---|---|
| `radius_m` | 정수 · **`null` = "반경 없음(면 배제)"** · 키 생략 = 건너뜀(미확정 유지) | `null` 과 생략이 **다른 뜻**이다 |
| `choice` | 1 가점 · 2 감점 · 3 배제 · 4 참조용 · 5 제외 | `weight` 는 1·2 일 때만 |
| `weight` | 크기. `choice` 가 부호를 정한다 | 여기는 슬라이더가 아니다 |
| `prefix` | 행정 코드 접두 | 🔴 틀려도 행 수가 그럴듯해서 **자동 검증으로 못 걸러낸다** |

- 대상 지목: `(dataset_id, role_index)` / `(dataset_id, op_index)`.
  **`results[]` 배열 인덱스는 쓰지 않는다** — 순서가 바뀌면 조용히 다른 걸 가리킨다.
- 백엔드는 `apply_radius_answer` · `apply_intent_answer` 를 **그대로 부른다.**
  새로 짜면 CLI 와 API 가 갈린다.
- `code_prefixes` 는 `status: "auto_confirmed"` 인 항목이면 질문에 안 나온다
  (코드표로 이미 확정 — 사람 확인 생략).

### 7-5. 게이트B — `POST /runs/{run_id}/hitl/weight`

```json
{
  "radius": {"07+02": 150, "06+03": 300, "08": 50, "09": 150, "10": 250},
  "weights": {"07+02": 0.75, "09": -0.4}
}
```

`run_weight_model.py` 의 **기존 `--radius`·`--weight` 인자**(`:196`·`:202`)로 넘어간다.
픽스처 모드가 지금 쓰는 바로 그 경로다.

🔴 **`weights` 는 `-1 ~ +1` 을 그대로 보낸다. 프런트가 분해하지 않는다.**
이유가 두 개다:
1. 미리 분해해 `{seed_weight, direction}` 으로 보내면 `normalize_matrix` 의 cost 반전과
   **이중으로 걸려 조용히 뒤집힌다.**
2. 분해 지점(`apply_weight_hitl:1129-1133`)이 `direction_source` 를 찍는다.
   프런트가 분해하면 **누가 방향을 정했는지가 산출물에서 사라진다** — 규약 위반.

주의:
- **`0` 은 "그 지표 제외"** 이고, 이때 `direction` 은 **원래 값을 유지**한다(`:1129`).
- 전 지표 절대값 합이 0 이면 `ValueError` → 400. 조용히 안 넘어간다(`:1108`).
  **이래서 항목별 PATCH 가 아니라 화면 단위 배치 POST 다** — 한 항목만 받으면
  합이 0 이 되는지 알 수 없고, 검증을 빼면 전 후보 점수가 0 이 된다.
- 없는 지표 id 는 `ValueError` → 400 (`:1106`).

### 7-6. STEP4 는 HITL 을 넣지 않는다 (1차)

`gam4_site_select.py` 의 `input()` 은 **0개**이고 `CLAUDE.md` 설계도
"HITL 위치: STEP1 · STEP3" 이다. 일치한다.

🔴 **결과 하나를 명시해 둔다.** S9 배제판정 `배제판정_확인요청` gap(흡연 4건)은
**STEP4 에서 계산된다.** `load_exclusions`(`:749`)가 STEP2 정제 결과를 필요로 해서
게이트A 시점에는 아직 존재하지 않는다 — **앞으로 당길 수 없다.**

→ 화면 2b 의 S9 판정 표는 **읽기 전용**이다. 뒤집으려면 새 run 을 시작해야 한다.
버튼을 붙이지 않거나, 붙인다면 "다음 실행에 반영"임을 화면에 적어야 한다.

### 7-7. 미해결 — `save_to_exclusion_cache` 전역 쓰기

`apply_radius_answer:720` 이 run 폴더 **밖**에 쓴다:
```
data_임시/search_cache/exclusion_radius_cache.json   키 = facility_type ("금연구역")
```
**도메인 구분이 없다.** 흡연에서 확정한 "금연구역 50m" 가 성동구에도 적용된다.

- 픽스처 회귀(57/57)는 **위험하지 않다.** 캐시는 `enrich_hitl_flags`(STEP1 감리 시점)
  에서만 읽히고, 픽스처 모드는 `reviewed.json` 을 통째로 덮기 때문이다. (실측 확인)
- 남는 위험은 **도메인 간 오염**뿐이다. 화면1(실제 감리 실행)을 붙일 때 다룬다.
  그때 `apply_radius_answer` 에 `cache=False` 를 추가할지 결정한다 — 정본 수정이라
  사람 승인이 필요하다.
