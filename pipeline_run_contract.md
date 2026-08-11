# pipeline_run_contract — 파이프라인 실행 API 계약

> **이 문서가 유일한 기준이다.**
> 백엔드 세션(BigProject_Back)과 프런트 세션(BigProject_Front)이 서로를 볼 수 없으므로,
> 두 세션은 이 문서에 적힌 필드명·값·구조를 그대로 따른다.
> 바꿔야 할 이유가 있으면 **구현하지 말고 사람에게 먼저 말한다.**

작성 2026-08-04 · 갱신 **2026-08-05(HITL 게이트 구현·실측 완료)** ·
범위: STEP2~4 재실행 — `fixture`(무입력 완주) · `hitl`(게이트 2개)

> **갱신 내역** — 프런트 세션은 이 판을 기준으로 한다.
> ① 산출물 화이트리스트 4개 → 6개 + `clean_NN`(화면4가 `score_grid`·`topN_min` 을 읽는데 구판에 없었다)
> ② `steps[].label` 확정 (아래 2절, 실측)
> ③ STEP1 출력도 run 별로 가른다 (5절)
> ④ 로그·진행률 필드는 **넣지 않는다** — `steps[].sec` 로 충분하다는 판단(6절)
> ⑤ **2026-08-05 — 7절 HITL 게이트가 설계에서 구현으로 바뀌었다.**
>    엔드포인트 `POST /runs/{run_id}/hitl/{gate_id}` 신설 · `status` 에 `awaiting_hitl`
>    추가 · 게이트B 답변 본문 확정(`{run_id, radius, slider}`, `weights` 아님) ·
>    **폴링 종료 조건에 `awaiting_hitl` 이 추가된다**(7-3)
> ⑥ **2026-08-05 — 진단·대조 스크립트 경로가 `검증용/` → `app/tools/` 로 바뀌었다**(5절·7-8절).
>    계약 내용은 그대로다. 예전 판을 보고 `검증용\...` 을 치면 파일이 없다.

---

## 1. 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/v1/pipeline/runs` | 실행 시작 |
| GET | `/api/v1/pipeline/runs/{run_id}` | 상태 조회 (폴링) |
| POST | `/api/v1/pipeline/runs/{run_id}/hitl/{gate_id}` | **HITL 게이트 답변** (7절) |
| GET | `/api/v1/pipeline/runs/{run_id}/log` | 실행 로그 (마스킹본) |
| GET | `/api/v1/pipeline/runs/{run_id}/artifacts/{name}` | 산출물 전달 |

### POST /api/v1/pipeline/runs

```json
// 요청 — fixture · hitl
{"domain": "흡연", "mode": "fixture"}

// 요청 — full (8절). user_input 필수 · topn 선택(기본 20)
{"domain": "흡연", "mode": "full",
 "user_input": "용산구 흡연부스 부지 선정", "topn": 20}

// 202 Accepted
{"run_id": "r_20260804_001"}
```

- `mode` 는 `"fixture"` · `"hitl"` · `"full"` 세 값이다(7·8절). 다른 값은 400.
- `user_input`·`topn` 은 **`full` 전용**이다. 다른 모드에서 주면 **400** —
  받아놓고 안 쓰면 호출자는 반영됐다고 읽는다(원칙 4).
  `fixture`·`hitl` 의 실행 조건은 전부 `<도메인>_FIX/기준값.json` 에서 온다.
- 같은 `domain` 이 이미 `running` **또는 `awaiting_hitl`** 이면 **409**.
  게이트 대기는 "끝난 것"이 아니다 — 그 run 이 도메인을 계속 점유한다.
- `run_id` 는 **백엔드가 만든다.** 프런트가 생성하지 않는다.
- 🔴 **인증은 선택이다**(2026-08-12 신설 · 사람 결정). 이 엔드포인트 **하나**만 그렇다.
  세 갈래이고 **가운데가 없다** —

  | `Authorization` 헤더 | 결과 |
  |---|---|
  | 없음 (또는 `Bearer ` 빈 값) | **202** · 익명 run (`user_id: null`) |
  | 유효한 access token | **202** · 그 사람이 주인 (`user_id` 채움) |
  | 만료·위조·폐기(로그아웃)·`type≠access`·없는 사용자 | **401** · run 을 **시작하지 않는다** |

  세 번째를 조용히 익명으로 떨어뜨리지 **않는** 이유: 만료된 사람의 실행이 익명으로
  기록되면 화면은 로그인 상태인데 마이페이지에서만 안 보인다 — 안 터지고 값만 틀린다
  (원칙 1·4). 토큰이 왔는데 못 푸는 것은 「누구인지 모른다」가 아니라 **「무언가
  잘못됐다」**다. 401 을 만나면 프런트는 사람이 헤더 버튼으로 재발급한다(자동 재발급을
  일부러 안 한다 — RTR 이라 재발급 실패가 전 세션을 지운다).
  ✅ **살아 있는 서버 실측 (2026-08-12 재시작 후 · 사람 승인).** 만료 · 위조 ·
  refresh 를 access 자리 · 없는 사용자 **네 갈래 전부 401** 이고 `runs/r_*` 개수가
  **18 → 18**(네 번 쳤는데 run 이 하나도 안 늘었다). 빈 `Bearer ` 는 **202**
  (`r_20260812_006` · `user_id: null` · 89초 완주) — 익명이 살아 있다.
  🔴 **재시작 전에는 이게 전부 거짓이었다.** 같은 요청이 401 이 아니라 **202** 였고
  run 이 하나 생겼다(`r_20260812_005`) — 코드가 아니라 **살아 있는 uvicorn 이 변경 전
  프로세스**였기 때문이다(기동 00:19:07 ↔ 파일 mtime 01:07, `--reload` 없음).
  in-process 대조기 27/27 은 이걸 **원리적으로 증명하지 못한다**(자기 프로세스에 방금
  import 한 코드를 잰다). 이 표를 근거로 삼기 전에 **서버 기동 시각**을 먼저 본다.
  🔴 **여기서 강한 근거는 401 이 아니라 「18 → 18」이다.** 401 은 「거절했다」만 말하고,
  개수 불변이 **「`start_run` 에 닿기 전에 거절했다」**를 말한다 — 401 을 주면서 run 을
  만드는 구현도 401 이다. in-process 대조기는 `start_run` 이 목이라 이 구분을
  **원리적으로 못 본다**(프런트 지적 2026-08-12).
  ✅ **두 번째 줄(유효 토큰 → `user_id` 채움)도 실서버로 확인됐다** (2026-08-12 ·
  사람 승인 · 실 계정 `aivle2@test.com`). 로그인 **200**(access 60분) → `POST /runs`
  **202** `r_20260812_007` → `run_records.user_id=`**10**(발급 시점 `last_known_status`
  = `queued`) → **77초** 뒤 완주해도 **10 그대로**(`succeeded` · `loaded 13/20`) ·
  `status.json` 에도 `user_id: 10`(익명 `r_20260812_006` 은 `null`).
  🔴 여기서 새로 증명된 것은 「박힌다」가 아니라 **「종료 UPSERT 가 `user_id` 를 NULL 로
  안 덮는다」**다. 발급 INSERT 와 종료 UPSERT 는 다른 함수이고 `record_run_end` 는 행이
  없어도 도는 UPSERT 라 구조상 덮을 수 있는 모양이었다 — `check_run_records_e2e.py` §3 은
  **발급 INSERT 없이** UPSERT 만 재므로 이 조합을 못 본다.
  ⚠ 그 run 은 **안 지웠다**(사람 결정) — `run_records` 8행 중 유일하게 `user_id` 가 있는
  행이라, `mine=true` 가 붙었을 때 「내 것 1 + 주인 없는 것 7」로 갈리는지 볼 실물이다.
- 🔴 **주인은 발급 시점에 한 번 박힌다.** 로그인 전에 시작한 run 을 로그인 후에 내
  것으로 만드는 경로는 **없다** — 만들면 「누구 run 이었나」의 정본이 둘이 된다.
  그래서 `user_id` 가 비는 것은 사고가 아니라 **기록**이다(3-3).
- ⚠ 다른 엔드포인트(`GET /runs/{id}`·산출물·로그·게이트 답변)는 **인증을 아예 안 본다.**
  run_id 를 아는 사람은 누구나 읽고 답할 수 있다. 여기에 소유권 검사를 넣는 것은
  별개 결정이고 아직 안 했다.

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
  `results[]` 에는 **`label`·`label_source`** 도 있다(2026-08-11 추가) — 그 데이터셋의
  짧은 표시명이다. 값은 감리가 낸 `roles[].facility_type` 을 **옮겨 적은 것**이고,
  감리가 이름을 안 낸 데이터셋은 **`null`** 이다. 🔴 `null` 은 「못 찾았다」가 아니라
  「없다」다 — 프런트가 `{"01":"금연구역", …}` 같은 고정 사전으로 채우면 안 된다.
  `dataset_id` 는 업로드 파일명 가나다순이라 도메인이 바뀌면 번호가 다시 매겨진다
  (흡연 실측: 그런 사전 하나가 8개 중 4개를 틀렸다). `null` 이면 `filename` 을 쓴다.
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
**픽스처 재실행 범위는 아래 8개다.** STEP0·1 은 실행하지 않으므로 `steps` 에 넣지 않는다.

🔴 **단계 목록은 `mode` 가 정한다. 개수를 상수로 갖지 마라**(2026-08-10).
`full` 은 앞에 `0`·`1` 이 더 붙어 **10개**, `hitl` 은 게이트·제안 칸이 끼어 **8개**다(8절).
(게이트·제안 칸은 `steps` 에 안 들어간다 — 프로세스가 없다. 그래서 `hitl` 은 `fixture` 와
같은 8칸이다.)
안 도는 단계를 모든 모드에
같이 두면 영원히 `idle` 인 칸이 화면에 남아 진행률이 거짓말을 한다(원칙 4).
프런트는 `status.steps` 배열을 **그대로** 그리면 된다.

| id | label (확정) | 실행 주체 | 실측 소요 |
|---|---|---|---|
| `2` | 정제 | `gam2_clean_data.py` | 26.6s |
| `3-1` | 후보 필지 생성 | `make_parcel_candidates.py` | 18.6s |
| `3-2` | 가중치 산정 | `run_weight_model.py` | 18.7s |
| `4-1` | 후보점 생성 | `gam4_site_select.py` `[B]` | 1.7s |
| `4-2` | 점수화·배제 적용 | `gam4_site_select.py` `[C]~[G]` | 12.4s |
| `4-3` | 위치 선정 | `gam4_site_select.py` `[H]~[J]` | 6.1s |
| `적재-감리` | 감리 규칙 DB 적재 (토론 근거) | `scripts/load_audit_data.py` | 1.6s |
| `적재-후보` | 후보점 DB 적재 (화면5 목록) | `scripts/load_topn_candidates.py` | 2.8s |

🔴 **뒤 두 칸은 2026-08-11 에 `fixture`·`hitl` 에도 붙였다**(사람 결정). 그전엔 `full` 에만
있었고 사유는 「fixture 는 정본 산출물의 재생이고 그 Top-N 은 이미 `run_id='정본'` 으로
DB 에 있다」였다. 맞는 말이지만 **결론이 틀렸다** — `/candidates` 가 읽는 건 파일이 아니라
`booth_candidates` 이므로, 적재 칸이 없으면 그 run 의 `topN.geojson` 이 폴더에 있어도
**프런트는 닿지 못한다.** 즉 fixture run 의 결과는 화면5 에서 볼 수가 없었다.
시연에서 업로드를 건너뛰고 화면5까지 가려면 이 두 칸이 있어야 한다.

🔴 **`hitl` 은 같은 날 조금 뒤에 붙였다**(사람 지시). 처음엔 뺐고 사유는 「게이트에서
사람을 기다리므로 시연 프리셋이 아니다」였는데, 그건 **왜 `fixture` 에 넣는가**의 답이지
**왜 `hitl` 에서 빼는가**의 답이 아니다. 게이트를 지나 완주한 run 은 사람이 값을 확정한
run 이고, 그 결과를 화면5 에서 못 보는 건 `fixture` 와 **똑같은 구멍**이다.
지금은 **세 모드 다** 적재 칸을 갖는다.
누적 우려(그때의 반대 근거)는 `runs/` 정리 정책 쪽에서 받는다(`app/services/run_pruner.py`).

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
  "loaded": null,
  "user_id": null,
  "error": null,
  "started_at": "2026-08-04T14:02:11",
  "finished_at": null
}
```

### 필드 규약

| 필드 | 값 |
|---|---|
| `status` | `queued` \| `running` \| `awaiting_hitl` \| `succeeded` \| `failed` |
| `gate` | `awaiting_hitl` 일 때**만 존재하는 키**(7-3). 그 외에는 `null` 이 아니라 **키가 없다** |
| `steps[].status` | `idle` \| `running` \| `done` \| `failed` |
| `steps[].sec` | 완료된 단계의 소요 초(float). 미완료면 `null` |
| `artifacts[name]` | 생성됐으면 **GET URL 문자열**, 아직이면 `null` |
| `loaded` | 이 run 이 **DB 에 넣은 것**. 안 넣었으면 `null` (3-1) |
| `user_id` | 이 run 을 돌린 사람. **익명 실행이 정상 상태**라 보통 `null` (3-3) |
| `run_record_errors` | `run_records` 기록이 **실패했을 때만 생기는 키**(3-3). 성공이면 키가 없다 |
| `error` | `failed` 일 때만 문자열. 그 외 `null` |
| `started_at` / `finished_at` | ISO 8601. 진행 중이면 `finished_at` 은 `null` |

- `artifacts` 에 **서버 내부 파일 경로를 넣지 않는다.** 프런트가 그대로 fetch 할 URL을 넣는다.
- `artifacts` 의 키 8개는 항상 존재한다. 값만 `null` ↔ URL 로 바뀐다.
  (`reviewed` 는 예외적으로 `queued` 때부터 URL — 위 1절 참조)
  `clean_NN` 은 여기 없다 — 개수가 도메인마다 다르다. `clean_report` 를 읽고 부른다.
- **`steps[].sec` 외에 로그·진행률 필드는 없다.** 프런트 명세(v7)의 실시간 로그 패널과
  "실측 소요시간 비율" 진행률은 `steps[].sec` 로 계산한다 — 사람 판단(2026-08-04):
  *"로그도 진행률 보여주려고 넣어둔 거니까 진행률만 떠도 괜찮다."*
  run 로그는 `runs/<run_id>/run.log` 에 남고, **status 의 필드가 아니라 별도
  엔드포인트** `GET /runs/{run_id}/log` 로 나간다(1절, 2026-08-05 추가).
  ⚠ 여기 "API 로 내보내지 않는다"고 적혀 있던 건 그 엔드포인트가 생기기 전 문장이다
  (2026-08-10 정정). 상태를 적은 문장은 되돌리는 변경에서 같이 지워야 한다.

### 3-1. `loaded` — 이 run 이 DB 에 넣은 것 (2026-08-10 신설)

```json
"loaded": {
  "run_id": "r_20260810_006",
  "audit_rules": 13,
  "booth_candidates": 20,
  "cascaded": {
    "conflict_simulations": 0,
    "debate_logs": 0,
    "verified_precedents_unlinked": 0
  }
}
```

| 값 | 뜻 |
|---|---|
| `null` | 이 run 은 **아직 아무것도 적재하지 않았다.** 2026-08-11 부터 **세 모드 다** 적재 칸을 가지므로(8-5), `null` 은 「그 칸에 아직 안 닿았다」는 뜻이다. 옛 run 은 키 자체가 없어 `null` 로 채워진다 — 「기록 없음」과 구분이 필요하면 `steps` 의 `적재-감리`·`적재-후보` 칸을 본다 |
| 객체 | 넣었다. `run_id` 는 프런트가 `GET /api/v1/simulations/candidates` 의 **`run_id` 파라미터에 그대로 넣을 값**이다 |

- **`run_id` 를 값으로 준다.** 프런트가 "full 이면 최상위 run_id 와 같다"는 규칙을
  따로 들고 있게 하지 않는다 — 규칙을 양쪽이 각자 구현하면 언젠가 갈린다.
- 행 수의 출처는 **적재기 자신**이다. 두 로더가 마지막에 `[LOADED] table=… run_id=… rows=…`
  한 줄을 찍고 러너가 그것만 읽는다. 러너가 DB 에 다시 세면 **적재 이후에 다른 실행이
  건드린 값**을 이 run 의 성과로 적게 된다(원칙 4·5).
  러너는 그 줄의 `run_id` 가 이 run 과 다르면 **단계를 `failed` 로 닫는다** —
  적재기가 `--run` 을 무시하고 정본에 넣었는데 status 만 맞게 적히면,
  프런트가 `/candidates?run_id=` 로 조회했을 때 0건이 나온다.
- 🔴 **2026-08-10 이전에 만들어진 run 에는 이 키가 없다.** `read_status` 가 `null` 로
  채우는데, 그 `null` 은 "적재 안 함"뿐 아니라 **"기록이 없다"** 도 포함한다.
  산출물 키와 달리 디스크를 보고 사실을 복원할 수 없기 때문이다(행 수를 아는 건
  그때 돌았던 적재기뿐이다). 구분이 필요하면 **`steps` 의 `적재-감리`·`적재-후보`
  칸 상태**를 본다 — 그게 그 run 의 사실이다.
- 🔴 **`loaded` 는 과거의 기록이고, 나중에 고쳐 쓰지 않는다**(2026-08-11 확정).
  「그때 20행을 넣었다」는 그 뒤에 무슨 일이 있어도 **여전히 참**이다 — 지금 DB 에
  없다고 이 값을 지우거나 `invalidated_by` 같은 표시를 되쓰면 한 필드가 「넣었다」와
  「지금 있다」 **두 의미**를 겸하게 된다(원칙 4 · 함정표 「필드 하나로 두 의미」).
  게다가 그런 표시는 **적재기만** 쓸 수 있어서 손수 `DELETE` · 정리 도구 · DB 재생성은
  기록될 자리가 없다. 두 사실은 **묻는 시점에 대조**한다 —
  `pipeline_runner.loaded_record(run_id)`(그때의 기록, 읽기 전용)와 DB 조회(지금).
  `GET /api/v1/simulations/hearings` 의 404 `detail` 이 그 대조 결과다(아래).

#### `loaded` ↔ 지금 DB — `/hearings` 404 의 `detail` (2026-08-11 신설, 프런트 합의)

`GET /api/v1/simulations/hearings?run_id=…` 는 후보점이 0행이면 404 인데, `detail` 이
**문자열이 아니라 객체**다. 같은 404 라도 사유가 다섯 갈래이고, 접으면 화면이 없는
말을 한다(원칙 4).

```json
{"code": "LOADED_BUT_MISSING", "message": "…", "run_id": "r_20260810_006",
 "domain": null, "loaded": {"run_id": "…", "booth_candidates": 20},
 "current": {"booth_candidates": 0}}
```

| `code` | 판정 | 뜻 |
|---|---|---|
| `UNKNOWN_RUN` | `runs/<id>` 폴더가 없다 | run_id 가 틀렸거나 이 서버의 run 이 아니다 |
| `STATUS_UNREADABLE` | 폴더는 있는데 `status.json` 을 못 읽는다 | **「적재된 적 없다」가 아니다** — 확인하지 못한 것이다. 사유가 `message` 에 들어간다 |
| `LOADED_BUT_MISSING` | `loaded.booth_candidates > 0` 인데 DB 0행 | 적재 후 지워졌다(재적재 · 수동 삭제 · DB 재생성). 그 run 의 공청회·발화도 CASCADE 로 함께 사라졌다 |
| `NEVER_LOADED` | 기록도 없고 행도 없다 | 적재 칸이 없는 모드이거나 적재 전에 멈춘 run |
| `DOMAIN_MISMATCH` | run 에는 행이 있는데 그 `domain` 만 0행 | `current.booth_candidates_any_domain` 에 실제 행 수를 같이 준다 |

- 🔴 **`message` 는 전 갈래에서 항상 채운다.** 프런트는 모르는 `code` 를 만나면
  분기하지 않고 `message` 를 **그대로 띄운다** — 코드를 늘려도 프런트 배포를 안
  기다리는 대신, 비면 화면에 코드값만 뜬다.
- 🔴 `UNKNOWN_RUN` 과 `STATUS_UNREADABLE` 을 **같은 코드로 접지 않는다.**
  「없다」와 「물을 수 없다」는 다른 사실이다.
- 대조: `python app\tools\check_hearings.py` (다섯 갈래 전부. `STATUS_UNREADABLE` 은
  깨진 `status.json` 을 실제로 만들어 보고 지운다)

#### `cascaded` — 적재하면서 **지워진 것** (2026-08-11 신설)

`load_topn_candidates.py` 는 같은 `(domain, run_id)` 의 기존 후보점을 **지우고 다시
넣는다.** 그런데 `conflict_simulations.parcel_id` 가 `ON DELETE CASCADE` 이고
`debate_logs.simulation_id` 가 다시 그것을 따른다 → **그 run 에서 열렸던 공청회와
발화가 통째로 사라진다.** `verified_precedents` 는 `ON DELETE SET NULL` 이라 행은
남고 **연결만 끊긴다**(지워지지 않아 더 안 보인다).

| 필드 | 뜻 |
|---|---|
| `conflict_simulations` | CASCADE 로 지워진 공청회 건수 |
| `debate_logs` | 위를 따라 지워진 발화 행 수 |
| `verified_precedents_unlinked` | 연결이 끊긴(행은 남은) 판례 수 |

🔴 **이 세 필드는 「넣은 수」가 아니라 「지운 수」다. 실제로 반대로 읽힌 적이 있다**
(2026-08-11, 백엔드 회신). 「`loaded` 에 `conflict_simulations` 적재 건수가 기록된다」는
말이 나왔는데, 문장은 **글자 그대로 참**이고 뜻만 정반대였다 — 그대로 마이페이지에
띄웠으면 **토론 N건이 열린 run 이 「0건」으로** 보였을 것이다(실측값이 전부 0 이라
한동안 안 걸린다). 🔴 **run 별 토론 건수는 `status.json` 에 없다.** 토론은 run 이 끝난
뒤에 따로 치는 것이라 **run 수명 밖**이고, 토론 API 는 `status.json` 을 **읽기만 한다**
(`_write_status` 호출 0회). 건수를 내려면 조인뿐이다 —
`conflict_simulations.parcel_id → booth_candidates.id → .run_id`(§8-5-2).
⚠ 원인은 읽는 쪽이 아니라 **쓰는 쪽**에 있다: `loaded`(넣은 수) 안에 `cascaded`(지운 수)를
두면 이름만으로 뜻이 안 선다. **한 블록에는 한 방향만 담는다** — 늘릴 때 지킬 것.

- **러너 경로에서는 항상 0 이다.** `_new_run_id` 가 `runs/run_seq.json` 의
  **최고수위(high-water mark)** 를 쓰므로 run 폴더를 지워도 번호가 안 되돌아간다
  (2026-08-11 수정. 예전엔 폴더를 세어 매겼고, 그래서 폴더를 지우면 그 번호로 이미
  한 토론이 CASCADE 로 사라졌다). 0 이 아니면 **원장이 지워졌거나** 사람이 원장 밖에서
  같은 run_id 로 손수 적재한 것이다 — 둘 다 확인해야 할 사건이다.
- 값이 0 이어도 적재기가 `[CASCADED] …` 줄을 찍고 러너가 그대로 옮긴다.
  **키가 없는 것은 "0건"이 아니라 "그 적재기가 세지 않았다"** 다(원칙 4).
- `dry-run`(`--yes` 없이)도 같은 줄을 낸다. 계획 출력이 **지울 것**을 안 말하면
  계획이 아니다.
- `audit_rules` 에는 매달린 테이블이 없다 → `load_audit_data.py` 는 이 줄을 안 찍는다.

**🔴 2026-08-11 추가 — 기록에서 그치지 않고 막는다(프런트 요청).**
공청회가 하나라도 딸려 나가면 `load_topn_candidates.py` 는 **`--force` 없이는 멈춘다**
(rc=1, DELETE 전에 정지). 후보점 행은 `topN.geojson` 에서 다시 만들어지지만 LLM 토론은
아니다 — 발화는 Redis TTL 600초뿐이라 재구성이 안 된다.

- `--yes` 와 `--force` 는 **다른 손짓**이다. `--yes` = 「쓰겠다」, `--force` =
  「**남의 공청회 결과를 지우면서까지** 쓰겠다」. 합치면 평소 적재와 파괴적 적재가
  구분되지 않는다.
- **`full` 모드의 정상 경로는 안 막힌다.** 새 run_id 에는 매달린 공청회가 없다.
  걸리는 건 **run_id 재사용**뿐이고, 그때는 실제로 남의 결과를 밟는 것이 맞다.
  러너는 `--force` 를 **넘기지 않는다** — 넘기면 이 방어가 러너 경로에서만 사라진다.
  그 경우 `적재-후보` 칸이 `failed` 로 서고 사유는 `run.log` 에 남는다.
- `dry-run` 은 필요한 손짓을 알려준다: `적재하려면 --yes --force 를 붙일 것.`

### 3-2. `pruned` — 이 run 에서 **지워진** 산출물 (2026-08-11 신설)

`runs/` 는 서버 디스크다. 그대로 두면 run 하나당 37 MB 씩 무한히 쌓인다
(2026-08-11 실측: 8 run · 309.2 MB). 그래서 **최근 5개를 뺀 나머지의 `.gpkg`·`.parquet`
만** 지운다. 사람 결정(2026-08-11) — 부팅 시 자동, `OMNISITE_RUNS_KEEP` 로 조정.

```json
"pruned": {
  "at": "2026-08-11T09:26:19",
  "policy": "keep_recent:5",
  "removed": [{"path": "step3/흡연_후보_지적도필지.gpkg", "bytes": 25197...}]
}
```

- **키가 없으면 지운 적이 없다.** `null` 이 아니라 아예 없다 — 옛 run 과 구분된다.
- **폴더를 통째로 지우지 않는다.** `status.json`·`run.log`·`topN`·`report`·`clean_report`
  는 남으므로 「그 run 이 무슨 값을 냈나」는 그대로 읽힌다. 폴더째 지우면 폴링이
  404 가 되고 이 계약 3절이 옛 run 에 대해 거짓이 된다. 실측상 지우는 두 확장자가
  용량의 **96.8%** 이고 남는 전부가 3.2%(9.9 MB)다.
- **지우면 `artifacts` 의 해당 키가 `null` 로 되돌아간다.** 안 되돌리면 status 는 URL 을
  주는데 엔드포인트는 404 다 — status 가 거짓말을 한다(원칙 4).
  실측: `candidates` 만 `null`, 나머지 7키는 URL 유지.
- **안 지우는 조건 둘** — 하나라도 걸리면 남긴다.
  ① 최근 N개(기본 100) ② 상태가 `queued`·`running`·`awaiting_hitl`.
  🔴 예전엔 셋이었다 — ③ `booth_candidates.run_id` 가 참조 중이면 남긴다.
  **2026-08-11 에 없앴다**(사람 결정). 근거(살아 있는 후보점의 출처가 그 폴더다)는
  지금도 참이지만 **이 조건엔 상한이 없다**: 세 모드가 다 적재하게 되자 「적재한 run
  은 전부 참조됨」이 되어 정리기가 사실상 꺼졌고, 반대로 **적재도 토론도 안 한 버려진
  run**(가치가 가장 낮다)이 ③ 에 안 걸려 **먼저** 지워졌다 — 실패 방향이 뒤집힌다.
  상한이 없는 보호는 보호가 아니라 **정지**다. 지금 조절 지점은 `keep` 하나뿐이고
  디스크는 `39MB × keep` 으로 묶인다.
  ⚠ 그래서 **DB 에 행이 있어도 폴더는 안 지켜진다** — 정리기는 DB 를 아예 안 본다.
  이미 한 토론의 근거는 폴더가 아니라 `result_json.basis` 에 **본문으로** 박혀 있고
  (`basis_snapshot`), 아직 안 한 토론의 POI 는 `poi_context` 가 「정리됐거나 만들어지지
  않았다」를 `skipped` 로 남긴다.
- 계획은 `keep` 항목에도 **이유**를 붙인다. 「지울 것」만 말하는 계획은 왜 안 지웠는지를
  사람이 다시 캐게 만든다.
- 부팅 훅은 **`reap_orphans()` 뒤**에 돈다. 먼저 돌면 이전 서버가 죽여놓고 간 run 이
  아직 `running` 이라 영원히 보호된다.
- 도구: `python app\tools\prune_runs.py [--keep N] [--yes]`(계획만 출력이 기본) ·
  대조 `python app\tools\check_prune_runs.py`(40항목, 진짜 `runs/` 를 안 쓴다).

### 3-3. `user_id` · `run_record_errors` — run 메타데이터를 DB 에도 적는다 (2026-08-11 신설)

`status.json` 은 **한 run 의 진행 상태**만 안다. 「내가 돌린 run 을 최신순으로」(마이페이지)
는 폴더 N개를 전부 열어야 답할 수 있는 질문이라 파일로는 못 푼다 → run 메타데이터를
Postgres **`run_records`** 에 **사본으로** 적는다(4계층 중 ③).

```json
"user_id": null,
"run_record_errors": [
  {"at": "start", "time": "2026-08-11T23:41:34", "reason": "OperationalError: 연결 실패"}
]
```

- 🔴 **정본은 여전히 `status.json` 이다.** 그래서 DB 컬럼명이 `status` 가 아니라
  **`last_known_status`** 이고 값은 셋뿐이다 — `queued`·`succeeded`·`failed`.
  `running`·`awaiting_hitl` 은 **일부러 안 넣는다**: 진행률을 DB 에 물으면 정본이 둘이
  되고 그 둘은 언젠가 갈린다. **지금 어디까지 갔는지는 `GET /runs/{id}` 로 본다.**
- **행은 발급 시점에 만든다**(`queued`). 「DB 는 끝난 사실만」을 문자대로 읽으면 돌다
  죽은 run 은 행이 아예 안 생겨 `reap_orphans` 가 갱신할 대상이 없다(원칙 4).
  「끝난 사실만」은 *행을 언제 만드나*가 아니라 *무엇을 DB 에 묻지 않나*로 읽는다.
- **`user_id` 는 영구 nullable 이다.** 「아직 로그인 배선 전」이라서가 아니라
  **익명 실행이 정상 상태**이기 때문이다(사람 결정). 옛 run 은 키가 없어 `read_status`
  가 `null` 로 채우는데, 그 `null` 은 「주인 없음」과 「그 시절엔 안 적었다」를 **둘 다**
  포함한다.
- ✅ **채우는 쪽이 2026-08-12 에 생겼다**(선택적 인증, 1절 `POST /runs`).
  그전까지는 컬럼도 FK 도 인덱스도 있는데 **넘기는 곳이 없어** 전부 익명이었다
  (실측 5/5 `NULL`) — 배관이 다 뚫려 있고 마지막 한 칸만 비어 있는 상태라 코드만
  읽으면 「되고 있다」로 보인다. 값은 `POST /runs` → `start_run(user_id=)` →
  `_new_status` → `record_run_start(doc)` 로 흐르고, 중간에 손으로 옮겨 적는 자리가
  없다(`doc` 하나를 넘긴다).
  대조: `python app\tools\check_optional_auth.py` **27항목**(실 DB·실 Redis, LLM 0회).
  🔴 거기서 제일 중요한 항목은 성공이 아니라 **「401 일 때 `start_run` 이 아예 안
  불렸다」**다 — 인증에 실패했는데 run 이 시작되면 그게 최악이다.
- ⚠ **옛 행은 소급해서 안 채운다.** 지금 `NULL` 인 5행은 진짜로 주인이 없다.
- 🔴 **이 기록이 실패해도 run 은 그대로 돈다.** 다만 「catch 한다」와 「조용히 삼킨다」는
  다르다(원칙 1·4) — 실패하면 `run_record_errors` 에 `{at, time, reason}` 을 쌓고
  `warning` 로그를 남긴다. `at` 은 `start`·`end`·`reap` 셋 중 하나다.
  **성공이면 키 자체가 없다** — 항상 두고 `[]` 를 넣으면 옛 run 까지 「시도했고 다
  성공」으로 읽힌다.
- 🔴 **`loaded.cascaded` 는 DB 로 안 옮긴다.** 그건 「넣은 수」가 아니라 재적재로
  **지워진 수**다(뜻이 정반대). 같은 지붕 아래 두 방향을 담으면 읽는 쪽이 반드시 한
  번은 틀린다. 옮기는 건 `loaded.audit_rules`·`loaded.booth_candidates` 둘뿐이다.
- 🔴 **여기 행이 있다고 `runs/<run_id>/` 폴더가 지켜지지 않는다**(3-2). 마이페이지는
  `RUN_FOLDER_GONE`·`ARTIFACT_PRUNED` 갈래를 반드시 갖는다.
- 구현: 모델 `app/db/models/run_record.py` · 기록 `app/services/run_records.py`
  (`record_run_start` · `record_run_end` · `record_runs_end`) · 대조
  `python app\tools\check_run_records.py`(64항목, 넣은 행을 지우고 **지워졌는지까지** 본다).
  ⚠ 러너는 `threading.Thread` 위의 **동기 코드**라 앱의 async 엔진을 못 쓴다
  (`asyncio.run` 을 쓰면 전역 풀이 닫힌 루프에 커넥션을 물고 있어 이후 요청이
  `Event loop is closed` 로 죽는다) → **psycopg 동기 + `connect_timeout`**.
- ⚠ **마이페이지 API 는 아직 없다.** 지금 있는 건 「적는 쪽」뿐이고, 읽는 엔드포인트가
  생기면 이 절에 추가한다. **담당은 천명님**(2026-08-12 사람 결정).
- 🔴 **그런데 프런트에는 그 화면이 이미 있고, 이미 부르고 있다**(2026-08-12 실측).
  `BigProject_Front/src/app/mypage/page.tsx` → `fetchRuns(true)` →
  **`GET /api/v1/pipeline/runs?mine=true`**.
  🔴 **여기 「404」라고 적었던 건 틀렸다 — 실제로는 `405 Method Not Allowed` 다**
  (2026-08-12 프런트 실측 · 우리도 재확인). `POST /runs` 가 **같은 경로**를 이미
  점유하고 있어서 라우터는 경로를 찾고 **메서드에서 막는다.** 「경로가 없다」로 적으면
  다음 사람이 **없는 것을 새로 만드는 문제**로 읽는데, 실제로는 **있는 경로에 메서드를
  더하는 문제**다. 나는 라우트 목록만 보고 404 라고 단정했다(원칙 5 — 안 쳐봤다).
  붙일 자리는 천명님 저장소가 아니라 **우리 라우터 `app/api/v1/pipeline.py`** 다.
  ✅ 프런트는 삼키던 `catch` 를 없앴다(2026-08-12) — 지금은 화면에
  「HTTP 405 — Method Not Allowed」가 그대로 뜬다. **실패 분기를 빈 목록 분기보다
  앞에** 뒀다: 순서가 반대면 「아직 실행한 분석 내역이 없습니다」가 떠서 사용자가 자기
  기록이 지워진 줄 안다 — 실패했을 때의 `[]` 는 **「없다」가 아니라 「모른다」**다.
  프런트가 이미 굳혀둔 응답 모양 —

  ```ts
  { runs: [{ run_id, domain, mode, status, started_at, finished_at, is_mine }] }
  ```

  · `status` 는 **`last_known_status`** 다(`running`·`awaiting_hitl` 이 없다는 뜻).
    진행 중인 run 을 목록에서 「멈춘 것」으로 그리지 않으려면 화면이 `queued` 를
    「진행 중이거나 죽었음」으로 읽어야 한다 — 정확한 현황은 `GET /runs/{id}` 다.
  · `is_mine` 은 **DB 에 없는 파생값**이다(`user_id == 나`). 익명 행은 `false` 로 온다.
  · 🔴 `mine=true` 인데 익명 행까지 돌려주는 셈이라 **이름과 내용이 어긋난다.**
    프런트는 한 번 불러 `is_mine` 으로 두 그룹으로 가른다.
    ✅ **프런트 회신으로 확정됐다**(2026-08-12) — 거르는 건 서버가 아니라 프런트다.
    `is_mine` 필드가 응답에 있는 것 자체가 「섞어서 주고 프런트가 가른다」는 뜻이다.
    🔴 `is_mine: true` 만 돌려주면 「로그인 없이 실행된 분석 내역」 구획이 **영원히
    비는데 에러가 안 난다** — 로그인 전에 돌린 run 을 볼 방법이 사라진다.
    만드는 사람은 **이름(`mine`)이 아니라 이 문장**을 따를 것.
  · 🔴 **`GET /runs` 를 붙이는 순간 `POST /runs` 와 경로가 같아진다** — 인증 규약이
    메서드별로 갈린다(POST 선택 · GET 은 필수여야 「내 것」이 뜻을 갖는다).
- 🔴 **프런트 화면 문구가 계약과 어긋나 있다**(2026-08-12 실측, 우리가 못 고치는 자리).
  `mypage/page.tsx:177` 이 익명 run 을 **「주인 미상(이관 전) 분석 내역」**이라고 쓴다.
  「이관 전」은 **언젠가 주인이 생긴다**는 말인데, 익명 실행은 미구현이 아니라 **의도된
  정상 상태**이고 소급 귀속 경로는 **없다**(1절 · 4계층 문서 ㉠ — 「이관 전이라 쓰면
  안 된다」가 거기 명시돼 있다). 화면이 없는 미래를 약속하고 있다(원칙 4).
  「주인 없음(로그인 없이 실행)」 정도가 사실이다. **프런트에 전달할 것.**

---

## 4. 양쪽이 함께 지킬 것

- **실패한 run 도 `status.json` 을 남긴다.** `status: "failed"` + `error` 채움.
  프런트는 `failed` 를 정상 상태 중 하나로 다룬다. 예외로 던지지 않는다.
- **폴링은 `succeeded` · `failed` · `awaiting_hitl` 이 되면 멈춘다.** 간격 1~2초.
  `awaiting_hitl` 은 답을 주기 전까지 **영원히 안 바뀐다** — 계속 돌면 무한 폴링이다.
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
- 🔴 **`data_임시/<도메인>/fixture/profiles.json` 은 `fixture` 모드의 첫 칸(STEP2)이
  요구하는 입력이다.** `.gitignore` 대상이라 clone 에 안 들어오고, 이름을 바꾸면
  `fixture` 모드가 **10초 만에 `FileNotFoundError`** 로 죽는다(2026-08-10 `r_20260810_003`
  실측 — 누군가 `fix_profiles.json` 으로 바꿔놓았다. 두 파일은 sha256 이 같았다).
  `gam2_audit_judgment_test.build_fixtures()` 는 없으면 만들어 주지만 `gam2_clean_data.py`
  는 안 만든다 — **STEP1 을 안 도는 fixture 모드에서만 드러난다.**
  없으면 `python app\services\gam2_profile.py data_임시\<도메인>` 로 다시 만든다.
- 작업 후 `python app\tools\check_fixture.py 흡연` 이 **57/57** 이어야 한다.
  (스크립트는 저장소 루트가 아니라 `app/tools/` 에 있다. 2026-08-05 에 `검증용/` 에서
  옮겼다 — 그 폴더가 `.gitignore` 라 clone 에는 기준값만 있고 대조기가 없었다.)
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
| ~~STEP0·1 실행~~ | ✅ **`mode: "full"` 로 완주 실측 (2026-08-10)** — 8절·8-8 |
| ~~HITL 게이트 (`mode: "hitl"`)~~ | ✅ **구현·실측 완료 (2026-08-05)** — 7절 |
| ~~`audit_rules` 재실행 누적~~ | ✅ **B안으로 해소 (2026-08-10, 사람 승인)** — 아래 |

**`audit_rules` 누적 — 적재 단위와 조회 단위가 어긋나 있었다**(8-5). 같은 도메인을 두 번
돌리면 근거가 두 배가 되고 AHP 가중치가 묽어졌다. 안 터지고 값만 틀린다.

| 안 | 내용 | 대가 |
|---|---|---|
| A | 적재기가 `domain` 단위로 **전량 교체** (run_id 는 출처 기록으로만) | 한 도메인의 과거 감리 이력이 DB 에 안 남는다 |
| **B ✅** | 조회에 `run_id` 조건 추가 (`booth_candidates` 행에서 가져옴) | 정본 `run_id` 어휘를 두 테이블에서 통일해야 한다 |
| C | 조회가 도메인별 **최신 run_id** 하나만 사용 | 사람이 화면4 에서 **옛 run 의 후보**를 고르면 근거만 최신이 된다 |

🔴 **처음엔 A 를 권고했다가 철회했다.** `audit_rules` 는 STEP1 **산출물**이고, 실측하면
두 적재기(`load_audit_data.py`·`load_topn_candidates.py`)가 **이미 `(domain, run_id)` 단위로
교체**하고 있었다 — 어긋난 건 저장이 아니라 **조회 하나**였다. A 는 맞는 쪽(저장)을
틀린 쪽(조회)에 맞추는 안이었다.

B 의 대가로 적었던 "정본 도메인이 깨진다" 는 **run_id 어휘를 통일해서 없앴다.** 예전엔
각 적재기가 자기 STEP 폴더 이름(`step1_output` / `step4_output`)을 넣어 갈려 있었다.
run_id 는 "어느 STEP 폴더에서 왔나" 가 아니라 **"어느 실행에서 나왔나"** 다 →
격리 run 은 `r_YYYYMMDD_NNN`, 정본은 두 테이블 모두 **`"정본"`**(기존 33행 마이그레이션 완료).

조회는 `_select_audit_rules(db, facility_type, domain, run_id)` 이고 `run_id` 는
**요청으로 받지 않는다** — `domain` 과 **같은 `booth_candidates` 행**에서 나온다.
같은 행에서 뽑으면 둘이 어긋날 수가 없고, 파라미터로 받으면 A run 의 후보점에
B run 의 감리 근거를 넘길 수 있다. 0건이면 조용히 넓히지 않고 `raise` 한다(원칙 1).

실측: 흡연업로드에 **26행**(두 run)이 있는데 후보점 62·84·2 각각 **13행 · 고유 요인명 8** 로
풀린다. 규칙이 없는 run 은 **0.56초**에 멈춘다(고치기 전엔 남의 run 근거로 39.5초 완주했다).

**되돌린 결정 (지우지 않고 이유를 남긴다)**

- `--auto-radius` 를 쓰려다 **뺐다.** 쓰면 `run_weight_model.py` 가
  `radius_conf["_confirmed"] = True` 를 안 찍는다(같은 파일 283행). 픽스처는 `--radius` 로
  반경을 고정한 실행이라, `--auto-radius` 로 돌리면 조건이 달라진다.
- `--no-diag --bootstrap 0` 도 **뺐다.** 픽스처가 기록한 `조건` 에 없다.
  진단이 가중치를 안 바꾼다고 "알고는" 있지만, 안 재본 것을 같다고 단정하지 않는다.
- 러너에 `--facility` / `--region` 을 넘기려다 **뺐다.** 파이프라인이 `reviewed.json` 에서
  스스로 읽으므로 값은 같지만, 넘기는 순간 도메인 값이 러너에 박힌다(하드코딩 금지).

---

## 7. HITL 게이트 — `mode: "hitl"`

작성 2026-08-05 · 사람 승인 완료 · **구현·실측 완료 2026-08-05.**
흡연 픽스처로 게이트 두 개를 지나 완주했고, 같은 답을 주면 `fixture` 모드와
**값이 전 항목 일치**한다(7-8).

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
STEP0/1 감리 ─▶ ⏸ 게이트A ─▶ STEP2 ─▶ STEP3-1 ─▶ (제안패스) ─▶ ⏸ 게이트B ─▶ STEP3-2 ─▶ STEP4 ─▶ 끝
                배제반경                                            [R] 집계반경
                데이터의도                                          [W] 가중치(-1~+1)
                지역코드
```

**재실행은 0회다.** 멈췄다가 이어간다.

구현상 실행 계획은 배열 하나다(`pipeline_runner._PLAN`). `hitl` 은 `fixture` 에
게이트 두 칸과 제안 패스를 끼워 넣은 것이고, **단계 커맨드는 두 모드가 같은 함수를 탄다.**

```
fixture : 2 · 3-1 · 3-2 · 4 · load-audit · load
hitl    : ⏸audit · 2 · 3-1 · propose · ⏸weight · 3-2 · 4 · load-audit · load
```

🔴 **적재 칸은 이제 세 모드에 다 있다**(2026-08-11). 계획 배열만 바꿨고 실행부(`_execute`)는
`load-audit`/`load` 를 이미 일반 경로로 넘기고 있어 **디스패치는 한 줄도 안 고쳤다**(2절 끝 참조).
적재 칸은 **꼬리에** 붙인다 — 게이트 앞에 끼우면 `_resume_index`(게이트 답변 후 재개 위치)가
조용히 밀린다. 지금 값은 `hitl` 에서 audit=1 · weight=5 로 붙이기 전과 같다.

게이트를 만나면 **실행 스레드가 그냥 끝난다.** 진행 상태는 전부 디스크에 있으므로
서버가 재시작돼도 답변 POST 로 이어갈 수 있다. 이어갈 위치는 `gate.id` 로 유도한다 —
"어디까지 했나"를 status.json 에 따로 적지 않는다. 같은 사실을 두 곳에 적으면 갈린다.

### 7-1b. 제안 패스 — 왜 한 번 더 도나

게이트B 화면에 올릴 **제안값**([R] 집계반경 · [W] 슬라이더 초기값)은 실행해 봐야 나온다.
`run_weight_model.py` 에 dry-run 이 없었고, 기존 `--radius`/`--weight` 는 **덮어쓰기**지
읽어오기가 아니다. API 프로세스에서 `define_indicators`/`suggest_radius` 를 다시 짜면
**CLI 와 API 가 갈린다** — 이 저장소가 반복해서 당한 유형이다.

그래서 `--propose-only` 를 **정본 CLI 에 추가**했다. `[A] 지표정의 → [A2] 레이어부착 →
[R] 반경제안 → 슬라이더 초기값 → 제안 파일 저장 → 종료`. 후보 로드·`[B]` 행렬·CRITIC·
합성·저장은 **하지 않는다**. 흡연 실측 **9.6초**(그중 LLM mini 1회).

제안 파일은 `weight_set` 과 같은 디렉터리에 `<도메인>_weight_proposal_<run_id>.json`
으로 떨어진다. **산출물 화이트리스트에는 넣지 않았다** — 게이트가 값을 인라인으로
싣고 나가므로 프런트가 파일을 따로 받을 이유가 없다.

🔴 `--propose-only` 를 넣으면서 `suggest_radius` 앞에 가드도 같이 넣었다:
`--radius` 가 비-admin 지표를 **전부** 덮는 실행에서는 LLM 제안을 부르지 않는다.
부르면 만들자마자 덮어써 순수 낭비이고, **쓰지도 않은 제안의 rationale 이 산출물에
남아 "이 근거로 정했다"고 주장하게 된다**(원칙 4). 픽스처 모드가 여기 해당한다 —
반경 **값**은 안 바뀌고 `radius_rationale`/`source` 문구만 바뀐다(`source: "none"` →
`--radius` 가 `"cli_fixed"` 로 덮음).

### 7-2. `mode` 두 값의 차이

| `mode` | 게이트 | 용도 |
|---|---|---|
| `fixture` | **없음** — 무입력 완주 | 회귀 검증 + **시연 프리셋**(업로드·게이트를 건너뛰고 화면5까지). 실측 **95초 · 8칸** (`r_20260811_002` · `loaded {audit_rules:13, booth_candidates:20}`) |
| `hitl` | **있음** — 게이트A·B 에서 멈춤 | 실제 사용(픽스처 감리 결과로 시작). 완주하면 **8칸**(2026-08-11 부터 적재 2칸 포함). 실측 `r_20260811_004` · `loaded {audit_rules:13, booth_candidates:20}` · fixture 와 **값 10항목 전 일치**(`check_hitl_e2e.py`) |
| `full` | **있음** — `hitl` 과 같은 게이트 2개 | 업로드한 도메인을 STEP0 부터 (8절) |

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
    "label": "감리 확인 — 배제반경 · 데이터 용도 · 지역 코드",
    "questions": [ ... ]      // 7-4 · 7-5. **평평한 배열**이고 각 항목에 `kind` 가 있다
  }
}
```

- **폴링 종료 조건이 바뀐다.** 지금까지는 `succeeded`/`failed` 였다.
  이제 `awaiting_hitl` 도 멈춰야 한다 — 답을 주기 전까지 영원히 안 바뀐다.
- `gate` 는 `awaiting_hitl` 일 때만 있다. 그 외에는 **키 자체가 없다**(`null` 아님).
- 답을 받으면 즉시 `running` 으로 돌아가고 `gate` 는 사라진다.
- `finished_at` 은 `awaiting_hitl` 동안 `null` 이다. **멈춘 것은 끝난 것이 아니다.**
- 게이트 대기 중에도 `run.log` 는 읽힌다. 답을 받아 이어갈 때 로그는 **덮어쓰지 않고
  이어 붙인다** — 게이트 화면에서 보던 로그가 답변 순간 증발하면 안 된다.

### 7-3b. 답변 엔드포인트 — `POST /runs/{run_id}/hitl/{gate_id}`

`gate_id` 는 `audit` | `weight`. 응답은 **답변 직후의 `status.json`**(status 는 이미
`running`, `gate` 키는 사라진 상태)이다. 프런트는 이걸 받고 폴링을 재개하면 된다.

| 코드 | 언제 |
|---|---|
| 200 | 접수됨. 실행이 이어진다 |
| 400 | 검증 실패 — `detail` 에 한국어 사유 |
| 404 | 없는 `run_id` |
| 409 | 게이트 대기 중 다른 run 이 같은 도메인을 점유함 |

400 이 나는 경우(전부 `detail` 로 이유가 나간다):

- `awaiting_hitl` 이 아닌 run 에 답을 보냄 / 지금 기다리는 게이트와 다른 `gate_id`
- 본문 `run_id` 가 경로와 다름 — **조용히 경로를 쓰지 않는다.** 프런트가 다른 run 을
  보고 있다는 뜻이고, 그대로 진행하면 남의 run 에 답을 적용한다
- `gate.questions` 에 없는 대상(`dataset_id`·`role_index`·`op_index`·`indicator_id`)
- `editable: false` 인 항목을 수정하려 함 (7-4)
- 값 범위 위반 · 알 수 없는 필드명

### 7-4. 게이트A — `POST /runs/{run_id}/hitl/audit`

`review_hitl`(`gam2_audit_judgment_test.py:1534`)이 처리하는 flag 3종 그대로다.

**질문** — `gate.questions[]` (`kind` 로 구분)

```json
{"kind": "exclusion",   "dataset_id": "01", "role_index": 0, "editable": false,
 "summary": "…", "facility_type": "금연구역", "exclusion_type": "polygon",
 "rationale": "…", "radius_m": 10, "radius_source": "human_confirmed",
 "proposed_m": 10, "proposal_source": "…", "evidence": "…",
 "evidence_matches_facility": true}

{"kind": "intent",      "dataset_id": "12", "editable": true,
 "summary": "…", "message": "…", "current_roles": ["…"],
 "choices": [{"value": 1, "label": "가점(수요)", "needs_weight": true}, …]}

{"kind": "code_prefix", "dataset_id": "04", "op_index": 3, "editable": false,
 "summary": "…", "col": "행정동코드", "prefix": "11170", "region": "서울특별시 용산구",
 "verdict": "ambiguous", "reason": "…", "detail": "…", "suggestion": "11170",
 "confirmed_by": "code_table:행자부", "recheck_skipped": false}
```

🔴 **`editable: false` 인 항목도 목록에 나온다. 보여주되 수정은 안 된다.**
(사람 결정 2026-08-05) HITL 전에 `confirmed` 가 되는 건 조례·코드표에서 근거를
확실히 찾았을 때뿐이라 고칠 이유가 없다. 그렇다고 감추면 사람은 **무엇이 이미
정해졌는지 모른 채** 남은 것만 답하게 된다 — 화면이 사실의 일부만 보여주는 것이다.
읽기 전용 항목을 수정하려 하면 **400** 이다. 조용히 무시하지 않는다.

> 흡연 픽스처 실측: 질문 **4건**(배제 3 · 의도 0 · 지역코드 1), **전부 읽기 전용**이다.
> 픽스처 `reviewed.json` 이 이미 전부 확정된 상태이기 때문이다.
> 즉 픽스처로 `hitl` 을 돌리면 게이트A 는 **확인 화면**이고 빈 답 `{}` 로 통과한다.

`proposed_m`(AI 제안)과 `radius_m`(현재 확정값)을 **한 필드로 합치지 않았다.**
합치면 "제안인지 확정인지"가 화면에서 사라진다. `evidence_matches_facility: false` 는
근거문장에 그 시설명이 없다는 뜻 — **다른 시설 규정일 수 있다.** 경고로 띄울 것.

`recheck_skipped: true` 는 감리 때 코드표 대조를 못 했고(`prefix_check` 없음 또는
`verdict: "unknown"`) **API 가 다시 판정하지도 않았다**는 뜻이다. 재판정에 쓰는
`_code_samples` 가 `build_fixtures()` 를 부르고 모듈 전역에 캐시하는데, 오래 사는
API 프로세스가 할 일이 아니다. 못 한 건 못 했다고 내보낸다(원칙 4·5).

**답변**

```json
// 요청
{
  "run_id": "r_20260805_004",
  "exclusions": [
    {"dataset_id": "01", "role_index": 0, "radius_m": 10}
  ],
  "intents": [
    {"dataset_id": "12", "choice": 1, "weight": 0.6}
  ],
  "code_prefixes": [
    {"dataset_id": "04", "op_index": 3, "prefix": "11170"}
  ]
}
```

세 배열 모두 **선택**이다. 고칠 게 없으면 `{}` 로 보낸다.

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

**게이트는 하나다.** `[R]` 집계반경과 `[W]` 가중치를 나누지 않는다 —
`slider_from_indicators()` 는 `define_indicators` 의 `seed_weight`·`direction` 에만
의존하므로 반경이 정해지기 전에 이미 계산된다. 둘을 한 화면에 올릴 수 있다.
(사람 결정 2026-08-05)

#### 질문 (`status.gate.questions[]`)

지표 하나가 항목 하나다. 값은 전부 **제안 패스**(7-1b)가 만든 것이다.

```json
{
  "kind": "weight",
  "indicator_id": "07+02",
  "indicator_kind": "point_sum",
  "radius_required": true,
  "direction": "benefit",
  "seed_weight": 0.75,
  "components": {"geo": "07", "val": "02"},
  "rationale": "버스정류장은 유동인구가 많은 장소로 …",
  "data_note": "314건 × 값",
  "radius_proposed": 300,
  "radius_rationale": "버스정류장은 유동인구가 많아 …",
  "radius_source": "llm",
  "slider_proposed": 0.75,
  "conflict": null
}
```

| 필드 | 뜻 |
|---|---|
| `indicator_id` | 답변의 키. `07+02` 처럼 결합 지표는 `geo+val` |
| `indicator_kind` | `point_sum` · `point_count` · `admin` |
| `radius_required` | `indicator_kind != "admin"`. **`true` 면 반경 입력이 필수**, `false` 면 **보내면 400** |
| `radius_proposed` | LLM 제안 반경(m). `admin` 은 `null` |
| `radius_source` | `llm` · `human` · `none`. 사람이 이미 정한 게 있으면 LLM 을 안 부른다 |
| `slider_proposed` | `-1 ~ +1`. `direction` 이 `cost` 면 음수다 |
| `conflict` | `null` 이거나 `{geo_dataset, geo_direction, val_dataset, val_direction}` |

`conflict` 는 결합 지표에서 **geo 쪽과 val 쪽 방향이 갈릴 때**만 실린다
(`define_indicators:300-303`). `seed_weight` 는 둘을 평균하는데 `direction` 은 val 쪽만
쓰므로 geo 판정이 조용히 사라진다 — 어느 쪽이 옳은지는 도메인마다 다르므로
**규칙으로 정하지 않고 사람에게 넘긴다**(원칙 3).

#### 답변

```json
{
  "run_id": "r_20260805_004",
  "radius": {"07+02": 150, "06+03": 300, "08": 50, "09": 150, "10": 250},
  "slider": {"07+02": 0.75, "06+03": 0.8, "04": 0.7, "08": 0.3, "09": 0.7, "10": 0.7}
}
```

- 필드는 **이 셋뿐**이다. 다른 키가 있으면 400. `resolved_conflicts` 같은 필드는 **없다** —
  충돌은 **슬라이더 부호로 확정**한다. 같은 사실을 두 곳에 적으면 갈린다.
- `radius` 는 **정수 m**, 범위 `1~5000`. `radius_required: true` 인 지표가 하나라도
  빠지면 400 이고, `admin` 지표에 반경을 보내도 400 이다.
- `slider` 는 생략 가능하다 — **생략하면 `slider_proposed` 를 그대로 쓴다**(안 고친 것).
  단 `conflict` 가 있는 지표는 **반드시 `slider` 에 있어야 한다.** 없으면 400.
- 백엔드는 답변을 `runs/<run_id>/hitl/weight_answer.json` 에 남기고,
  이어지는 `3-2` 단계에 `--radius "07+02=150,…"` · `--weight "07+02=0.75,…"` 로 넘긴다.
  **`run_weight_model.py` 의 기존 인자다. 픽스처 모드가 쓰는 바로 그 경로**이고
  파이프라인 정본은 이 때문에 고치지 않았다.

🔴 **`slider` 는 `-1 ~ +1` 을 그대로 보낸다. 프런트가 분해하지 않는다.**
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
  백엔드는 이 검사를 **접수 시점에** 한 번 더 한다(생략분은 `slider_proposed` 로 채운 뒤
  합산). 파이프라인까지 가서 터지면 run 이 `failed` 로 죽지만, 여기서 막으면 400 이고
  게이트는 그대로 열려 있다 — 사람이 다시 답하면 된다.
- 없는 지표 id 는 400. `ValueError`(`:1106`)까지 안 간다.

> 🔴 `run_weight_model.py:314` 의 `ValueError`(반경이 빠진 non-admin 지표)는 **결함이
> 아니다.** 조용히 기본값을 넣지 않는다는 뜻이므로 그대로 둔다. 대신 백엔드가
> **접수 시점에 같은 검사**를 해서 400 으로 되돌린다 — 사람이 고칠 수 있는 자리에서
> 막는 게 맞다. (사람 결정 2026-08-05)

### 7-6. STEP4 는 HITL 을 넣지 않는다 (1차)

`gam4_site_select.py` 의 `input()` 은 **0개**이고 `CLAUDE.md` 설계도
"HITL 위치: STEP1 · STEP3" 이다. 일치한다.

🔴 **결과 하나를 명시해 둔다.** S9 배제판정 `배제판정_확인요청` gap(흡연 4건)은
**STEP4 에서 계산된다.** `load_exclusions`(`:749`)가 STEP2 정제 결과를 필요로 해서
게이트A 시점에는 아직 존재하지 않는다 — **앞으로 당길 수 없다.**

→ 화면 2b 의 S9 판정 표는 **읽기 전용**이다. 뒤집으려면 새 run 을 시작해야 한다.
버튼을 붙이지 않거나, 붙인다면 "다음 실행에 반영"임을 화면에 적어야 한다.

### 7-7. ✅ 해소 — 배제반경 캐시 제거 · 배제는 전부 사람이 본다 (2026-08-10)

**있던 문제.** `apply_radius_answer` 가 run 폴더 **밖**(`data_임시/search_cache/
<prefix>_exclusion_radius_cache.json`, 키 = `facility_type`)에 확정값을 적었고,
`enrich_hitl_flags` 가 다음 실행에서 그 값을 **묻지 않고 채웠다**(`from_cache`).
같은 함수에 두 번째 자동 확정도 있었다 — 조례 텍스트에 시설유형과 반경 숫자가
**둘 다 substring 으로 있으면** `confirmed=True`. 둘 다 flag 를 안 만들어서
**게이트A 화면에 아예 안 뜬다.**

**왜 고쳤나.** 그건 substring 대조다 — 「제5조의 10m 가 이 시설 얘기인지」까지는
모른다. 근거는 되지만 확정은 아니다. 자동으로 끝까지 가야 할 때는 `mode:"fixture"`
가 따로 있으므로, HITL 은 **"사람이 전부 본다"** 가 뜻의 전부여야 한다. (사람 지시)

**바뀐 것.**

| 항목 | 전 | 후 |
|---|---|---|
| 캐시 | `load/save_to_exclusion_cache` · `EXCLUSION_CACHE_PATH` · `_DOMAIN["cache_path"]` | **삭제**(코드·`config.py`·기존 json 3개) |
| 조례 대조 | `confirmed=True` 로 확정 | flag 의 **`제안값`·`출처`·`근거_시설_일치`** 로 강등 |
| `hard_exclusion` role | 일부만 flag | **전부** `confirmed=False` + `exclusion_radius_missing` flag |
| `mode:"hitl"` 입력 | 픽스처의 옛 `confirmed:true` 를 그대로 → `editable:false` | `_prepare_dirs` 가 **run 안의 사본만** `reset_exclusion_confirmations()` 로 되돌림(값은 `제안값` 으로 보존) |
| 미확정인 채 STEP2 | 완주하고 `report.json` gap 에만 남음 | **`SystemExit`**(`assert_exclusions_confirmed`, `gam2_clean_data.clean_domain` 진입부) |

- 확정은 **그 run 안에서만** 유효하다. 다음 실행은 다시 묻는다.
- 되돌림은 `runs/<id>/step1/` 의 **사본**에만 한다. 원본 픽스처는 안 건드린다.
- `fixture` 모드는 게이트가 없으므로 되돌리지 **않는다** — 되돌리면 STEP2 가 멈춘다.
  픽스처 reviewed 의 배제 5건은 모두 `confirmed:true` 라 그대로 통과한다(57/57 유지).
- 게이트A 는 flag 가 없는 `hard_exclusion` role 도 질문으로 만든다. 안 물으면
  STEP2 가 멈췄을 때 **답할 방법이 없어** run 이 죽는다. `_apply_audit` 은 그 답을
  적을 flag 를 필요하면 만든다.
- ✅ **배제 승격(`choice: 3`)의 반경은 같은 항목에서 받는다** (2026-08-10 추가, 사람 결정).
  한때 "남은 구멍"으로 적어뒀던 건이다 — 아래 §7-7-1 참조.

### 7-7-1. 배제 승격(`choice: 3`)의 반경 · 항목 내 키 검사 (2026-08-10)

**있던 문제.** `apply_intent_answer(…, 3)` 은 `배제반경_m: null · confirmed: false` 인
`hard_exclusion` role 을 **새로** 만든다. §7-7 로 미확정은 STEP2 에서 막히는데,
게이트A 질문 목록은 **답변 전에** 만들어지므로 *그때 없던* role 의 질문이 있을 수
없다. 실측 —

| 시도 | 결과 |
|---|---|
| `intents: [{choice:3, radius_m:30}]` | **200 인데 값이 버려진다** — 저장된 `배제반경_m` 은 `null` |
| `exclusions: [{dataset_id:"02", role_index:0, radius_m:30}]` (같은 요청) | `400 게이트에 없는 대상입니다` |
| 배열 순서를 바꿔서 | 같음 — 질문 목록이 정적이다 |

앞의 것이 더 나쁘다. `_apply_audit` 은 payload **최상위 키**만 화이트리스트로 막고
**항목 내부는 안 봤다** → 프런트는 200 을 성공으로 읽고, run 은 STEP2 에서 죽는다.
같은 이유로 `exclusions` 의 `radius_m` 오타(`radius_mm`)는 「건너뜀 = 미확정 유지」로
읽혔다. 둘 다 조용한 실패다(원칙 1·4).

**바뀐 것.**

- `intents` 항목이 **`radius_m` 을 받는다**(`choice: 3` 에서만. 다른 choice 에 실으면 400).
  규약은 `exclusions` 와 같다 — 값 = 확정 · `null` = 반경 없이 면으로 확정 ·
  **키 생략 = 미확정 유지**(그 run 은 STEP2 에서 멈춘다).
- 질문의 `choices` 에 **`needs_radius`** 를 넣었다. 프런트가 반경 칸을 띄울 근거다
  (`needs_weight` 와 같은 자리). 추가 필드라 기존 응답을 안 깬다.
- 승격은 roles 를 통째로 갈아치우므로 **옛 flag 의 확정 표시를 지운다.**
  안 지우면 게이트를 다시 열었을 때 그 항목이 `editable: false` 로 굳는다(실측).
- `exclusions`·`intents`·`code_prefixes` **항목 안의 알 수 없는 키는 전부 400.**

```
exclusions    dataset_id · role_index · radius_m
intents       dataset_id · choice · weight · radius_m
code_prefixes dataset_id · op_index · prefix
```

🔴 **프런트가 여분 필드를 보내고 있었다면 그 요청은 이제 400 이다.** 조용히 버리던
것을 드러낸 것이므로 의도된 변경이다.

### 7-8. 검증 결과 (2026-08-05 실측)

두 가지를 확인했다. 둘 다 서버를 재시작하지 않고 러너를 **in-process** 로 불러 돌렸다.

**① 게이트 로직 단위 — 57/57 통과** (`app\tools\check_hitl_gate.py 흡연`, LLM 호출 0회)
(2026-08-10 에 `[7]`(§7-7) 10항목 + `[8]`(§7-7-1) 10항목을 더해 37 → **57**.
아래 목록은 앞 37개다)

- 계획 배열·재개 위치(`gate:` 칸에서 재개하지 않는다)
- 게이트A 질문 — 기존 run 의 `reviewed.json` 에서 배제 **5**(2026-08-10 이전엔 3 —
  flag 없는 role 2건을 안 물었다) · 의도 0 · 지역코드 1, 전부 읽기 전용.
  `op_index` 로 실제 op(`filter_by_code_prefix`)를 찾는지
- 게이트A 답변 — 확정분 수정 400 · 없는 대상 400 · 알 수 없는 필드 400 ·
  가점인데 weight 누락/0 400 · 반경 범위 400 · prefix 빈값 400 · 정상 적용 후 값 확인
- 게이트B 검증 — 반경 누락 · admin 에 반경 · 충돌 미확정 · 없는 지표 · 범위 · 합 0 ·
  알 수 없는 필드 전부 400
- 답변 → CLI 인자 왕복. `_parse_radius_arg`·`_parse_weight_arg`(정본 파서)로 되읽어 일치
- `[8]` 배제 승격 반경·항목 내 키 검사 10항목 (2026-08-10 추가, §7-7-1) —
  `needs_radius` 는 `choice 3` 에만 True · 승격 대상엔 exclusion 질문이 **없다**(그래서
  여기서 받아야 한다) · `radius_m` 적용 후 STEP2 통과 · `null` 도 확정 · 키 생략은
  STEP2 차단 · `choice 1` 에 `radius_m` 400 · 세 배열의 오타 필드 각각 400
- `[7]` 배제 전부 재확인·캐시 제거 10항목 (2026-08-10 추가) — 캐시 함수·상수 부재 ·
  `reset_exclusion_confirmations` 가 `hard_exclusion` **개수만큼** 되돌리는지 ·
  되돌린 뒤 `제안값` 이 보존되는지 · 5건 전부 `editable` · 픽스처는
  `assert_exclusions_confirmed` 통과하고 되돌린 문서는 **차단**되는지 ·
  flag 없는 배제에 답하면 `배제반경_m`·`confirmed`·`source=human_confirmed` 가 써지는지 ·
  `_prepare_dirs` 가 fixture 사본은 확정 유지 / hitl 사본만 전부 미확정 / **원본은 무변경**

**② 완주 무회귀 — fixture · hitl 두 모드가 같은 값에 도달**
(`app\tools\check_hitl_e2e.py 흡연`, 제안 패스 때문에 LLM 1회)

비교 항목 **10개**: `w_human` · `w_critic` · `w_final` · `radius_m`(지표별 맵) ·
`counts`(parcels·points·survive) · `spatial`(배제 union·내접폭 분포) ·
`coverage`(누적 100점) · `gap_kinds` · `topn_PNU` 20건 · `topn_점수` 20건.

| 대조 | 결과 |
|---|---|
| `r_20260805_003`(fixture) ↔ `r_20260805_004`(hitl) | 10개 전 항목 일치 |
| `r_20260805_001`(변경 **전** fixture) ↔ `r_20260805_004`(hitl) | 10개 전 항목 일치 |

🔴 **처음 쓴 대조기는 없는 키를 읽고 있었다.** `report.json` 의 실제 키는
`counts`·`data_gap`·`topn` 인데 `후보수`·`gap`·`topN` 으로 읽어 `None == None`,
`[] == []` 으로 통과했다 — **아무것도 안 본 채 초록불**이다. 지금은 비교 항목이
비어 있으면 `SystemExit` 로 멈춘다. 대조기가 조용히 통과하면 회귀보다 나쁘다.

🔴 **hitl 쪽 게이트A 답변은 2026-08-10 에 `{}` 가 아니게 됐다**(§7-7).
`_prepare_dirs` 가 배제 확정을 전부 되돌리므로 **5건 전부 편집 가능**이고, 답을
안 내면 STEP2 가 `SystemExit` 로 멈춘다. `check_hitl_e2e.py` 는 이제 각 질문의
`제안값`(없으면 `radius_m`)을 그대로 승인해 답을 만든다 — **픽스처가 확정했던
바로 그 값**이라 대조의 뜻(같은 답 → 같은 값)은 그대로다. 배제 말고 편집 가능한
질문이 있으면 답을 지어낼 수 없으므로 스크립트가 멈춘다.

게이트B 는 **픽스처 반경**(`07+02=150,06+03=300,08=50,09=150,10=250`)과
`slider_proposed` 그대로였다. 게이트B 가 제안한 LLM 반경
(`07+02=300,06+03=300,08=100,09=50,10=200`)을 그대로 쓰면 값이 달라지는 게
**정상**이다 — 이 대조는 *"같은 답을 넣으면 같은 값이 나오는가"* 를 본 것이지
*"LLM 제안이 픽스처와 같은가"* 를 본 게 아니다.

답변 기록은 `runs/<run_id>/hitl/{audit,weight}_answer.json` 에 남는다.

🔴 **부수적으로 러너 결함 두 개가 여기서 드러났다.** uvicorn 으로는 안 보였고,
in-process 로 부르니 바로 나왔다.

1. `_SERVER_BOOT` 가 마이크로초까지 갖는데 `started_at` 은 `timespec="seconds"` 로
   기록된다 → **부팅과 같은 초에 시작된 run 을 `_reap_orphans` 가 "이전 서버의 고아"로
   판정**해 실행 도중 `failed` 로 닫았다. → `.replace(microsecond=0)`
2. `status.json` 임시파일 이름이 공유라 실행 스레드와 폴링 스레드가 같은 `.tmp` 를
   두고 부딪혔다(Windows `PermissionError: WinError 32`).
   → `_IO_LOCK` + 스레드ID 접미사

`runs/r_20260805_002` 가 그 피해자다. `failed` 로 남아 있지만 **거짓 실패**다.
지우지 않고 둔다 — 기록이다.

---

## 8. `mode: "full"` — 업로드한 도메인을 STEP0 부터 (2026-08-10 신설)

### 8-1. 왜 생겼나

`fixture`·`hitl` 은 **둘 다 `<도메인>_FIX/` 를 요구했다.** 즉 화면1(업로드)로 만든
도메인은 API 로 **한 단계도 못 돌았다** — 계획(`_PLAN`)에 STEP0 프로파일링과
STEP1 감리가 애초에 없었기 때문이다. 화면1 → 화면2(감리 확인) → 화면3(가중치)이
이어지려면 그 두 칸이 있어야 한다.

막혀 있던 건 코드가 아니라 **값의 출처**였다. `fixture` 는 실행 조건을 기준값.json 에서
가져오는데, 업로드 도메인엔 그 파일이 없다. 그래서 `full` 은 조건을 **명시적으로 선언**한다.

### 8-2. 실행 조건 — 무엇을 사람이 정하고 무엇을 고정하나

사람이 정하는 것은 **둘뿐**이다(사람 결정 2026-08-10).

| 요청 필드 | 뜻 | 기본 | 검증 |
|---|---|---|---|
| `user_input` | 사용자 의도. STEP0.5 가 여기서 **시설·지역을 확정**한다 | **없음(필수)** | 빈 문자열·공백만 → 400 · `--` 로 시작 → 400 · 200자 초과 → 400 |
| `topn` | STEP4 가 뽑을 후보 개수 = 화면4 목록 길이 | **20** | 정수 아님 → 400 · 1~200 밖 → 400 |

나머지 실행 옵션은 **고정**이다. 값과 출처:

```
alpha 0.3 · decay gaussian(sigma_ratio 1/3) · scale log · spacing 20
출처: data_임시/흡연_FIX/기준값.json 의 `조건` (2026-08-03 고정 기준선)
```

🔴 **왜 이게 하드코딩 금지(원칙 2)에 안 걸리나** — 원칙 2 가 막는 것은 **도메인 값**
(시설명·지목·배제반경·지역코드)이다. 위 넷은 도메인이 아니라 **계산 방식**이고,
도메인이 바뀌어도 안 바뀌는 값이다.

🔴 **그래도 기본값을 조용히 쓰지 않는다.** CLI 기본값은 `scale=minmax`·`decay=null`
이라 위와 다르고, **그 차이 하나만으로 Top-N 이 통째로 갈린다**(실측). 그래서 러너가
매번 명시해서 넘기고, 요청 파라미터는 `runs/<run_id>/params.json` 에 남긴다.

> `params.json` 은 **status.json 스키마를 늘리지 않으려고** 따로 둔다(3절 계약 유지).
> 게이트에서 스레드가 끝나므로 이어받는 스레드가 `user_input`·`topn` 을 디스크에서
> 다시 읽어야 한다 — 메모리에 들고 있으면 서버 재시작에서 사라진다.

**선행 조건** — `data_임시/<도메인>/data/` 에 파일이 하나도 없으면 **400**이다.
빈 폴더로 STEP0 을 돌리면 빈 프로파일로 조용히 진행한다(원칙 1).

### 8-3. 단계 — **10개**다

| id | label | 실행 주체 | 실측(`r_20260810_002`) |
|---|---|---|---|
| `0` | 프로파일링 · 시설/지역 확정 | `gam2_run_pipeline.py` STEP0·0.5 | 5.7s |
| `1` | 감리 판정 · 상위법 검색 | 같은 프로세스 STEP1·2 | **211.7s** |
| `2` | 정제 | `gam2_clean_data.py` | 23.8s |
| `3-1` | 후보 필지 생성 | `make_parcel_candidates.py` | 17.1s |
| `3-2` | 가중치 산정 | `run_weight_model.py` | 12.3s |
| `4-1` `4-2` `4-3` | (2절과 같음) | `gam4_site_select.py` | 1.6 / 11.2 / 5.1s |
| `적재-감리` | **감리 규칙 DB 적재 (토론 근거)** | `scripts/load_audit_data.py` | 0.8s |
| `적재-후보` | **후보점 DB 적재 (화면5 목록)** | `scripts/load_topn_candidates.py` | 0.6s |

게이트 대기를 뺀 순수 실행 합계는 **약 290초**다. `1` 이 그중 73% 다(LLM·외부검색).

`0`·`1` 은 한 프로세스이고 경계는 stdout `▶ STEP 0 ` / `▶ STEP 1 ` 로 잡는다
(2절의 gam4 마커와 같은 방식 — 문구가 바뀌면 `sec` 은 `null` 이 된다).

### 8-4. 게이트는 `hitl` 과 **완전히 같다**

```
0 → 1 → [reviewed 시드] → 게이트A → 2 → 3-1 → 제안 패스 → 게이트B → 3-2 → 4
  → 적재-감리 → 적재-후보
```

7-4·7-5 의 요청/응답 형식이 그대로다. 게이트 뒤쪽 단계는 `hitl` 과 **같은 배열·같은
커맨드 조립 함수**를 탄다 — 갈라 짜면 "hitl 로는 되는데 full 은 다른 값"이 나온다.

🔴 **`reviewed` 시드**가 `hitl` 과 다른 유일한 지점이다. `hitl` 은 픽스처의
`reviewed.json` 을 run 폴더로 복사해 두고 시작하지만, `full` 은 STEP1 이 방금 만든
`_audit_result_enriched.json`(없으면 `_audit_result.json`)을 `_audit_result_reviewed.json`
으로 앉힌다. 폴백 순서는 정본 `review_hitl()` 과 **같다** — 다르면 CLI 로 돌린 결과와
API 로 돌린 결과가 갈린다.

그래서 `full` 은 run 생성 시점에 **아무것도 복사하지 않는다.** 정본 `step1_output/` 을
복사해 두면 감리가 실패했을 때 **남의 도메인 결과로 그대로 진행**한다(원칙 1·4).
→ `artifacts.reviewed` 는 `full` 에서 **`queued` 때 `null`** 이다(`fixture`·`hitl` 과 다름).

### 8-5. 적재 칸 — 화면4 와 화면5 사이의 다리는 **둘**이다

STEP4 는 `topN.geojson` **파일만** 쓴다. 화면5 는 테이블 **두 개**를 읽는다.

| 칸 | 넣는 것 | 화면5 에서 하는 일 |
|---|---|---|
| `적재-감리` | `<도메인>_audit_result_reviewed.json` → `audit_rules` | **무엇을 근거로** 토론할지 |
| `적재-후보` | `topN.geojson` → `booth_candidates` | **어디를** 논의할지 |

🔴 **한쪽만 넣으면 목록은 정상인데 토론이 첫 줄에서 죽는다.** 2026-08-10 `r_20260810_001`
이 정확히 그랬다 — `/candidates` 는 20행을 돌려주는데 `/stream` 은 0.4초 만에
`AI_ENGINE_ERROR: audit_rules 에 도메인 '흡연업로드' … 규칙이 없다`. 프런트에는
"후보는 있는데 토론이 안 된다" 로 보인다. 다행히 `_select_audit_rules` 가 조용히
빈 목록으로 진행하지 않고 **적재된 `(domain, run_id, target_facility)` 조합을 세어 알려준다**(원칙 1).

- 순서상 의존은 없지만 **근거를 먼저** 넣는다. 목록이 먼저 보이면 사람이 고를 수 있는데
  눌러도 안 되는 구간이 생긴다.
- 한 칸에 두 프로세스를 넣지 않는다 — 어느 쪽이 실패했는지 진행 표시에서 사라진다.
- 🔴 **세 모드 다 있다**(2026-08-11, 사람 결정으로 두 번 정정).
  예전 문구는 「`full` 에만 있다 — `fixture`·`hitl` 은 정본 재생이고 그 산출물은 이미
  `run_id='정본'` 으로 DB 에 있다. 재생할 때마다 다시 넣는 건 적재가 아니라 누적이다」
  였다. 앞 절반은 지금도 참이지만 **결론이 틀렸다**: `/candidates` 가 읽는 건 파일이
  아니라 `booth_candidates` 이므로, 적재 칸이 없으면 그 run 의 `topN.geojson` 이
  폴더에 있어도 프런트는 닿지 못한다 — **fixture run 의 결과는 화면5 에서 볼 수가
  없었다.** 정본 행이 DB 에 있다는 건 「그 run 을 볼 수 있다」가 아니라 「다른 run 을
  볼 수 있다」다. 시연에서 업로드를 건너뛰고 화면5까지 가려면 이 두 칸이 필요하다.
  누적 우려는 없앤 게 아니라 **옮겼다** — `runs/` 정리(`app/services/run_pruner.py`,
  `OMNISITE_RUNS_KEEP` 기본 100)가 상한을 준다.
  🔴 **`hitl` 은 같은 날 조금 뒤에 붙였다**(사람 지시). 처음엔 「게이트에서 사람을
  기다리므로 시연 프리셋이 아니다」로 뺐는데, 그건 **왜 `fixture` 에 넣는가**의 답이지
  **왜 `hitl` 에서 빼는가**의 답이 아니다. 게이트를 지나 완주한 run 은 사람이 값을
  확정한 run 이고, 그 결과를 화면5 에서 못 보는 건 같은 구멍이다.
  같은 자리를 이틀에 두 번 정정했다 — 「어느 모드에 넣나」를 **모드의 성격**(프리셋이냐)
  으로 판단했기 때문이다. 기준은 그게 아니라 **「그 run 의 결과를 화면5 가 읽어야 하나」**
  하나다. 그 기준으로는 세 모드가 전부 예다.
  ✅ **`hitl` 완주 실측**(2026-08-11 · `check_hitl_e2e.py` 통과) — `r_20260811_003`(fixture)
  ↔ `r_20260811_004`(hitl) 둘 다 **8칸 succeeded**, 꼬리 두 칸 done,
  `loaded {audit_rules:13, booth_candidates:20}`, `cascaded` 전부 0.
  실 DB 에서 `정본`/`_002`/`_003`/`_004` 가 각각 13·20행으로 **격리**돼 있다.
  🔴 안 돌리려던 사유(「게이트 뒤쪽이 fixture 와 같은 배열·같은 `_proc_of` 라 새로 도는
  코드가 없다」)는 **맞았지만 증명이 아니었다** — 코드를 읽은 결론과 끝까지 갔다는 사실은
  다른 문장이다(원칙 5).
- 적재기는 같은 `(domain, run_id)` 만 지우고 다시 넣는다. 다른 도메인·정본 행은 안 건드린다.
- 개수는 **파일에 있는 만큼 전부**다. 적재기에 20 이 박혀 있지 않다.
- 두 칸이 끝나면 `status.json` 의 **`loaded`** 에 넣은 결과가 남는다(3-1).
  프런트는 거기 있는 `run_id` 를 `/candidates` 에 그대로 넘기면 된다 —
  "full 이면 run_id 와 같다"는 규칙을 따로 들고 있을 필요가 없다.

✅ **같은 도메인 2회차 누적 해소 (2026-08-10, 사람 승인 · 6절 B안).** 적재는 예전부터
`(domain, run_id)` 단위 교체였는데 읽는 쪽만 `(domain, target_facility)` 로 걸러
2회차에 **26행**이 됐다(hard 10 · positive 16, 고유 요인명 14). 예외 없이 AHP 가중치만
묽어진다. 지금은 조회도 `run_id` 를 보고, 그 값은 `booth_candidates` 행에서 온다 —
**적재 단위 ↔ 조회 단위가 같아졌다.** 정본 `run_id` 어휘도 두 테이블 모두 `"정본"` 이다
(예전엔 `step1_output` ↔ `step4_output` 으로 갈려 run_id 조인이 0건이었다).
`booth_candidates` 는 `/candidates` 가 최신 run 하나만 돌려줘 애초에 짝이 맞아 있었다.

### 8-5-1. 왜 **이 둘만** DB 인가 — 산출물의 자리를 정하는 기준 (2026-08-11)

산출물은 STEP0~4 를 통틀어 열 몇 개인데 DB 로 가는 건 위 둘뿐이다. **「중요해서」가
아니다** — 그건 기준이 못 된다(`weight_set` 도 중요하다). 기준은 셋이고, 셋을 다
만족하는 산출물만 테이블을 갖는다.

**① 파이프라인이 안 읽는다 (읽는 건 웹 요청뿐).**
`audit_rules`·`booth_candidates` 를 참조하는 파일 19개를 전수로 셌다 —
`gam2_*`·`gam4_*`·`make_parcel_candidates`·`run_weight_model` 은 **한 곳도 없다.**
전부 `api/v1/{simulations,stakeholders}` · `candidate_context` · 적재기 · 대조기다.
그래서 DB 로 가도 **정본이 안 갈린다.** 반대로 `clean_report`·`weight_set`·`report.json`
은 `gam4_site_select.py:81` 등이 **파일로 읽는 파이프라인 입력**이라, DB 로 옮기면
정본을 고치거나 사본을 하나 더 만들어야 한다.

**② 행 단위로 좁혀야 한다 (통짜 JSON 으로는 불가능).**
```sql
audit_rules       WHERE domain=? AND run_id=? AND target_facility=?
booth_candidates  WHERE id=?                       -- 사람이 화면4 에서 고른 parcel_id
                  WHERE domain=? [AND run_id=?] ORDER BY rank   -- /candidates 목록
```
`reviewed.json` 을 매 요청마다 통째로 파싱하면 저 3중 조건을 **요청 코드에서 재구현**해야
한다. 실제로 그 조건 하나가 빠져 근거가 26행으로 두 배가 됐던 것이 6절 B안이다.
나머지 산출물은 통째로 쓰는 값이라 좁힐 게 없다.

**③ 다른 테이블이 FK 로 가리킨다 (파일에는 FK 를 못 건다).**
`conflict_simulations.parcel_id → booth_candidates.id` (ON DELETE CASCADE) ·
`debate_logs.simulation_id → conflict_simulations.id`.

넷째로, **파일은 언젠가 지워진다.** `run_pruner` 가 부팅마다 돌고 보호는 `keep`
최근 N개(기본 100)와 진행 중 둘뿐이다(3-2). 오래된 run 의 `.gpkg`·`.parquet` 는
사라지지만 그 후보점으로 연 공청회는 계속 조회돼야 한다.

같은 기준으로 나머지를 재면 —

| 산출물 | ① 파이프라인 미참조 | ② 행 질의 | ③ FK 대상 | 자리 |
|---|---|---|---|---|
| STEP1 `reviewed.json` | ✅ | ✅ | — | **DB** `audit_rules` |
| STEP4 `topN.geojson` | ✅ | ✅ | ✅ | **DB** `booth_candidates` |
| STEP2 `clean_report.json` | ❌ STEP3·POI 가 읽는다 | ❌ | ❌ | 파일 |
| STEP2 `clean_NN.gpkg/parquet` | ❌ STEP3·4 가 읽는다 | ❌ | ❌ | 파일 |
| STEP3 `weight_set.json` | ❌ STEP4 가 읽는다 | ❌ | ❌ | 파일 |
| STEP3 `후보_지적도필지.gpkg` | ❌ STEP3·4 가 읽는다 | ❌ | ❌ | 파일 |
| STEP4 `report.json` | ❌ 대조기가 읽는다 | ❌ | ❌ | 파일 |
| STEP0 `profile.json` | ✅ | ❌ | ❌ | 파일 |

**한 줄로: DB 에는 「웹이 조인·필터해야 하는 것」만 넣는다. 파이프라인이 파일로 읽는
것은 파일에 둔다 — 옮기면 정본이 둘이 된다.**

🔴 이 기준이 어디에도 안 적혀 있어서 실제로 「그럼 나머지도 넣자」가 한 번 올라왔다
(`feature/pipeline-db-export`, STEP1~4 산출물 8테이블). run 1회당 약 **106,077행**이
되고, 그중 `selected_topn_sites` 는 `booth_candidates` 와 **같은 것을 SRID 만 다르게**
한 벌 더 갖는 구조다. 채택하지 않은 이유가 곧 이 절이다.
이 절은 **규칙**이고, 그렇게 정한 **근거**(전수 census 19파일 · 반례 검토 · 이 기준이
말하지 **않는** 것 3가지)는 `02_작업일지\2026-08-11_산출물의_자리_왜_이_둘만_DB.md` 에 있다.
바꿀 때는 **이 절을 먼저** 고친다 — 여기가 정본이다.

⚠ run **메타데이터**(`status`·`started_at`·`finished_at`·`error`·`user_id`)는 여기서
말하는 산출물이 아니라 **별개 축**이다 — 산출물 행은 단계가 성공해야만 생기므로
실패·대기 중인 run 은 DB 에서 아예 안 보인다(원칙 4).
그쪽 구조는 **확정됐다** — `01_설계결정\산출물_저장구조_4계층_확정.md`
(① 산출물 中 DB 2개 = 이 절 · ② 나머지 산출물 = 디스크 · ③ run 메타데이터 =
`run_records` 신설 · ④ 진행 상태 = `status.json`).
✅ **`run_records` 는 2026-08-11 에 만들었다** — 규약은 **3-3 절**에 있다.
⚠ 만든 뒤에도 정본은 여전히 `status.json` 이다 — DB 컬럼 이름이 `last_known_status`
인 이유가 그것이다.
**설계는 2왕복으로 닫혔다**(2026-08-11) — 행은 **발급 시** 만들고(`queued`) 진행률만
DB 에 안 묻는다 · 러너는 `record_run_start()`/`record_run_end()` **둘만** 부른다 ·
그 함수가 실패해도 run 은 안 죽이되 **사유는 남긴다**.
🔴 **배선 순서는 하나뿐이다: 테이블+함수 실재 → 우리 호출 배선 → 마이페이지 API.**
호출을 먼저 넣으면 함수가 없는 동안 **모든 run 이 시작에서 죽는다**.
⚠ 여기 「그쪽이 주는 함수를 부른다」고 적혀 있던 건 **소유가 바뀌기 전** 문장이다
(2026-08-11 정정). 사람 지시로 테이블·함수·배선을 **우리가 만들었다** — 앞 두 칸은
끝났고 남은 건 **마이페이지 API** 하나다. 소유가 바뀌었으므로 상대에게 **통보**한다.

### 8-5-2. `conflict_simulations.parcel_id` 는 **NOT NULL** 이다 (2026-08-11)

이 테이블에는 **`run_id` 컬럼이 없다.** run 에 닿는 경로는

```
conflict_simulations.parcel_id → booth_candidates.id → booth_candidates.run_id
```

**조인 하나뿐**이다. `parcel_id` 가 NULL 이면 「어느 실행의 어느 입지를 토론했나」를
알 방법이 아예 사라진다. 그래서 실 DB 를 `SET NOT NULL` 로 조였다(사람 승인).
`hearing_results_b.parcel_id` 는 처음부터 NOT NULL 이었다 — 짝을 맞춘 것이다.

**왜 `run_id` 컬럼을 대신 넣지 않았나** — 값이 이미 두 곳에 있다.

| 경로 | 뜻 |
|---|---|
| ⓐ 위 조인 | 이 후보점이 **지금 속한** run |
| ⓑ `result_json->'basis'->>'run_id'` | 토론할 때 **근거로 삼은** run |

- 컬럼은 **세 번째 사본**이 된다.
- ⓐ·ⓑ 는 원래 **다른 문장**이라 컬럼이 어느 쪽을 담을지 정할 수 없다 — 정하지 않으면
  한 필드가 두 의미를 갖는다(CLAUDE.md 함정표 `audit_rules.facility_type` 과 같은 모양).
- FK 가 `ON DELETE CASCADE` 라 ⓐ 는 **dangling 이 구조적으로 불가능**하다. 컬럼에는
  그 보증이 없다: 같은 run 을 재적재하면 후보점이 새 `id` 로 들어오는데(그때 토론은
  CASCADE 로 사라진다) 컬럼 방식이었다면 옛 `run_id` 를 가리키는 행이 남는다.
- 읽는 쪽(`GET /simulations/hearings?run_id=`)이 이미 이 조인 하나로 동작한다.
  컬럼을 더하면 **같은 질문에 답이 둘**이 되고 갈렸을 때 정본 규칙을 또 만들어야 한다.

실측(2026-08-11): `conflict_simulations` 3행 · `hearing_results_b` 1행 모두
`parcel_id` 가 채워져 있고 조인이 **전부 해석**된다. 다만 `basis` 는 옛 행 2건(id 15·17)
에 **없다**(`basis_snapshot` 이 2026-08-11 신설) — 그 행들에게는 ⓐ 조인이 **유일한**
경로다. 그래서 보증해야 할 것은 ⓑ 가 아니라 ⓐ 였다.

⚠ 쓰기 경로는 `resolve_candidate`(실패 시 `CandidateNotFound`)를 통과해야만 저장하므로
NULL 은 원래 생길 수 없었다. 하지만 그건 **코드의 약속이지 DB 의 보증이 아니다** —
손입력·다른 도구로 들어오면 막을 게 없었다. 적용은 `schema_step5.sql`(기존 DB) +
ORM `nullable=False`(새 DB). 이 테이블은 **어느 `.sql` 에도 `CREATE TABLE` 이 없고**
`create_missing_tables.py` 가 ORM 으로 만든다 — **양쪽을 같이 고칠 것.**
검증: `schema_step5.sql` 재실행 rc=0(멱등) · NULL INSERT 는 not-null 위반으로 거절 ·
`check_hearings.py` **60/60**.

### 8-6. 화면4 → 화면5 — **위치는 사람이 고른다**

```
GET /api/v1/simulations/candidates?domain=<도메인>[&run_id=][&limit=]
→ {"domain","run_id","count","candidates":[{parcel_id, rank, score, pnu, jibun, lat, lng, ...}]}

사람이 목록에서 하나 선택 → 그 원소의 parcel_id 를 /simulations/stream 에 넘긴다
```

- 🔴 **`rank == 1` 은 추천이지 강제가 아니다**(사람 결정 2026-08-10). 예전 문구는
  "첫 원소의 `parcel_id` 를 넘긴다"였는데 그건 폐기한다.
  `/stream` 은 예나 지금이나 **임의의 `parcel_id`** 를 받는다 — 바뀐 건 계약이지 구현이 아니다.
- 🔴 **`rank` 는 점수 내림차순이 아니다**(MCLP 커버 기여 그리디). 흡연 실측에서
  4위 0.7793 > 1위 0.7703 이다. 목록을 점수로 다시 정렬하지 마라 — **다른 점**이 1위가 된다.
- `run_id` 를 안 주면 **가장 최근에 적재된 run 하나**만 돌려준다. 도메인으로만 거르면
  실행 두 번의 행이 섞여 `rank` 가 `1,2,3…,1,2,3…` 이 된다.
- `limit` 은 **기본이 없다(전량)**. 예전 기본값 20 은 STEP4 `--topn` 기본값과 우연히
  같았을 뿐이라 `topn=30` 으로 돌리면 10개가 **말없이 잘렸다**.

### 8-7. 검증 (2026-08-10 실측)

| 항목 | 결과 |
|---|---|
| `check_fixture.py 흡연` | **57/57** (무회귀) |
| `check_hitl_gate.py 흡연` | **37/37** |
| `check_upload_api.py --no-ingest` | **16/16** |
| 러너 배선 in-process | **22/22** (계획·조건·커맨드·검증·반경 출처) |
| `POST /runs` 400 경로 | **8/8** (full 필수값 4 · 타 모드 침입 2 · 모드 1 · 타입 1) |
| 적재 칸 실행 | `r_20260808_002` → 20행 삽입 · `[TOP1] id=42` · rc=0 |
| `/candidates` | 적재 후 최신 run 을 기본으로 반환 · `limit=0` 400 · 없는 run 404 |

### 8-8. 완주 실측 (2026-08-10) — **업로드 → 화면6 까지 한 번에**

앞 절까지는 *"각 칸이 따로 돈다"* 까지였다. 아래는 **끝까지 돌린 결과**다(원칙 5).

입력 — `data_임시/흡연/` 의 `data/`(11개) · `law/`(3개)를 **화면1 업로드 API 로**
새 도메인 `흡연업로드` 에 넣었다. 파일을 제자리에서 쓰지 않았다.
`user_input="용산구 흡연부스 부지 선정"` · `topn` 기본 20.

| 항목 | `r_20260810_001` | `r_20260810_002` |
|---|---|---|
| 결과 | `succeeded` (9칸) | `succeeded` (10칸) |
| 총 소요 | 9분 57초 | 9분 11초 |
| 게이트 | A·B 둘 다 정상 정지 → POST 로 재개 | 같음 |
| 화면5 | parcel 65(rank 4) · 1,476 events · 40.8s | parcel 86(**rank 5**) · 1,564 events · 39.6s |
| 화면6 | `/report/65` 200 · **82,551 bytes** `%PDF-` | — |

**값은 픽스처 기준선과 전부 일치했다**(업로드 경로로 들어가도 같은 값이 나온다):
후보 42,216 / 후보점 66,915 / 생존 56,967 · 배제 union **1.1107 km²** ·
내접폭 중앙 10.7784 p95 251.7108 합 3,445,355.8962 폭2m통과 59,989 ·
gap 6건(4/1/1) · `w_final 0.1858/0.1827/0.1975/0.0870/0.1683/0.1786` ·
`alpha 0.3 · scale log · decay gaussian` · `hitl.value_source="human"`.

001 과 002 의 차이는 **`적재-감리` 칸**이다. 001 은 그게 없어 화면5 가 죽었고(8-5),
칸을 넣은 뒤 002 는 화면5 목록까지 갔다.

🔴 **여기 "손으로 한 단계도 거들지 않고" 라고 적었던 건 틀렸다**(정정 2026-08-10).
**바로 위 표에 "게이트 A·B 둘 다 정상 정지 → POST 로 재개" 라고 적혀 있다** — 같은
절 안에서 모순이었다. `full` 계획에는 `게이트A`·`게이트B` 칸이 있고 답을 주기 전까지
`awaiting_hitl` 로 멈춘다(7-3). **`full` 은 무인 완주 모드가 아니다.**

그리고 **위 `w_final` 이 기준선과 같은 이유도 게이트 답에 있다.** `w_human`·`w_final` 은
게이트B 의 `radius`·`slider` 로 결정되므로, 값이 기준선과 같았다는 건 그때 답이 픽스처
조건(`radius 150/300/50/150/250` · `slider 0.75/0.8/0.7/0.5/0.7/0.7`)과 **같았다는 뜻**이다
(값에서 거슬러 올라간 추론이다 — 그 run 폴더는 지금 없다. 아래 참조).
즉 이 절이 증명하는 건 「업로드 경로로도 기준선이 재현된다」가 아니라
**「같은 답을 넣으면 같은 값이 나온다」**(A2 의 `check_hitl_e2e` 결론과 같은 성질)이다.

🔴 **`run_id` 가 재사용되던 시절이 있었다 — 아래 표가 그 흔적이다.**
`_new_run_id` 는 `runs/r_<날짜>_*` **폴더를 세어** 번호를 매겼고, 폴더를 지우면 번호가
되돌아갔다. 이 절의 `r_20260810_001~004`(도메인 `흡연업로드`)는 폴더 정리 후 **같은
이름의 다른 run** 으로 덮였다(2026-08-10 실측):

| 이 절이 말하는 run | 지금 `runs/` 에 있는 같은 이름의 run |
|---|---|
| 001 full · 흡연업로드 · 9칸 | fixture · 흡연 · 6칸 · 79초 |
| 002 full · 흡연업로드 · 9분 11초 | full · **흡연_E2E** · 5분 32초 |
| 003 fixture **실패**(FileNotFoundError) | fixture · 흡연 · **succeeded** |
| 004 fixture · 71초 | **hitl** · 흡연 |

정정을 쓰면서 나도 한 번 밟았다 — `runs/r_20260810_002/hitl/` 에 답 파일이 있는 걸
근거로 삼았는데 **그건 흡연_E2E run 이었다.** 결론(게이트가 둘이다)은 그대로지만
근거는 run 폴더가 아니라 **계획·계약**이어야 했다. 앞으로 run 을 문서에 인용할 땐
`run_id` 만 적지 말고 **도메인·시각·모드를 같이** 적는다.

✅ **2026-08-11 원인 제거.** `_new_run_id` 는 이제 `runs/run_seq.json`(날짜 → 마지막
발급 번호)의 **최고수위**와 폴더 최대값 중 **큰 쪽 + 1** 을 쓴다. 폴더를 지워도 번호가
안 되돌아간다. 원장은 **발급 시점에** 쓴다 — `start_run` 이 실패하면 그 번호는 버려지는데,
번호 하나 버리는 건 싸고 다시 쓰는 건 위 표 같은 일을 부른다. 원장 파일이 깨져 있으면
`{}` 로 넘기지 않고 **raise** 한다(조용히 넘기면 판정이 폴더 세기로 되돌아가는데,
그게 바로 이 파일이 막으려는 재사용이다).

`fixture` 모드도 같은 날 다시 완주시켰다 — `r_20260810_004` **71초 · 6칸 · succeeded**,
값은 위와 동일. 단 그 전에 `data_임시/흡연/fixture/profiles.json` 이 **없어서 실패**했다
(`r_20260810_003`, STEP2 에서 10.6초 만에 `FileNotFoundError`). 5절도 함께 볼 것.

### 8-9. run_id 정렬 이후 재실측 (2026-08-10 · 6절 B안 적용 후)

| 경로 | 대상 | 결과 |
|---|---|---|
| 음성 — 규칙 없는 run | parcel 62 (`r_20260810_001`) | **1 event · 0.56초** 정지. 적재된 `(도메인, 실행, 시설)` 목록을 그대로 알려준다 |
| 정본 | parcel 2 (흡연 · `run_id='정본'`) | 1,498 events · 35.1s · 시나리오 C · CSS 7.5 · 수용도 15.0% |
| run 격리 | parcel 84 (흡연업로드 · `r_20260810_002`) | 1,407 events · 28.2s · 시나리오 B · CSS 7.5 · 수용도 40.0% |
| 적재 후 재시도 | parcel 62 (규칙 13행 적재) | 1,539 events · 32.1s · 정상 |
| 누적 분리 | 흡연업로드 **26행 저장** | 62·84·2 각각 **13행 · 고유 요인명 8** 로 풀린다 |
| 화면6 | `/report/2` | 200 · `application/pdf` · **83,438 bytes** `%PDF-` |

🔴 **고치기 전에는 parcel 62 가 39.5초 동안 토론을 완주했다** — 남의 run 감리 규칙으로.
예외가 안 나므로 결과만 보면 정상이다. 이게 이 결함의 성질이다.

⚠ `GET /simulations/results/{id}` 의 `{id}` 는 **`parcel_id`** 다(`simulations.py:970`).
로그의 `simulation_id=` 를 넣으면 404 가 나는데 **회귀로 보인다.** 실측: `/results/{2,84,62}` 전부 200.

### 8-10. 서버 재시작 후 전 구간 재실측 (2026-08-10 · 사람 지시)

*"처음부터 끝까지 … 특히 마지막에 생성된 결과 문서가 제대로 DB에 저장되는지도 (해당 run_id로)"*

재시작 전에 **활성 run 0건**을 `runs/*/status.json` 직접 조회로 확인했다
(`reap_orphans()` 가 남의 run 을 닫는 걸 막기 위해).
도메인 **`흡연_E2E2`**(신규) · 흡연 원본 `law/` 2 + `data/` 11(536MB)을
**화면1 업로드 API 로만** 5.0초에 넣고 `mode:"full"` · `topn:20`.

| 칸 | 초 | 칸 | 초 |
|---|---|---|---|
| 0 프로파일링 | 10.1 | 4-1 후보점 생성 | 1.58 |
| 1 감리·상위법 | 237.18 | 4-2 점수화·배제 | 8.99 |
| 2 정제 | 24.43 | 4-3 위치 선정 | 4.89 |
| 3-1 후보 필지 | 17.26 | 적재-감리 | 0.93 |
| 3-2 가중치 | 10.52 | 적재-후보 | 0.72 |

`succeeded` · `loaded {"run_id":"r_20260810_006","audit_rules":13,"booth_candidates":20}` ·
산출물 8키 전부 URL · `error: null`.

**게이트 응답은 제안값을 그대로 되돌려줬다** — `proposed_m`(없으면 `radius_m`) ·
`radius_proposed` · `slider_proposed`. 값을 지어내지 않는 「엔터로 제안값 승인」이다.
게이트A 질문 6건 중 `04` 지역코드는 `editable:false` 라 건너뛰고, 배제 5건은
`01=10 · 05=30 · 06=10 · 07=10 · 11=30` — CLAUDE.md 레이어별 실측표와 같은 값이다.
🔴 `07 버스정류소`만 `proposed_m` 이 `null` 이라 `radius_m` 을 썼다.

**값 대조** — 결정론 구간은 기준선과 **전부 일치**(후보 42,216 / 후보점 66,915 /
생존 56,967 / union 1.1107 / 내접폭 4종 / 폭2m통과 59,989 / 수요점 6,797 / gap 6).
`w_final` 만 갈렸다: 이번 게이트B 답이 **LLM 제안값**(`07+02=200 · 09=100 · 10=150` ·
슬라이더 `04=0.8 · 08=0.3 · 09=0.8`)이라 8-8 의 픽스처 조건과 다르다. **회귀가 아니다.**

**화면5** — `/candidates?domain=흡연_E2E2&run_id=r_20260810_006` → 20건 전부 이 run.
rank 1 = `parcel_id=122`(0.7538). 🔴 rank 3 이 0.7781 로 더 높다(커버 기여 그리디).
`/stream` → 1,440 events · 53.5초 · 시나리오 **C** · CSS 7.5 · 수용도 15.0%.
`conflict_factors` **8개** = `positive_factor` 8행 (26행이 섞였다면 16개다 — 6절 B안이
실제로 걸린 증거).

**🔴 결과 문서의 run 귀속 — 조인으로만 확인된다.**
`conflict_simulations` 에는 **`run_id` 컬럼이 없다.** 경로는
`parcel_id → booth_candidates.id → booth_candidates.run_id` **하나뿐**이다.

```
conflict_simulations  id=18 · parcel_id=122 · facility_type=흡연부스 · css_score=7.5
  result_json 있음 5,736자 · worst_scenario 만 채움(A/B 는 NULL = 사실, 원칙 4)
  candidate_land_id=None ← booth_candidates.land_id 가 NULL 이라 유도값도 NULL(지어내지 않음)
  ⟵ JOIN booth_candidates : run_id='r_20260810_006' · domain='흡연_E2E2' · rank=1
debate_logs  simulation_id=18 · 14행 (발화 1건 = 1행)
```

교차 오염 없음 — `정본`(흡연) · `r_20260810_002`(흡연_E2E) · `r_20260810_006`(흡연_E2E2)
셋 다 `audit_rules 13` · `booth_candidates 20` 으로 격리.

**화면6 둘 다** — `/simulations/results/122/pdf` → 200 `application/pdf` **85,039 B** `%PDF-` ·
`/report/download/hwpx` → 200 `application/hwp+zip` **4,520 B** `PK`,
`BinData/image1.png`·`image2.png` **2개** · 대체문구 0 ·
**선언 4곳 전부 일치**(manifest 2 · content.hpf 2 · header `bindataList` 2 ·
section0 `<hp:pic>` 2, `binaryItemIDRef="image1"/"image2"`) · XML 4개 파싱 통과.
