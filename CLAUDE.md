# OmniSite 백엔드 (BigProject_Back)

B2G 공간의사결정지원(SDSS). 갈등시설 입지 선정 — GIS 최적화(MCLP) + 다중에이전트 공청회 시뮬레이션.
MVP: 용산구 흡연부스 / 2차: 성동구 재활용정거장.

**엔진은 그대로, 데이터만 바꾼다.** 도메인이 바뀌어도 코드는 안 바뀌어야 한다.

---

# 항상 한국어로 답한다

## 절대원칙

1. **조용한 실패 금지.** 애매하면 `raise`. 추측해서 진행하지 않는다.
   - 시끄러운 크래시를 조용한 오동작으로 바꾸는 변경은 **후퇴**다.
2. **하드코딩 금지.** 도메인 값(시설명·지목·반경·지역코드)은 주입하거나 HITL 로 확정한다.
   - 기본값이 필요하면 왜 도메인 상수가 아닌지 주석에 남긴다.
3. **LLM 제안 → 결정론 코드 실행 → 사람 확정(HITL).**
   - LLM 이 판정할 것: 의미·역할·의도
   - 코드가 조달할 것: 데이터에서 확인 가능한 것(코드·좌표계·형태)
4. **산출물은 거짓말하지 않는다.** 적용 안 한 것은 "안 했다"고 기록한다.
5. **실측 없이 단정하지 않는다.** 설명문·파일명이 아니라 값을 확인한다.

---

## 🔴 알려진 함정 (전부 실제로 발생했음)

| 함정 | 증상 | 방어 |
|---|---|---|
| **행정코드 불일치** | 마포구(11440) 데이터가 용산구(11170)로 통과. 행 수 검증 전부 통과 | 크로스워크 **값 대조** |
| **키워드 매칭** | `"동"` 이 자동차등록대수·활동인구에 걸림. `next()` 라 컬럼 순서가 조인 키를 정함 | 값·구조로 판정 |
| **좌표계 추측** | `X좌표/Y좌표` 를 4326 으로 읽으면 공간조인 0건 → 지표 전부 0 | 값 범위(124~132/33~39)로 판정 |
| **exclusion_type 오판** | 점 데이터를 `polygon` 으로 판정 → 반경 없음 → **배제 면적 0** | 지목 배수 판정 (S9, 완료). 면적 0 이면 `SystemExit` |
| **배수만으로 면 판정** | 어린이집 종교용지 7점(교회 부설) 배수 10.6x → 교회 필지 전체 배제 | **관측 하한 10%** 동시 충족 (7점은 4.0%) |
| **합계 0** | `human_weights` 가 원본 반환 → 전 후보 점수 0 | `raise` (해결됨) |
| **하드코딩 상수** | MCLP `pool=5000` 이 CLI 노출 없이 박혀 Top-20 60% 교체 | CLI 노출 |
| **LLM 변동** | 같은 입력에 `seed_weight` 0.7↔0.8, HITL 대기 4건↔3건 | 회귀엔 고정 픽스처 필요 (S12) |
| **POINT EMPTY** | `isna()` 로 안 잡힘 | `~is_empty & notna()` |
| **CSV 저장** | 앞자리 0 유실(`"00001"`→`1`) → 조인 파괴 | parquet (pyarrow 필수) |
| **모듈 사본** | `app/services/dummy/gam4_spatial_ops.py` 가 정본(378행)의 낡은 368행 사본이었다(2026-08-04 dummy/ 삭제로 해소). **import 는 멀쩡히 되고 값만 다르게 나온다** — 안 터지니 안 걸린다 | 같은 파일명을 두 곳에 두지 않는다. 옮길 땐 사본이 아니라 **이동**. 값이 안 맞으면 `sys.modules[...].__file__` 부터 찍는다 |
| **import 시점 외부접속** | `upload.py:10`·`sim_ai/graph.py:57` 이 모듈 최상단에서 `RagVectorStorage()` 생성 → `vector_db.py:32` 의 `PGVector` 가 **import 중에** Postgres 접속. DB 없으면 `import app.api.v1.upload` 가 **525.7초**(실측, rc=0). 등록하면 uvicorn 기동이 9분 | 모듈 최상단에서 DB·API·파일 접속을 하지 않는다. 요청 시점에 만든다 |
| **타임아웃을 "무한"으로 읽음** | 위 건을 240초 타임아웃으로 재고 "끝나지 않는다"고 단정했다. 실제로는 525.7초에 **성공**했다. 게다가 `simulations` 의 진짜 사유는 DB 가 아니라 15행 `pdf_service` 부재였는데, 12행 DB 대기에 가려 240초 안에 안 드러났다 | 타임아웃은 "여기까진 안 끝났다"만 증명한다. **"끝나지 않는다"는 다른 주장이다**(원칙 5). 끝까지 돌려보고 말할 것. import 실패는 **첫 에러가 진짜 원인이 아닐 수 있다** — 앞 줄이 느리면 뒷줄 에러가 안 보인다 |
| **응답 Content-Type** | `.gpkg` 25MB 바이너리가 `text/plain; charset=utf-8` 로 나갔다. `FileResponse` 에 `media_type` 미지정 → `mimetypes` 가 모르면 텍스트로 떨어진다. `res.text()` 쓰면 조용히 깨짐 | 파일 응답에 `media_type` 을 **명시**한다. 모르면 `text/plain` 이 아니라 `application/octet-stream` — 틀린 단정보다 참인 진술이 낫다 |
| **전이 의존 버전 이동** | `pip install langchain-openai` 가 `openai` 를 2.44→2.53 으로 말없이 올렸다. `sse-starlette` 은 `starlette` 0.37→1.3 을 시도(막힘). **감시 목록 밖이라 안 보인다** | `pip install -c constraints.txt` + `--dry-run` 선행. 사후엔 `pip freeze` **전체 diff** — 5개만 보면 놓친다 |
| **커밋했는데 절반만 돈다** | `3cc73ff` 후에도 서버가 **기동 14:16 / 커밋 18:03** 인 옛 프로세스였다(`--reload` 없음). 그런데 `run_weight_model.py` 는 **자식 프로세스라 즉시 새 코드**, `pipeline_runner.py` 는 **import 라 옛 코드** → 새 인자를 안 넘겨 `argparse` 기본값이 들어갔다. 안 터지고 값만 틀린다 | "고쳤다"고 말하기 전에 **프로세스 기동 시각 ↔ 커밋 시각**을 비교한다. 자식 CLI 와 임포트 모듈은 **반영 시점이 다르다** |
| **시각 정밀도 불일치** | `_SERVER_BOOT` 는 마이크로초인데 `started_at` 은 `timespec="seconds"` → 부팅과 **같은 초**에 시작된 run 을 `_reap_orphans` 가 "이전 서버의 고아"로 보고 실행 중에 `failed` 로 닫았다. uvicorn 으로는 부팅·요청 간격 때문에 **한 번도 안 나타난다** | 비교하는 두 값의 **절삭 단위를 맞춘다**. "실서버에서 안 나오니 없는 버그"가 아니다 — in-process 로도 돌려본다 |
| **같은 이름의 다른 스키마** | `schema.sql` 과 ORM 이 테이블명은 같은데 컬럼이 다르다. 공통 14개 중 11개는 완전 일치이고 `conflict_simulations`·`verified_precedents` 만 갈리는데 **하필 `/audit/*` 이 쓰는 둘.** 이름이 같아 **`SELECT` 를 짤 때까지 안 보인다** | 이름이 아니라 **컬럼 집합**을 대조한다(`Base.metadata` ↔ `schema.sql` 파싱). 스크립트는 `01_설계결정\백엔드팀_API현황_및_Redis_Postgres_전환.md` §10-1 |
| 🔴 **대조 대상이 둘인 줄 알았는데 셋** | 위 항목의 확장(2026-08-08 실측, 정정). `conflict_simulations` 는 **ORM·`schema.sql`·실제 DB 가 전부 다르다.** 필지 참조 컬럼이 `parcel_id`(FK `booth_candidates.id`) / `cadastral_land_id` / `candidate_land_id` 로 셋 다 이름도 대상도 다르고, **공통 컬럼은 `id`·`created_at` 둘뿐**이다. 위 항목이 적어둔 `ForeignKey("parcels.id")` 도 `merge_Back` 에선 틀렸다 → `booth_candidates.id`. `/simulation(s)/results/{id}` 가 **500**(`UndefinedColumnError`)이고, 쓰기(`simulations.py:492`)는 예외를 `print` 로 삼켜 **조용히 실패**한다(원칙 1·4) → 그래서 `conflict_simulations`·`verified_precedents` 둘 다 **0행**이다. 🔴 2026-08-09 추가 실측 — **컬럼 이름만 맞춰선 안 끝난다**: ① 엔진은 시나리오를 **1개만** 내는데 컬럼은 3개(A/B/C) ② `result_json` 이 갈 컬럼이 없고 그 안의 **`debate_logs` 는 재구성 불가**(Redis TTL 600초뿐) ③ 런타임 `parcel_id` 는 `booth_candidates.id` 인데 FK 는 `candidate_lands` — **id 공간이 다르다**(1행 ↔ 6,524행). 그냥 넣으면 FK 는 통과하고 **다른 필지**를 가리킨다 | `schema.sql` 이 현행이라고 가정하지 않는다 — **실제 DB 를 `\d` 로 직접 본다.** 시드를 뜬 기준이 저장소 SQL 과 다를 수 있다. **✅ 2026-08-09 해소(B안, 사람 승인)** — `schema_step5.sql` 로 `conflict_simulations` 에 `parcel_id`(FK→`booth_candidates.id`)·`facility_type`·`result_json` 가산, 기존 `css_score`·`css_vector`·`candidate_land_id`·시나리오 1칸은 **채운다**(NOT NULL 완화 안 함). `debate_logs` 테이블 신설(발화 1건=1행). `verified_precedents` 는 **반대로 DB 이름이 맞아서** ORM 을 고쳤다(`parcel_id=simulation_id` 는 오기였다). 쓰기는 `_persist_simulation()` 으로 분해했고 **읽기 경로는 한 줄도 안 고쳤다.** 실측: 토론 299.9s → `conflict_simulations` 1행 · `debate_logs` 14행 · `/results/1` 정상 · `/report/1` 86,260 bytes PDF. 상세: `배포후_작업일지\20260809_DB_현재구조_정리.md` · `20260808_…_WinError5.md` §B |
| 🔴 **실패를 기록하는 코드가 같은 함수로 실패한다** | `r_20260808_001` 이 시작 **같은 초**에 죽었는데 `status.json` 엔 25분 뒤 `_reap_orphans` 의 일반 메시지만 남았다. 진짜 사인은 `status.json.7652.tmp` 에만 있었다 — `PermissionError [WinError 5]` on `os.replace`. `_write_status`(`:161`)는 **쓰기만** `_IO_LOCK` 인데 `read_status`→`_reap_orphans`(`:209`)가 `runs/*/status.json` 39개를 **락 밖에서** 연다(프런트 폴러 2개 × 초당 ~1.2회). 더 나쁜 건 `finally`(`:611`)가 **같은 `_write_status`** 를 써서 실패 기록도 못 하고 `_ACTIVE.pop`(`:613`)까지 못 간다 → 그 도메인은 **재시작 전까지 409**. `_ACTIVE` 판정이라 **파일을 손으로 고쳐도 안 풀린다** | 정리 코드는 **터질 수 있는 호출 뒤에 두지 않는다** — 자원 반납을 기록보다 먼저 한다. Windows `open()` 은 `FILE_SHARE_DELETE` 를 안 줘서 **읽는 중에도** `os.replace` 가 터진다(예상했던 `WinError 32` 가 아니라 **5**). 쓰기끼리만 직렬화하는 건 방어가 아니다. `runs/` 가 쌓일수록 확률이 오른다(사고 당시 39개·1.3GB). **✅ 2026-08-08 수정됨** — `os.replace` 재시도(8×25ms, 끝내 안 되면 `raise`) + `_ACTIVE.pop`(`:642`)을 `_write_status`(`:643`) **앞으로**. 폴러 2개·69회 폴링으로 사고 조건 재현하며 완주, 픽스처 57/57. **✅ 2026-08-09 원인 제거** — 전수 스캔을 `read_status` 에서 떼어내 **부팅 1회**로 옮겼다(`app/main.py` lifespan → `reap_orphans()`). 판정식이 `started_at < _SERVER_BOOT` 라 **답은 부팅 시점에 이미 고정**이다 — 폴링마다 부를 이유가 애초에 없었다. `start_run` 쪽 호출도 뺐다(그 시점엔 무조건 no-op) |
| **선언만 있는 ORM** | 모델 17개 중 **13개가 `app/db/models/` 밖에서 참조 0회**. 공간 13종은 테이블이 비어 있고 파이프라인은 파일로 읽는다. "모델이 있으니 적재돼 있겠지" 로 읽힌다 | 참조 횟수를 센다. **있는 것과 쓰이는 것은 다르다** |
| **읽기인 줄 알았는데 쓰기** | 검증하려고 별도 프로세스에서 `pipeline_runner.read_status()` 를 부르면 그 안의 **`_reap_orphans()`** 가 돈다. 새 프로세스는 `_SERVER_BOOT` 가 now 라 **남이 실행 중인 run 을 `failed` 로 닫는다.** 2026-08-05 23:16, 프런트가 돌리던 `r_20260805_022` 를 밟기 직전에 멈췄다 | 재시작만 위험한 게 아니다. **out-of-process 로 러너 함수를 부르기 전에 `runs/*/status.json` 을 직접 읽어 활성 run 을 센다.** 함수명이 `read_` 여도 부작용이 있을 수 있다. **✅ 2026-08-09 해소** — `read_status` 는 이제 순수 읽기다. 다만 **`reap_orphans()`·`start_run` 은 여전히 쓴다** — 습관은 유지할 것 |
| 🔴 **안 고친 주석이 남의 요구사항이 된다** | 2026-08-04 에 `pdf_service.py`·`report_template.html` 이 잠깐 지워졌던 시점을 보고 `main.py:38`·`simulations.py:806` 에 "화면6 은 두 파일 복구 + **weasyprint(GTK3)** 필요"라고 적었다. 파일이 복구된 뒤에도 **주석만 남았다.** 실측하니 **셋 다 거짓** — 두 파일 다 있고, `pdf_service.py` 는 weasyprint 가 아니라 **playwright(chromium)** 를 쓰며(weasyprint 코드 참조 **0회**), 실제로 **33,335 bytes `%PDF-`** 가 나온다. 그 사이 프런트 작업목록에 **"weasyprint GTK 미설치로 화면6 막힘"** 이 요구사항으로 올라와 있었다 | 주석에 **상태**를 적는 순간 상하기 시작한다 — 상태 대신 **이유**를 적고, 상태를 적었으면 되돌리는 커밋에서 **같이 지운다.** 남이 내 주석을 근거로 계획을 짠다(원칙 4·5). 「없다/안 된다」를 적을 땐 **되돌아왔을 때 누가 지울지**까지 생각한다 |
| 🔴 **주석이 기능을 껐다** | 위 항목의 두 번째 사례(2026-08-09). `vector_db.retrieve_similar_statutes` 가 `facility_type` 을 **인자로 받아만 놓고 안 썼다.** 그 자리 주석은 "시설 종류별 조례가 metadata 로 분류되어 있지 않으므로 강제 필터링 제거" 였는데 **실측하면 거짓**이다 — 222청크 전부 태그가 있고 값도 갈려 있다(흡연부스 178 · 전기차충전소 44). `simulations.py:439` 는 그 값을 정확히 넘기고 있었고, 데이터팀 `ingest_statutes.py` 는 "서비스가 항상 필터를 건다"를 전제로 문서별 태깅까지 해뒀다 — **한쪽만 빠져 있었다.** 결과: 흡연부스 토론 상위 15건 중 **4건이 전기차충전소 조례**. 안 터지고 근거만 틀린다 | 주석이 "없어서 껐다"고 말하면 **그 없음을 실측한다**(원칙 5). 인자를 받고 안 쓰는 함수는 **호출자가 이미 그 기능을 기대하고 있다**는 뜻이다 — 시그니처와 본문이 어긋나면 본문이 아니라 **왜 어긋났는지**를 본다. **✅ 2026-08-09 복구** — 필터 복구 + 필터로 0건일 때 무필터 재조회로 「태깅 어긋남 ↔ 조례 없음」을 로그에서 구분 + `delete_statute_chunks()` 신설(재업로드 시 옛 청크가 남아 같은 조문이 두 번 인용된다). ⚠ `cmetadata` 는 `jsonb` 가 아니라 **`json`** 이다(직접 SQL 은 `->>`). PGVector `filter=` 는 정상 동작 |
| **대조기가 없는 키를 읽음** | `report.json` 실제 키는 `counts`·`data_gap`·`topn` 인데 `후보수`·`gap`·`topN` 으로 읽어 `None == None`·`[] == []` 로 **전 항목 통과**. 아무것도 안 본 채 초록불이 떴다 | 비교 항목이 **비면 멈춘다**(`SystemExit`). 가짜 초록불은 회귀보다 나쁘다 — 그 뒤 모든 판단의 근거가 된다 |
| **브랜치마다 따로 돌린 ruff** | 2026-08-06 통합에서 충돌 8건 중 **7건이 로직이 아니라 포맷**이었다. 브랜치 둘이 각자 `ruff format` 을 돌려(455b1f6 / 6d40676·dd058ae) 같은 코드가 서로 다른 모양이 됐다. 정본 `gam2_weight_model.py` 는 1714줄 ↔ 1439줄로 갈렸는데 **의미 차이는 0**이다. 줄 수만 보면 대형 개편으로 읽힌다 | 충돌 파일은 줄 수가 아니라 **AST 로 대조**한다. 자리표시자 없는 `f""` → `""`, `import a, b` 분해, 미사용 import 제거까지 정규화하면 남는 게 진짜 차이다. 이번엔 그러고 나니 **본문이 전부 동일**했고 진짜 충돌은 `test_api_client.py` 1건뿐이었다 |
| 🔴 **인증 실패를 「크래시 탓」으로 오진** | 2026-08-07 로더가 `password authentication failed for user "postgres"` 로 죽었다. 로그에 `not properly shut down` + `invalid record length` 가 같이 있어 **WAL 손상으로 SCRAM 검증자가 깨졌다**고 결론짓고 `ALTER USER ... PASSWORD 'postgres'` 로 되돌렸다. **틀렸다.** 실제로는 그 전에 **외부 침입**이 있었다(08-07 11:39 무차별 대입 20회 → 14:12 `DROP DATABASE omnisite` → 랜섬 노트). 되돌린 비번이 다시 `postgres` 라 **문을 다시 열어준 셈**이다. 그때도 `docker logs` 를 읽었지만 `FATAL` 만 grep 해서 `sh: 1: wget: not found` 줄을 못 봤다 — **찾는 패턴 밖의 증거는 보고도 못 본다** | 인증 실패가 **연속으로** 뜨면 먼저 **누가 시도했는지**를 본다: `docker logs <c> \| grep -E "authentication failed\|sh:\|DROP DATABASE"`. 서버 내부(WAL·SCRAM)만 후보에 올리면 **바깥에서 들어온 가능성**이 아예 안 보인다. 상세: `04_이슈\2026-08-08_로컬DB_랜섬웨어_침해사고.md` |
| 🔴 **컨테이너 포트 기본 노출** | 위 침입의 **진짜 원인.** `ports: "5432:5432"` 는 앞에 IP 가 없으면 **`0.0.0.0` = 전 인터넷 공개**다. 비번은 `postgres`, Redis 는 인증 자체가 없었다. 두 조건이 겹치면 뚫리는 데 필요한 건 **시간뿐**이다. `.env.example` 에도 `postgres:postgres` 가 예시로 박혀 있어 **예시값이 곧 실사용값**이 됐다 | `ports` 는 항상 **`127.0.0.1:` 을 붙인다.** 비밀번호는 compose 에 기본값을 두지 않고 `${VAR:?메시지}` 로 **없으면 기동을 실패시킨다** — 기본값이 있으면 빠뜨렸을 때 조용히 약한 암호로 뜬다(원칙 1). 점검: `netstat -ano \| grep LISTENING \| grep -E ":5432\|:6379"` 에 `0.0.0.0` 이 보이면 열린 것. **✅ 2026-08-09 — 앱 쪽 기본값도 제거**했다. compose 만 막고 앱에 `postgresql://postgres:postgres@…` 가 남아 있으면 막은 게 아니다: `app/config.py`(`_require_env`) + `scripts/load_{region_boundaries,cadastral,domain_from_gpkg}.py` 4곳 전부 **없으면 기동 실패**다 |
| **libpq 무타임아웃** | 위 건에서 도커가 아예 죽었을 땐 `psycopg.connect(DSN)` 이 **260초**를 기다렸다(실측). 원시 TCP 는 2초에 `ConnectionRefused` 인데 libpq 만 안 끝난다 → 사용자에겐 "느린 스크립트"로 보인다. 60초 타임아웃으로 재고 "무한 대기"라 단정한 것도 틀렸다(원칙 5) | `connect_timeout` 을 **명시**한다(`scripts/load_region_boundaries.py` 는 10초, 실측 12초에 종료). 기다림은 실패로 드러나야 한다 |
| **요약표 한 칸만 읽음** | 2026-08-08. 데이터팀 스키마(#205)를 보고 "크로스워크 코드 3체계를 `region_code` 하나로 접었다"고 되묻기를 적었다. **틀렸다** — `schema_region_boundaries.sql:72-84` 에 세 컬럼이 다 있고 인덱스도 2개다. 이슈 요약표의 `PK/키` 칸만 읽고 **같은 표의 `주요 컬럼` 칸도, 우리 저장소 루트에 있는 DDL 도 안 열었다.** 같은 건에서 "3,559−3,555=4건이 조용히 사라진다"도 틀렸다: 미매칭은 **6건**(뺄셈≠미매칭, 부분집합이 아니다)이고 전부 서울 밖이며 **#205 본문이 이미 답해놨다** | 남의 요약을 근거로 삼지 않는다. **원본(DDL·코드)이 우리 저장소에 있으면 그걸 연다.** 집합 차이는 뺄셈이 아니라 **교집합·차집합을 실제로 센다.** 되묻기를 적기 전에 **상대 본문을 끝까지 읽는다** — 이미 답한 걸 물으면 문서 전체를 안 믿게 된다 |
| **stdout 재래핑이 `-u` 를 무력화** | 같은 스크립트가 `sys.stdout = io.TextIOWrapper(...)` 로 다시 감싸 **`python -u` 가 안 먹었다.** 백그라운드로 돌리니 출력 파일이 끝까지 비어 진행 상황을 알 수 없었다 | 재래핑할 땐 `line_buffering=True` 를 같이 준다. 진행이 안 보이면 멈춘 건지 도는 건지 구분할 수 없다 |
| 🔴 **MOCK 모드가 DB 실패를 가린다** | 동현님이 프런트에서 화면5 를 **성공적으로 시연**했는데 `conflict_simulations` 는 **0행**이었다. 모순이 아니다 — 시연 시점(`100c8a3`)은 `USE_MOCK_DB = True` 였고 그 분기의 본문은 `print("[MOCK 모드] DB 저장 우회 완료")` **한 줄이 전부**다. DB 를 아예 안 건드렸다. `ff1f873`(08-06)에서 `False` 로 바뀌며 드러났다. "원래 되던 게 지금 DB 작업 때문에 깨졌나?" 로 읽힌다 | **"돌아가는 걸 봤다"는 어느 코드가 돌았는지까지 확인해야 근거가 된다.** 시연 시점의 커밋·플래그를 먼저 본다. 모의 분기는 "우회했다"를 **산출물에도** 남겨야 한다 — 콘솔 print 는 사라진다(원칙 4) |
| 🔴 **감리 결과 테이블이 통째로 없었다** | 화면5 토론이 **첫 줄에서** `UndefinedTableError`. `schema_cleaned_data.sql` 의 **뒤쪽 §17~19**(`national_owned_properties`·`rag_feedback_log`·`audit_rules`)가 실 DB 에 없었다 — 08-07 `DROP DATABASE` 후 재생성이 중간에 끊긴 흔적이다. **앞쪽은 다 있어서** 테이블 목록을 훑으면 정상으로 보인다 | DB 재생성 뒤엔 개수가 아니라 **DDL 의 테이블명 집합 ↔ `get_table_names()` 를 차집합으로 대조**한다. `scripts/create_missing_tables.py`(dry-run 기본, `--yes` 로 적용) |
| 🔴 **FK 는 DB 가 아니라 metadata 에서 풀린다** | `create_all` 이 `NoReferencedTableError: transit_passengers.station_id could not find table 'transit_stations'`. 그런데 그 테이블이 DB 에 **있든 없든 똑같이 죽는다** — SQLAlchemy 는 `Base.metadata` 안에서 FK 대상 `Table` 객체를 찾는다. ORM 에 선언만 없으면 나는 에러다. 🔴 **같은 날 두 번 밟았다** — `ConflictSimulation.candidate_land_id` 에 `ForeignKey("candidate_lands.id")` 를 붙였는데 그 테이블은 ORM 선언이 없다. 이번엔 `create_all` 이 아니라 **flush 시점**에 터졌고, 하필 **5분짜리 토론을 다 돌린 뒤**였다 | 두 갈래다. **만들어야 하면** `metadata.reflect(only=[...])`(생성 아님). **읽고 쓰기만 하면** ORM 에서 `ForeignKey` 를 아예 빼고 평범한 `Integer` 로 둔다 — 제약은 실 DB 에 걸려 있으므로 무결성은 그대로다. **ORM 이 FK 를 아는 것과 DB 가 FK 를 거는 것은 별개다.** 에러 문구가 "테이블이 없다"여도 **DB 를 보라는 뜻이 아니다** |
| 🔴 **필드 하나로 두 의미를 쓰면 소비 코드가 조용히 틀린다** | `audit_rules.facility_type` 이 「배제 대상 시설(금연구역)」과 「입지를 정하려는 대상 시설(흡연부스)」을 겸했다. 그래서 `facility = facility_types[0]` 은 **대상 시설을 "금연구역"** 으로 만들고, `factor_name = r.facility_type or "요인"` 은 positive_factor **8개를 dict 키 하나로 뭉갠다**. 둘 다 예외 없이 값만 틀린다 | 의미가 둘이면 **컬럼도 둘**이다(`target_facility` / `facility_type` / `factor_name`). 산출물이 한 자리에 두 의미를 섞어 쓰면(`source` = 조항 문자열 or 리터럴 `human_confirmed`) **원문을 유지하고 임의로 쪼개지 않는다** — 쪼개면 없는 정보를 지어낸다(원칙 5) |
| 🔴 **「테스트용 폴백」이 본선에서 돌고 있었다** | `gam2_audit_judgment_test.py:2023` 의 `DOMAIN = {"facility":"흡연부스","region":"용산구"}` 는 주석에 **"테스트용 폴백 기본값"** 이라 적혀 있었지만 실제로는 `fac.get("region") or DOMAIN["region"]` 형태로 **실행 경로 3곳**(`gam2_run_pipeline.py:191` 본선 · real · mock)에서 쓰였다. 성동구 입력에서 지역이 안 잡히면 조용히 **용산구** 조례·상위법을 검색한다. `enrich_with_search(region="용산구")` 도 같은 함수가 **`facility` 는 파일에서 읽으면서 `region` 만** 기본값을 썼다. 더 나쁜 건 `simulations.py` 의 좌표 폴백 — 후보점 조회가 실패하면 (37.534, 126.994) "이태원동 123-45 (테스트용)" 으로 갈아끼우고 **5분짜리 LLM 토론을 그대로 완주**해 DB 에 저장했다. 프런트엔 정상 결과로 보인다 | `or <기본값>` 은 **폴백이 아니라 분기**다 — 주석이 "테스트용"이라고 말해도 호출자를 세어 본다. 한 함수 안에서 값 A 는 파일에서 읽고 값 B 는 기본값을 쓰면 **B 가 A 와 다른 도메인**을 가리킬 수 있다: 출처를 같은 곳으로 맞춘다. **✅ 2026-08-10 제거**(사람 승인) — `require_region()` 신설(비면 `SystemExit`) · `enrich_with_search(region=None)` → `facility_inference.region` · 좌표 폴백은 `CandidateNotFound` → SSE `error_code: CANDIDATE_NOT_FOUND`. 픽스처 57/57 유지 |
| **`docker exec` 에 `-i` 가 없으면 stdin 이 무시된다** | `docker exec <c> psql … <<'SQL'` 이 **출력도 없고 exit 0** 인데 테이블이 안 생겼다. 성공으로 보인다 | heredoc·파이프로 SQL 을 넘길 땐 **`docker exec -i`**. 그리고 실행 후 `\d` 로 **결과를 확인**한다 — rc=0 은 "명령이 돌았다"만 뜻한다 |
| 🔴 **`localhost` 가 IPv6 로 먼저 풀린다** | `GET /upload/regulations` 가 **130초**. 어제 일지엔 "PGVector 초기화 비용"이라 적었는데 **틀렸다** — `getaddrinfo("localhost")` 가 `::1` 을 먼저 주는데 2026-08-09 보안 조치로 docker 가 **`127.0.0.1:5432`(IPv4 전용)** 에만 바인딩돼 아무도 안 듣는다. OS TCP 재시도를 다 쓰고 IPv4 로 폴백하는 데 130초. **동기 `psycopg.connect` 만** 걸리고 asyncpg 는 멀쩡해서 `/candidates`·`/stream` 은 빠르다 → "그 엔드포인트만 느리다"로 읽힌다 | DSN 에 **`127.0.0.1` 을 쓴다**(호스트명이 아니라 주소). 느린 엔드포인트를 만나면 "무거운 초기화"로 단정하기 전에 **어느 드라이버가 쓰이는지**부터 본다 — 같은 DB 인데 한쪽만 느리면 이름 해석이나 소켓 문제다. **✅ 2026-08-10 적용** — `.env` 만 고치면 다음 사람이 다시 `localhost` 를 쓴다. `app/config.py:_normalize_dsn()` 이 DSN 의 `localhost` 를 `127.0.0.1` 로 **고쳐 쓰고 경고 로그를 남긴다**(조용히 안 바꾼다). 컨테이너 서비스명(`postgres`)·이미 IP 인 것·`localhost.example.com` 은 안 건드린다. 같이 넣은 `DB_CONNECT_TIMEOUT`(기본 10초)은 **드라이버마다 인자 이름이 다르다**: asyncpg `timeout` / libpq·psycopg `connect_timeout` / PGVector 는 `engine_args={"connect_args": …}`. 복붙하면 조용히 무시된다. 실측 130초 → **0.151초** |
| 🔴 **읽는 쪽에 도메인 필터가 없다** | `_select_audit_rules`(`simulations.py:141`)가 `WHERE target_facility = :f` **만** 본다. `audit_rules` 에 `domain` 컬럼이 있고 적재기는 `(domain, run_id)` 단위로 교체하는데 읽기가 안 거른다 → 같은 시설을 쓰는 도메인이 둘이면 **26행**(실측: 흡연 13 + 흡연_E2E 13)이 되어 감리 근거가 두 배, AHP 가중치가 갈라진다. 안 터지고 값만 틀린다 | 적재 단위와 조회 단위를 **같게** 맞춘다. `domain` 은 `/stream` 요청이 아니라 **`booth_candidates.domain`**(이미 `parcel_id` 로 조회하는 행)에서 가져오면 프런트 계약을 안 바꾼다. **✅ 2026-08-10 적용** — `_select_audit_rules(db, facility_type, domain, run_id)`(같은 날 `run_id` 까지 추가, 아래 항목). 요청 본문으로 안 받은 이유가 하나 더 있다: 파라미터로 받으면 흡연 후보점에 재활용 도메인을 넘길 수 있다. **같은 행에서 뽑으면 둘이 어긋날 수가 없다.** 그래서 후보점 조회를 감리 조회 **앞으로** 옮겼고, `domain`·`run_id` 가 NULL 이면(손입력 1행) `CandidateNotFound` 로 멈춘다 |
| 🔴 **400 인데 절반은 이미 적용됐다** | 다중 파일 업로드에서 `<원본>.txt`(추출 캐시)를 올려 400 이 났는데, 그 전에 앞 파일은 **저장되고 벡터 청크 18개까지 적재**돼 있었다. 호출자는 400 만 보고 "아무 일도 없었다"로 읽는다(원칙 4) | 여러 건을 받는 엔드포인트는 **전건 검증 후 저장**(2-pass)이거나, 아니면 응답에 **어디까지 됐는지**를 적는다. 실패 응답을 받으면 목록을 **다시 조회**해서 확인한다 |
| **`--auto-*` 기본값이 픽스처 조건과 다르다** | 업로드 경로로 STEP3 를 `--auto-radius --auto-weight` 만 주고 돌렸더니 `scale=minmax`·`decay=null` 이 됐다(픽스처는 `log`·`gaussian`). 집계반경도 llm 제안값(150→200 등). 그 결과 **Top-N 이 통째로 다른 필지**가 됐다 — LLM 이 변덕부린 것처럼 보인다 | 회귀 대조를 할 땐 `--decay gaussian --scale log --radius …` 를 **명시**한다. 값이 갈리면 LLM 을 의심하기 전에 **`weight_set.json` 의 `scale`·`decay`·`radius_source` 를 대조**한다. 실측하니 `w_human` 6개는 소수 4자리까지 같았다 — 갈린 건 실행 옵션 하나였다 |
| 🔴 **다리를 하나만 놓았다** | full 모드 1회차(`r_20260810_001`)가 `succeeded` 인데 화면5 가 0.4초에 죽었다. 적재 칸이 `booth_candidates` 만 넣고 `audit_rules` 를 안 넣었다 — 화면5 는 **어디를**(후보점)과 **무엇을 근거로**(감리 규칙) **둘 다** 읽는다. 목록은 20행이 정상으로 뜨니 "후보는 있는데 토론이 안 된다" 로 보인다. 파이프라인 산출물을 DB 로 옮기는 지점을 셀 때 **STEP4 만 세고 STEP1 을 안 셌다** | 화면 하나가 읽는 테이블을 **전부** 센다. 산출물→DB 다리는 STEP 개수만큼 있을 수 있다. 적재를 **한 칸에 몰지 않는다** — 두 프로세스를 한 칸에 넣으면 어느 쪽이 실패했는지 진행 표시에서 사라진다. ✅ 2026-08-10 `적재-감리`·`적재-후보` 두 칸으로 분리 |
| 🔴 **적재 키와 조회 키가 다르다** | 위 건을 고친 뒤 같은 도메인을 두 번 돌리니 `audit_rules` 가 **26행**(hard 10 · positive 16, 고유 요인명은 14). 적재기는 `(domain, run_id)` 를 교체하는데 `_select_audit_rules` 는 `(domain, target_facility)` 만 본다 → 근거가 두 배, AHP 가중치가 묽어진다. **예외가 안 난다.** `booth_candidates` 는 같은 사고가 안 나는데, 조회(`/candidates`)가 최신 run 하나만 돌려주기 때문이다 — **짝이 한쪽만 맞춰져 있었다** | 테이블마다 **적재 단위 ↔ 조회 단위**를 짝지어 적어둔다. 한쪽을 고치면 다른 쪽을 같이 본다. 🔴 **어긋났을 때 어느 쪽을 고칠지가 진짜 갈림길이다.** 나는 처음에 "적재기를 domain 단위 전량 교체로" (A안) 권고했다 — **맞는 쪽(저장)을 틀린 쪽(조회)에 맞추는** 안이었다. `audit_rules` 는 STEP1 **산출물**이고 두 적재기는 이미 `(domain, run_id)` 로 옳게 교체하고 있었다. 실측 없이 "고치기 쉬운 쪽"을 고르면 방향이 뒤집힌다. **✅ 2026-08-10 해소**(B안, 사람 승인) — 조회에 `run_id` 추가(값은 `booth_candidates` 행에서, 요청으로 안 받는다) + 정본 run_id 어휘 통일: 예전엔 STEP 폴더 이름을 넣어 `audit_rules='step1_output'` ↔ `booth_candidates='step4_output'` 로 갈렸는데, run_id 는 "어느 STEP 폴더에서 왔나"가 아니라 **"어느 실행에서 나왔나"** 다 → 양쪽 다 **`'정본'`**(기존 33행 마이그레이션). 실측: 26행 저장 ↔ **13행 조회**, 규칙 없는 run 은 **0.56초**에 정지(고치기 전엔 남의 run 근거로 39.5초 완주). 계약 §6·§8-9 |
| **입력 파일 이름만 바꿔도 모드 하나가 죽는다** | `fixture` 모드가 STEP2 에서 10.6초 만에 `FileNotFoundError: …\흡연\fixture\profiles.json`. 누군가 그 파일을 `fix_profiles.json` 으로 바꿔놓았다(sha256 은 동일). `gam2_audit_judgment_test.build_fixtures()` 는 없으면 만들어 주지만 `gam2_clean_data.py` 는 안 만든다 → **STEP1 을 안 도는 fixture 모드에서만** 드러난다. `.gitignore` 대상이라 clone 에도 없다 | 모드마다 **첫 칸이 다르면 선행 파일도 다르다.** 한 모드가 돈다고 다른 모드가 도는 게 아니다. 재생성: `python app\services\gam2_profile.py data_임시\<도메인>` |
| 🔴 **캐시로 바꿔치기하면 업로드가 안 보인다** | PR #220 이 부팅 시 `data_임시/흡연/` 을 Redis 에 바이트로 시딩하고, `_child_env` 가 **Redis 에 키가 있으면** `OMNISITE_DATA_ROOT` 를 `runs/<id>/raw_data` 로 갈아끼우게 했다. 그런데 화면1 업로드(`upload.py:109`)는 **디스크**에 쓴다 → 부팅 이후 올린 파일은 파이프라인에 **영원히 안 들어간다**(서버를 다시 띄우기 전까지). 예외가 안 나고 값만 옛것이 된다. 2026-08-10 에 업로드→STEP0~4→토론→PDF 를 9분 11초로 관통한 경로가 통째로 무력화된다 | 데이터 경로를 **가로채는** 최적화는 「원본이 바뀌는 지점」을 전부 세고 나서 넣는다. 캐시는 **쓰는 쪽도 같은 캐시를 봐야** 캐시다 — 한쪽만 보면 그건 캐시가 아니라 **분기**다. ✅ 통합하되 **배선하지 않았다** — `seed_redis.py` 로 사람이 명시적으로 넣고 꺼낸다 |
| 🔴 **무TTL 키가 캐시 정책을 죽인다** | 위 시딩은 키에 TTL 을 안 줬다. compose 는 `--maxmemory-policy volatile-lru` 라 **TTL 있는 키만** evict 한다 → 원본 바이트(흡연 `data/` 만 537MB · 단일 최대 279MB)가 한도를 채우면 Redis 가 **모든 쓰기를 OOM 으로 거절**한다. 지오코딩·지목 캐시가 같이 죽는데, 원인은 "캐시를 보존하려고" 고른 정책이다 | 무TTL 로 넣을 값은 **크기 상한이 있는 것만**이다. `--maxmemory` 를 올리는 건 시간을 버는 것이지 고치는 게 아니다. 시딩 키는 TTL 필수(`seed_redis.py --ttl`, 기본 24h) |
| 🔴 **부분 복원이 조용히 통과한다** | 같은 PR 의 복원 함수는 Redis 접속 실패·키 누락을 전부 `warning` 으로 넘기고 `{}` 를 반환했다. 호출자는 「스테이징 결과가 비었으면 안 쓴다」만 봤다 → **반만 복원되면 그대로 주입**되고 파이프라인이 일부 데이터셋으로 완주한다. 지표가 0 이 아니라 **작아질 뿐**이라 안 걸린다 | 복원은 **전량·크기 대조**가 있어야 복원이다. 매니페스트(`__manifest__` 키)에 (상대경로 → 바이트수)를 남기고 하나라도 어긋나면 `raise`. 실측 확인: 키 1개를 지우고 재복원 → `RuntimeError: 매니페스트 3개 중 2개만` |
| 🔴 **확인 문구가 삭제 범위를 축소해서 말한다** | `reset_db_redis.py` 는 `input()` 하나로 `public` 스키마를 통째로 DROP 하는데 문구는 "**파이프라인** 데이터가 삭제됩니다" 였다. 실측하면 **39테이블 688.1MB** 이고 지적도(`cadastral_lands`)·경계 3종처럼 다시 만드는 데 몇 시간 걸리는 것이 대부분이다. 게다가 실패를 `print` 로 삼켜 rc=0 으로 끝난다 | 파괴적 도구는 **지울 것을 전부 나열한 뒤** 승인을 받는다. 범위를 좁게 말하는 확인은 확인이 아니다(원칙 4). 저장소 관례대로 **계획만 출력이 기본**, `--yes` + `'DELETE'` 타이핑으로만 실행 |
| 🔴 **표시값인 줄 알았는데 입력값이었다** | `simulations.py` 가 토론 시작 상태를 `css_pro/css_con = random.choice(["LOW","MEDIUM","HIGH"])` 로 잡았다. 화면에 뜨는 지표라 "표시가 흔들린다" 로 읽히지만, 이 값은 `graph.pro_node`·`con_node` 가 **1라운드 시스템 프롬프트를 고르는 키**다(`css_high.txt` "충분한 근거 없이는 양보하지 마세요" ↔ `css_low.txt` "가능한 빠르게 합의점을 찾으세요"). 라운드 2부터는 수용도로 결정론 재매핑되지만 **1라운드가 이후 전부의 입력**이라 결과 시나리오까지 갈린다. 같은 후보지·같은 감리 근거로 돌려도 매번 다르다 — 안 터지고 값만 틀린다 | 프런트에 나가는 값을 볼 때 **그 값이 어디로 또 흘러가는지**를 센다. "표시용"이라는 판단은 소비자를 세어 본 뒤에만 할 수 있다. **✅ 2026-08-10 제거**(사람 결정 A안) — `INITIAL_CSS_LEVEL = "HIGH"` 로 고정. 새로 정한 값이 아니라 **이미 세 곳에 선언돼 있던 기본값**이다(`state.get("css_pro","HIGH")` · `CSS_PROMPT_TEMPLATE` 폴백 · `_map_css_by_score(0.0)`). 값과 출처는 `result_json["determinism"]` 에 남긴다 |
| 🔴 **점수가 내용이 아니라 라운드 수를 따라 올라갔다** | 위 건을 추적하다 나온 두 번째 결함. `evaluator.txt` 와 `graph.py` 의 호출 문구가 **"조금이라도 타협 여지가 생겼다면 무조건 이전 점수보다 상향"** 이었다 — 한 방향 지시라 근거 없이 같은 말을 되풀이해도 점수가 오른다. 토론자 프롬프트도 짝을 이뤄 "**라운드가 거듭될수록** 양보를 모색하라" 였다. 최종 시나리오 A/B/C 는 이 점수로 갈린다(`reporter.txt`) → **토론 내용과 무관하게 라운드만 채우면 A 가 나온다** | 평가 프롬프트에 **한 방향만** 허용하면 그건 평가가 아니라 카운터다. 상향·하향·유지를 다 열고, **무엇이 점수를 움직였는지 근거 대목을 반환**하게 한다. 생산자(토론자)와 소비자(평가자) 프롬프트는 **같이** 고친다 — 한쪽만 고치면 평가할 내용 자체가 안 생긴다. ✅ 2026-08-10 수정(사람 지시) |
| 🔴 **자동 확정이 flag 를 안 남겨 HITL 화면에서 사라졌다** | `enrich_hitl_flags` 에 배제반경 자동 확정이 **둘** 있었다: ⓐ 사람이 한 번 답한 값을 run 폴더 **밖**(`search_cache/<prefix>_exclusion_radius_cache.json`, 키=`facility_type`)에 적어두고 다음 실행에서 묻지 않고 채움 ⓑ 조례 텍스트에 시설유형과 반경 숫자가 **둘 다 substring 으로 있으면** `confirmed=True`. 둘 다 **flag 를 안 만든다** → 게이트A 질문 목록에 아예 안 뜬다. 실측: 픽스처 배제 5건 중 06 지하철역·07 버스정류소는 `hitl_flags []` 라 **HITL 인데 사람이 볼 기회가 없었다.** ⓑ 는 「제5조의 10m 가 이 시설 얘기인지」를 모른다 — **근거는 되지만 확정은 아니다** | 질문 목록을 flag 같은 **부산물**에서 만들면 그 부산물을 안 만드는 경로가 곧 **구멍**이 된다. 목록은 **본체**(여기선 `hard_exclusion` role)에서 만들고 flag 는 부가정보로만 쓴다. **✅ 2026-08-10 제거**(사람 지시 · 계약 §7-7) — 캐시 삭제(`load/save_to_exclusion_cache`·`EXCLUSION_CACHE_PATH`·json 3개) · 조례 대조는 `제안값`·`출처`·`근거_시설_일치` 로 **강등** · 게이트A 가 flag 없는 role 도 질문으로 만들고 `_apply_audit` 이 답 적을 flag 를 만든다 · 미확정인 채 STEP2 진입은 `SystemExit`(`assert_exclusions_confirmed`). 확정은 **그 run 안에서만** 유효하다. `hitl` 은 `_prepare_dirs` 가 **run 안 사본만** 되돌린다(원본 픽스처 무변경) — `fixture` 는 게이트가 없으니 되돌리지 **않는다**(되돌리면 STEP2 가 멈춘다). 47/47 · 픽스처 57/57 |
| 🔴 **검증이 최상위 키에서 멈췄다** | 위 건을 막고 나서 「배제 승격」이 막다른 길이 됐다. `apply_intent_answer(…, choice=3)` 은 `배제반경_m: null · confirmed: false` 인 `hard_exclusion` 을 **새로** 만드는데, 게이트A 질문 목록은 **답변 전에** 만들어져 그 role 의 질문이 없다 → `exclusions` 로 답하면 `400 게이트에 없는 대상`. 그런데 `intents` 항목에 `radius_m` 을 실으면 **200 인데 값이 버려졌다** — `_apply_audit` 이 payload **최상위 키**만 화이트리스트로 막고 **항목 내부는 안 봤다.** 프런트는 성공으로 읽고 run 은 STEP2 에서 죽는다. 같은 이유로 `exclusions` 의 `radius_m` 오타(`radius_mm`)가 「건너뜀 = 미확정 유지」로 읽혔다 | 화이트리스트는 **한 겹만 치면 안 친 것과 같다** — 바깥을 막고 안을 안 보면, 거절당할 줄 알았던 필드가 조용히 사라진다. 「답이 새 대상을 만드는」 질문은 **그 답과 같은 항목에서** 후속 값을 받아야 한다. 질문 목록이 정적이면 나중에 물을 자리가 없다. **✅ 2026-08-10 해소**(사람 결정 · 계약 §7-7-1) — `intents` 가 `radius_m` 을 받는다(`choice 3` 에서만, 다른 choice 면 400) · 규약은 `exclusions` 와 동일(값=확정 · `null`=면 배제 확정 · **키 생략=미확정**) · `choices` 에 **`needs_radius`** 추가(프런트가 칸 띄울 근거) · 승격은 옛 flag 의 확정 표시를 **지운다**(안 지우면 재조회 시 `editable:false` 로 굳는다) · 세 배열 **항목 내 알 수 없는 키 전부 400**. 🔴 프런트가 여분 필드를 보내고 있었다면 그 요청은 이제 400 이다 |
| 🔴 **충돌이 안 난 파일이 우리 방어선을 지웠다** | PR #224 병합(2026-08-10). 충돌은 `app/main.py` **1건**이라 나머지는 안 봤어야 정상인데, `requirements.txt` 가 **충돌 표시 없이** auto-merge 되며 pyarrow·pyogrio 가 왜 필수인지 적은 주석과 `-c constraints.txt` 지침이 통째로 사라졌다. `main.py` 의 `include_router` 블록도 같은 이유로 PR 쪽으로 넘어가 **`/simulations`(복수) prefix 등록이 빠졌다** — 프런트 경로 5개가 조용히 404 가 될 자리다. 원리는 간단하다: **merge-base 와 우리 HEAD 가 같은 자리면** git 은 상대 변경만 적용한다. 상대 브랜치 베이스가 낡을수록 「우리가 나중에 지킨 것」이 아니라 「우리가 손대지 않은 것」이 통째로 상대 것으로 바뀐다. 같은 PR 이 `lands`·`ahp` (삭제된 파일) import 와 `try/except ImportError: None` 도 되살리려 했다 | 충돌 개수는 **위험의 크기가 아니다.** 병합 후 `git diff HEAD` 로 **수정된 기존 파일 전부**를 읽는다(신규 파일은 건너뛰어도 된다 — 겹칠 게 없다). 상대 브랜치의 merge-base 를 먼저 보고 (`git merge-base HEAD <br>`), 그 뒤 우리가 한 작업 목록을 **되돌려졌는지 기준으로** 훑는다. 주석은 코드가 아니라 잘 지워지는데, **왜 필수인지 적은 주석이 지워지면 다음 사람이 그 핀을 뺀다** |

---

## 파이프라인

```
STEP0  프로파일링          gam2_profile.py
STEP1  감리 AI + HITL      gam2_audit_judgment_test.py · gam2_audit_ops_catalog.py
       조례 로드            gam2_doc_extract.py(PDF→txt) · gam2_ordinance_select.py(조문 선별)
       상위법 검색          gam2_ordinance_acquisition.py
STEP2  정제                gam2_clean_data.py
STEP3  후보 생성            make_parcel_candidates.py
       가중치 [A][A2][R][W][B][D][E][F]   gam2_weight_model.py · run_weight_model.py
STEP4  입지 선정(MCLP)      gam4_site_select.py · gam4_spatial_ops.py · gam4_jimok.py
```

HITL 위치: STEP1(배제반경·데이터의도·지역코드) · STEP3 `[R]`(집계반경) `[W]`(가중치)
API 로는 **게이트 2개**다 — 게이트A = STEP1 끝, 게이트B = STEP3 중간(`[R]`+`[W]` 합침).
`input()` 실측: `gam2_audit_judgment_test` 4개 · `run_weight_model` 3개 · **나머지 전부 0개.**

---

## 규약

- **좌표계** 계산 EPSG:5186 / 저장 EPSG:4326
- **가중치** `seed_weight` 는 **크기만**(항상 ≥0). 방향은 `direction`(benefit/cost).
  슬라이더 `-1~+1` 은 UI 표현이고 `apply_weight_hitl` 이 경계에서 분해한다.
  부호를 `seed_weight` 에 넣으면 `normalize_matrix` 의 cost 반전과 이중으로 걸려 조용히 뒤집힌다.
- **출처 기록** 값마다 누가 정했는지 남긴다 — `radius_source` · `w_human_source` · `direction_source`
  🔴 출처는 **실행 방식이 아니라 값이 어디서 왔는지**다. `--auto-weight` 로 유도하면
  "대화형 루프를 건너뛴다"와 "사람이 확정 안 했다"가 섞인다 — 게이트 방식에선 앞만 참이다.
  자식 프로세스는 `--radius 07+02=150` 이 사람 답인지 픽스처인지 **알 수 없다** →
  호출자가 `--value-source {human,fixture,cli}` 로 선언한다. 어휘는 STEP1 의
  `human_confirmed` 를 STEP3 도 그대로 쓴다. **엔터로 제안값 승인도 확정이다**
  (그때 `source` 는 `llm` 로 남으므로 프롬프트를 띄웠는지 따로 센다).
  🔴 `--radius`/`--weight` 를 주면 **`--value-source` 는 필수**다(없으면 `SystemExit`).
  기본값을 두면 빠뜨렸을 때 조용히 새는데, 하필 `cli` 가 사람 취급이라
  **"사람이 확정함"으로 과대 기록**됐다(`r_20260805_017` 실측). 고정값이 아예
  없는 대화형 실행이면 `value_source` 는 `null` 이다 — CLI 에서 온 값이 없다.
  ⚠ `cli`=가짜가 **아니다.** 사람이 직접 치면 `cli` 는 진짜 사람이다.
  "`cli` 면 못 믿는다"는 **`runs/` 안에서만** 참이다(러너는 항상 인자를 넘기므로).
- **지역 데이터** `region_data/<지자체>/LSMD_CONT_LDREG_<시군구코드>_<연월>.shp`
  `find_region_file()` 이 **시군구코드로** 고른다(폴더명 아님). 지적도는 시군구 단위 배포.
- **조례** STEP1 감리는 **발췌**(`select_articles`), STEP2 반경/설치가부는 **전문**.
  감리는 데이터셋 수만큼 반복되므로 전문을 넣으면 4배 느려진다(성동구 실측).
- **행정코드 — 한 파이프라인에 세 체계가 같이 있다**(2026-08-05 실측, #208).
  "우리 산출물은 X 기준" 이라고 **한 마디로 답할 수 없다.**

  | 산출물 | 컬럼 | 체계 | 조인 |
  |---|---|---|---|
  | `clean_01,05~11` (공간조인) | `ADM_CD` `11030740` | **통계청** | 경계와 **직접** |
  | `clean_04` (생활인구 원본) | `행정동코드` `11170510` | **행자부** | 크로스워크 **경유** |
  | `topN`·후보 gpkg | `법정동코드` `1117012500` | **법정동**(PNU 앞10) | 코드 조인 **불가** → 공간조인 |

  법정동은 행자부/통계청의 변형이 아니라 **다른 축**이다(후암동 법정동 `1117010100`
  ↔ 행정동 행자부 `11170510`). 크로스워크에 없는 게 정상이다.
  🔴 행자부↔통계청을 직접 조인하면 **0건이 아니라 7/16 이 맞는다**(용산). 0이면 터지는데
  부분일치는 안 터진다 — "16개 중 12개" 사고가 그거였다.
  `topN` 은 **Point** 다(4326, csv 의 `경도`·`위도` 와 동일). 후보 gpkg 는 레이어가 갈린다:
  `candidates`=Point · `parcels`=Polygon. `.geojson` 이라고 폴리곤으로 단정하지 말 것.

---

## 실행

```bat
:: 진단 (LLM 호출 0회) — 전부 app\tools\ 아래다. 루트에는 없다.
python app\tools\check_loader_health.py <도메인>     :: 좌표계·행정동 조인키
python app\tools\check_ordinance_select.py <도메인>  :: 조례 조문 선별
python app\tools\check_exclusion_state.py <도메인>   :: 배제 레이어 면적
python app\tools\check_fixture.py <도메인>           :: 회귀 픽스처 대조 (S12) [--restore]
python app\tools\check_hitl_gate.py <도메인>         :: A2 — HITL 게이트 단위 57항목 (runs/ 불필요)
python app\tools\check_hitl_e2e.py <도메인>          :: A2 — fixture ↔ hitl 완주 대조 (🔴 LLM 1회)
python app\tools\check_upload_api.py                 :: 업로드 API 25항목 (in-process TestClient)
                                                     :: [--no-ingest] 벡터 적재·검색 제외 → 16항목·LLM 0회
python app\tools\check_postgis_parity.py <도메인>    :: S5 — geopandas ↔ PostGIS 술어 **값** 대조
python app\tools\bench_postgis.py <도메인>           :: S5 — 같은 술어 **속도** 대조
                                                     :: (둘 다 도커 필요. PGIS_DSN 으로 접속지 지정)
python app\tools\check_auth_dual_token.py            :: 듀얼 토큰 — 발급·refresh(RTR)·로그아웃
                                                     :: (PR #221. 검증이 InMemoryDB 목이라 실 DB 확인은 별도)

:: 운영 도구 (PR #220 통합 — 전부 수동. 자동으로 안 돈다)
python app\tools\get_cache.py {geocode|jimok|list} [질의]   :: Redis 캐시 조회(읽기 전용)
python app\tools\seed_redis.py seed  <도메인> [--ttl 86400] :: 도메인 폴더 → Redis 바이트
python app\tools\seed_redis.py stage <도메인> <복원폴더>     :: Redis → 폴더 (매니페스트 전량 대조)
python app\tools\reset_db_redis.py                          :: 계획만 출력. --yes + 'DELETE' 입력으로 실행
                                                     :: 🔴 public 스키마 **전부** DROP 한다(실측 39테이블 688MB)

:: 파이프라인
python app\services\gam2_run_pipeline.py <도메인> "<지역> <시설> 부지 선정"
python app\services\gam2_audit_judgment_test.py hitl <도메인>
python app\services\gam2_clean_data.py <도메인>   :: [--refresh-geocode] 지오코딩 실패분만 재호출
python app\services\make_parcel_candidates.py <도메인>
python app\services\run_weight_model.py <도메인> --candidates 후보_지적도필지.gpkg ^
       --auto-radius --auto-weight --no-diag --bootstrap 0
python app\services\gam4_site_select.py <도메인>

:: DB 적재 (scripts\ — 파이프라인 산출물을 실 DB 로)
python scripts\create_missing_tables.py            :: 계획만 출력. --yes 로 실제 생성
python scripts\load_audit_data.py <도메인>          :: STEP1 reviewed.json → audit_rules
                                                   :: [--run <run_id>] [--dry-run]
python scripts\load_topn_candidates.py <도메인>     :: STEP4 topN.geojson → booth_candidates
                                                   :: 계획만 출력. --yes 로 적재 [--run <run_id>]
                                                   :: 선행: schema_step4_topn.sql (가산·멱등)
```

🔴 **화면5 공청회 토론은 `audit_rules` 가 비어 있으면 못 돈다.** 감리 근거·AHP 가중치를
거기서 읽는다. 실측: 흡연 → results 11건 → **13행**(hard_exclusion 5 · positive_factor 8).
`reviewed` 를 쓴다 — `audit_result.json` 은 LLM 제안값이고 `reviewed` 가 HITL 확정분이다.

🔴 **STEP4 결과를 화면5 로 넘기는 건 `load_topn_candidates.py` 다**(2026-08-10 신설).
`gam4_export.export_topn()` 은 **파일만** 쓰고 DB 배선이 없었다 — 그래서 `booth_candidates`
엔 손으로 넣은 1행뿐이었고 화면5는 STEP4 와 무관한 **항상 같은 점**으로 토론했다.
적재 개수는 **STEP4 의 `--topn`(기본 20)** 이 정한다. 적재기는 파일에 있는 만큼 전부 넣고
N 을 따로 들고 있지 않다. **`--spacing` 은 개수가 아니라 후보점 간격**이다(다른 인자).
🔴 **`mode: "full"` 은 이 적재를 러너가 마지막 두 칸(`적재-감리`·`적재-후보`)에서 자동으로
돌린다**(2026-08-10). 화면5 는 `audit_rules`(근거)와 `booth_candidates`(대상)를 **둘 다** 읽는다.
손으로 돌릴 일은 정본 산출물(`data_임시/stepN_output/`)을 넣을 때뿐이다 — 그때만 `--run` 없이 쓴다.
🔴 **`run_id` 는 두 테이블의 조인 키다**(2026-08-10 통일). 격리 run 은 `r_YYYYMMDD_NNN`,
정본은 두 적재기 모두 **`'정본'`**. 예전엔 각자 STEP 폴더 이름(`step1_output` ↔
`step4_output`)을 넣어 갈려 있었다 — run_id 는 "어느 STEP 폴더"가 아니라 **"어느 실행"** 이다.
프런트는 `GET /api/v1/simulations/candidates?domain=<도메인>` 으로 목록을 받아
**사람이 고른** 후보의 `parcel_id` 를 `/stream` 에 넘긴다.
🔴 **`rank == 1` 은 추천이지 강제가 아니다**(2026-08-10, 사람 결정). "첫 원소를 넘긴다"고
적어뒀던 건 폐기한다 — 화면4 에서 위치를 선택하고 그 위치로 토론한다.
`/stream` 은 예나 지금이나 **임의의 `parcel_id`** 를 받는다(바뀐 건 계약이지 구현이 아니다).
`run_id` 를 안 주면 **가장 최근 적재분**만 나온다(안 거르면 실행 두 번이 섞여 rank 가 중복).
`limit` 은 **기본이 없다(전량)** — 옛 기본값 20 은 `--topn` 기본값과 우연히 같았을 뿐이라
`--topn 30` 이면 10개가 말없이 잘렸다.
🔴 `순위` 는 **점수 내림차순이 아니다**(MCLP 커버 기여 그리디). 흡연 실측에서 4위 0.7793 >
1위 0.7703 이다 — `ORDER BY score DESC` 로 뽑으면 **다른 점**이 나온다.

🔴 `run_weight_model.py` 에 `--radius`·`--weight` 로 값을 고정할 때는
**`--value-source cli` 를 같이 준다.** 없으면 `SystemExit` 이다(2026-08-05 `d2b780b`).
위 표준 명령에는 고정값이 없어 그대로 쓰면 된다.

도메인 폴더: `data_임시/<도메인>/data/`(원본) · `data_임시/<도메인>/law/`(조례 txt·md·pdf)

🔴 **`data_임시/*/data/`·`region_data/` 는 `.gitignore` 대상이라 clone 에 안 들어온다.**
원본 데이터·지적도는 받는 사람이 따로 구해야 한다. 코드를 처음 받은 사람에게는
`01_설계결정\실행_가이드_인수인계.md` 를 준다.

진단·대조 스크립트는 **`app/tools/` 로 옮겨 추적한다**(2026-08-05, 사람 지시).
예전엔 `검증용/` 에 있었고 그 폴더가 gitignore 라 **픽스처(기준값)는 리포에 있는데
대조기가 없었다** — 재는 자가 없는 자였다. `검증용/` 에는 일회성 분석기
(`analyze_clean_perf.py`) 하나만 남는다.
스크립트는 `__file__` 에서 **두 단계 위**를 저장소 루트로 잡는다(옮기면서 같이 고쳤다).

🔴 **저장소 루트 정리 (2026-08-10, 사람 지시).** 루트에 `.py` 15개가 널려 있었다.
참조를 코드 기준으로 전수로 세고 옮겼다 — 루트에 남은 `.py` 는 **`ingest_statutes.py` 하나**다
(데이터팀 조례 시드 로더. `app/core/data_pipeline/statute_parser.py` 를 부르는 현역).

| 이동처 | 파일 | 성격 |
|---|---|---|
| `tests/` | `test_api_client` `test_dynamic_discussion` `test_multi_docs_persona` `test_spatial_persona` `test_stakeholder_generator` | 실행용 테스트 스크립트 |
| `dummy/` | `check_db` `diag` `load_cleaned_data` `load_data` `poc_statute_ingest` `run_ai_console` `run_ai_console_rag` `run_poi_debate` `show_map` | 일회성·폐기·콘솔 도구 |

🔴 **`diag.py`·`load_data.py` 가 「파이프라인이 쓴다」고 적었던 건 틀렸다**(정정 2026-08-10).
`grep diag` 가 잡은 건 전부 **`--no-diag` CLI 플래그**였고, `upload.py` 의
`load_data`·`ingest_statutes` 는 **주석 문구**였다. 실제 `import` 는 **0회**다.
부분문자열 일치를 참조로 세면 안 옮겨도 될 것을 못 옮긴다 — `import X` / `from X` 로 센다.

옮길 때 같이 고친 것 —
- **`sys.path` 부트스트랩.** `python dummy/x.py` 는 `sys.path[0]` 이 `dummy/` 라
  `import app…` 이 안 된다. 자기 폴더를 루트로 잡던 6개는 **두 단계 위**로 바꾸고,
  없던 것들은 세 줄을 넣었다. 검증: **낯선 cwd(`C:\`)에서 10개 전부 import 성공.**
- **`pytest.ini` 의 `--ignore` 경로.** 🔴 없는 경로를 무시하는 건 **에러가 아니다** —
  옛 경로를 그대로 두면 조용히 아무것도 안 거르고 CI 에서 collection 에러로 되살아난다.
  `norecursedirs` 에 `dummy` 추가.
- **`dummy/load_data.py` 의 평문 비밀번호**(`DB_PASS = "9816"`). `show_map.py` 는
  2026-08-07 침해 대응 때 이미 제거됐는데 **이 파일만 남아 있었다.** 같은 사고의
  잔재는 한 파일만 고치면 안 고친 것과 같다 → `DATABASE_URL` 없으면 `SystemExit`.
- 참조 문서 3곳(`MULTI_STAKEHOLDER_ARCHITECTURE.md` · `RAG_업그레이드_잔여작업_가이드.md` ·
  `docs/데이터팀_DB_구조_가이드.md`)의 경로도 같이 고쳤다.

⚠ `dummy/` 는 **패키지가 아니다**(`__init__.py` 없음). 예전 `app/services/dummy/` 사고는
**같은 모듈명이 두 곳에 있어서** 났다 — 여기 것들은 정본과 이름이 겹치지 않는다.

### 흡연 회귀 기준 (고정 조건에서만 유효)

**기준은 픽스처다** — `data_임시/흡연_FIX/` (2026-08-04 재고정, S5(A) 계측 추가).
**여기 적힌 숫자는 사본이다. 다르면 픽스처가 맞다.**

```bat
python app\tools\check_fixture.py 흡연      :: 57항목 대조 (읽기 전용). --restore 로 reviewed.json 복원
python app\tools\make_fixture.py  흡연 --write  :: 기준선 이동. 수기값(--spacing·--cli) 필수
```

배제 union·커버 쌍·내접폭 분포는 이제 `report.json`(`spatial`·`coverage`)에서 **자동으로 읽는다**.
예전엔 `--union` 으로 손으로 옮겨 적었다 — 옮겨 적는 값은 언젠가 틀리고, 틀려도 아무도 모른다.

🔴 **콘솔이 cp949 면 `PYTHONIOENCODING=utf-8` 없이 죽는다.** 진단 스크립트가 `✅`·`🔴` 를
출력하는 순간 `UnicodeEncodeError` 로 터진다 — 값이 틀린 게 아니라 **출력에서** 터지는 것이라
회귀로 오인하기 쉽다. 파이썬을 subprocess 로 부르는 쪽(API 러너 포함)도 이 env 를 넘길 것.

대조기와 갱신기를 **분리**했다. 확인용 도구가 자기 기준을 갈아치울 수 있으면
오타 한 번에 회귀가 기준으로 승격되고 그 뒤로는 잡을 방법이 없다.

```
감리 입력 sha256  a0adbdd43beda3e6…
--decay gaussian --scale log --radius "07+02=150,06+03=300,08=50,09=150,10=250"
                 --spacing 20 --curve-n 100
w_human  0.1899 / 0.2025 / 0.1772 / 0.0759 / 0.1772 / 0.1772   (07+02·06+03·04·08·09·10)
w_final  0.1858 / 0.1827 / 0.1975 / 0.0870 / 0.1683 / 0.1786
후보 42,216필지 → 후보점 66,915 → 생존 56,967
배제 union  1.1107 km²   (S9 적용. `--no-shape-lift` 면 0.4157)
gap 6건 — 배제판정_확인요청 4 · 주변이격_미적용 1 · 수요_도달불가 1
커버 쌍 5,971,966 · 수요점 6,797                              ← S5 대조용(2026-08-04 추가)
내접폭  중앙 10.7784m · p95 251.7108 · 합 3,445,355.8962 · 폭2m통과 59,989
```

`check_fixture.py` 는 **감리 입력 sha256 이 다르면 값 비교를 하지 않고 멈춘다.**
LLM 이 달라진 것과 코드가 회귀한 것을 섞어서 보여주면 진단이 거짓말이 되기 때문이다.

**기준선 이력** — 값만 보고 회귀로 오인하지 말 것. 조건이 다르다.

| 시점 | w_final(07+02) | 후보점 | union | 왜 갈렸나 |
|---|---|---|---|---|
| ~2026-08-02 | 0.180 | — | 0.7552 | **재현 안 됨.** 추적 불가 — 쓰지 마라 |
| 2026-08-03 오전 | 0.1739 | 143,623 | 1.1358 | S12 최초 고정. `--spacing` 미지정 |
| **2026-08-03 저녁** | **0.1858** | **66,915** | **1.1107** | **현행.** 아래 3갈래 |

현행으로 갈린 이유 — **세 가지가 겹쳤다. 하나로 뭉뚱그리지 말 것.**

1. **코드 수정** ─ 두 건 모두 **감리 AI 에게 없던 정보를 코드가 조달**한 결과다(원칙 3).
   - 크로스워크 연결. `04` 지역코드 `unknown`(검증 불가) → `ambiguous` + 표본판정
     행자부 2/2 로 `auto_confirmed`. `code_prefix_unverified` 플래그 1건 → 0건
   - `value_dist`(프로파일에 값 분포 전체) + 운영상태 프롬프트 규칙 → `05` 어린이집에
     `운영현황 [정상,재개]` 필터. **173행 → 82행** (폐지·휴지 91곳 제외).
     `sample_rows` 는 앞 2행뿐이라 감리 AI 가 `재개`(30번째 행에 처음 등장)를
     볼 수 없었다 → 이전엔 필터가 아예 안 나왔고 **폐지 어린이집까지 배제**했다
2. **LLM 변동** — `04` weight 0.8→0.7 · `11` 배제반경 null→30m ·
   `08` 에 시군구 필터·trim 추가 · `03` whitelist 문자열 변경.
   가중치 전면 이동은 `04` weight 와 `05` 정제 변화의 하류다.
3. **실행 조건** — `--spacing 20`. 후보점이 절반 이하가 된 주된 원인이며 회귀가 아니다.
   구 픽스처는 spacing 을 기록조차 안 했다(그래서 안 걸렸다). 지금은 `조건.spacing` 에 남는다.

**레이어별 실측** — `app\tools\check_exclusion_state.py 흡연` (LLM 호출 0회, 픽스처와 자동 대조)

| ID | 시설 | 감리 | S9판정 | 반경 | 건수 | 기존 | S9 |
|---|---|---|---|---|---|---|---|
| 01 | 금연구역 | polygon | mixed | 10m | 90 | 0.0245 | **0.6325** (필지 47→113) |
| 05 | 어린이집 | radius | point | 30m | 82 | 0.2224 | 0.2224 |
| 06 | 지하철역 | radius | point | 10m | 17 | 0.0053 | 0.0053 |
| 07 | 버스정류소 | radius | point | 10m | 314 | 0.0975 | 0.0975 |
| 11 | 어린이보호구역 | polygon | mixed | 30m | 31 | 0.0875 | **0.4345** (필지 12→34) |
| | **union** | | | | | **0.4157** | **1.1107** (×2.67) |

**union 이 왜 줄었나 (1.1358→1.1107) — 실측으로 규명됨.** `11` 이 null→30m 로 늘었는데
총합이 줄어 앞뒤가 안 맞아 보였다. 두 힘이 반대로 걸렸다.

- `05` 어린이집 173→82행(운영현황 필터) → **약 0.44 → 0.2224 km² (-0.22)**
- `11` 어린이보호구역 반경 부여 → 0.1685 → 0.4345 (+0.27)

그런데 `11` 의 증가분은 **대부분 `01` 과 겹친다.** 어린이보호구역은 학교 주변이고
`01 금연구역` 도 학교를 포함해, 둘 다 같은 `학` 지목 필지로 확장한다.
레이어 합 대비 겹침이 0.208 → 0.2815 로 늘었다. 그래서 `11` 의 **한계 기여는 작고**
`05` 의 감소가 그대로 드러났다.

`05` 감소는 **회귀가 아니라 수정**이다. 이전 0.44 km² 에는 **폐지 어린이집 91곳**이
들어 있었다 — 없는 시설 주변을 배제해 후보를 부당하게 깎고 있었다.

S9 증가분의 출처: `01 금연구역` 0.0245→0.6325(학·공 55점이 면 판정 → 시드 47필지 →
인접확장 113필지), `11 어린이보호구역`(학 12점이 면 판정 → 시드 12 → 확장 34필지).

---

## 작업 방식

- 🔴 **감리·데이터 쪽은 `app/services/` 의 `gam2_*`·`gam4_*` 를 최우선으로 쓴다.**
  그게 실제로 돌아가고 이어져 있는 코드다. 없거나 모자랄 때만 다른 걸 본다.
  나머지(`api/v1` 일부·`sim_ai`·`services` 하위 기타)는 **작성자가 다르고 안 이어진 게 많다** —
  파일이 있다고 살아 있는 코드로 취급하지 말 것. 실제로 `dummy/`·`lands.py`·`ahp.py`가
  그 경우였고(2026-08-04 삭제), 값은 안 맞는데 import 는 되니 안 걸렸다.
- 코드 변경 전 **읽고 확인**한다. 파일 내용을 가정하지 않는다.
- 변경 후 **무회귀 확인** — 정상 경로 결과가 안 바뀌어야 한다.
- 구조 설계 갈림길에서는 **진행 전에 물어본다.**
- 개선점·오류를 발견하면 제안한다.
- 브랜치: 기능별 명명(`gam2_csh2`·`develop2`). 파괴적 작업 전 진단용 read-only git 명령 먼저.

---

## 문서 (필요할 때 읽을 것 — 전부 읽지 말 것)

```
D:\obsidian_claude\10_OmniSite\
  남은 작업들\00_남은작업.md          ← S1~S12 전체. 여기부터
  배포후_작업일지\20260809_DB_현재구조_정리.md
                                     ← 🔴 **DB 를 볼 일이 있으면 여기부터.** 실 DB 39테이블
                                       707MB 를 `\d` 로 전수 정리. 필수 17 / 비어있음 4 /
                                       줄일 수 있음 3 / **제거 가능 14**(0행+참조0회).
                                       §6 에 STEP5 저장 구조 B안 적용 내역과 검증 8항목
  배포후_작업일지\20260809_동현님구간_conflict_simulations_정합.md
                                     ← 동현님 설명용 단독 문서. 3중 불일치의 **경위와 근거**.
                                       처치는 위 문서 §6 (B안, 2026-08-09 적용·검증 완료)
  배포후_작업일지\20260808_파이프라인_실행중단_WinError5.md
                                     ← **최근.** ⓐ run 이 조용히 죽고 도메인이 409 로
                                       잠기던 건(os.replace WinError 5) — **수정·검증 완료**
                                       ⓑ 화면5·6 엔드포인트 6개 500 — **✅ 2026-08-09 해소.**
                                       3개가 겹쳐 있었고 셋 다 처리됐다: 3중 불일치(B안) ·
                                       `scalar_first`(→`scalars().first()`) ·
                                       weasyprint 는 **애초에 거짓**이었다(playwright 를 쓴다).
                                       실측 `/results/1` 200 · `/report/1` 86,260 bytes PDF
                                       ⚠ **그 1행은 2026-08-10 정리 때 지웠다**(손입력 후보점 ·
                                       domain·run_id 가 NULL 이라 화면5 가 못 쓴다) → 지금 `/results/1`
                                       은 404 다. 회귀가 아니다. 살아 있는 예는 `/results/2`
  02_작업일지\2026-08-10.md           ← 🔴 **최신.** §8 관통 테스트 2경로(경로A 기존DB ·
                                       경로B 업로드→STEP0~4→적재→토론→PDF) ·
                                       §9 DSN 127.0.0.1 고정 + connect_timeout ·
                                       `_select_audit_rules` 도메인 조건 · 테스트 산출물 제거 ·
                                       §9-5 화면1→3 배선 결함 → **§10 `mode:"full"` 로 해소** ·
                                       §11 업로드→화면6 **완주 실측** ·
                                       **§12 `audit_rules` run_id 정렬**(정본 어휘 통일 · A안 철회 경위) ·
                                       **§13 프런트 요청 3건** — `has_fixture`(러너 판정 재사용) ·
                                       토론 초기 CSS 무작위 제거 + 평가/토론 프롬프트 정정 ·
                                       `status.json` **`loaded`** 신설 ·
                                       **§13-5 재시작 후 살아 있는 서버 실측**(토론 2회 —
                                       수용도가 라운드가 아니라 **근거**를 따라 움직인다)
  02_작업일지\2026-08-09.md           ← DSN 기본값 제거 4곳 · _reap_orphans 부팅1회 ·
                                       scalar_first · 낡은 주석 정정 · 누락 테이블 생성 ·
                                       감리 로더 재작성 · **§7 STEP5 저장 B안 적용**
  02_작업일지\2026-08-07.md           ← DB 스택 이관 · 경계 3종 적재 · PR #217 분석
  배포후_작업일지\20260807_PR217_sim_ai_충돌_동현_민영.md
                                     ← sim_ai 충돌 = 동현님↔민영님 작업물 충돌. 두 분께 전달
  배포후_작업일지\20260807_PR217_파일별_문제와_수정안.md
                                     ← 위 문서의 **처치 계획**. 파일 10건 각각 무슨 문제·어떻게 고칠지
  배포후_작업일지\백엔드_더미데이터.md ← 커밋된 더미·중복 48.63MB 목록. 정리는 민영님 병합 **후**
  02_작업일지\2026-08-06.md           ← 통합 (동현님 STEP5·6 PR #211 · DB팀 PR #216)
                                       충돌 8건 중 7건이 ruff 포맷 · AST 대조로 판정한 근거
  02_작업일지\2026-08-05c.md          ← 프런트: 화면 1~4 완주 확인 · 하이드레이션 사고
  02_작업일지\2026-08-05b.md          ← A2 HITL 게이트 구현 완료
  02_작업일지\2026-08-05.md
  02_작업일지\2026-08-04c.md          ← 라우터 표면 확정 · 폐기 스캐폴딩 삭제 · 의존성 핀
  02_작업일지\2026-08-04b.md          ← S5 선행 검증 (계측 + PostGIS 정합성 실측)
  02_작업일지\2026-08-04.md           ← 파이프라인 실행 API 신설
  02_작업일지\2026-08-03b.md          ← S4·S6·S12 완료
  02_작업일지\2026-08-03.md           ← S9 완료
  02_작업일지\2026-08-02.md
  02_작업일지\2026-07-31.md
  01_설계결정\벡엔드_설계.md          ← 계층·저장경계·격리층·실행API·회귀방어·규약·DB연동
  01_설계결정\STEP1_감리AI_설계.md
  01_설계결정\STEP3_가중치_설계.md
  01_설계결정\STEP4_위치선정_설계.md
  01_설계결정\의존패키지_외부자원.md    ← 설치·API키·참조데이터
  01_설계결정\실행_가이드_인수인계.md   ← **코드를 처음 받은 사람용** (백엔드+프런트 · 2026-08-05)
  01_설계결정\백엔드팀_API현황_및_Redis_Postgres_전환.md
                                    ← **백엔드팀 전달용** (2026-08-05). 살아 있는 API 9개 실측 ·
                                       schema.sql↔ORM 불일치 · Postgres/Redis 전환 판단표
  04_이슈\2026-08-08_로컬DB_랜섬웨어_침해사고.md  ← 🔴 **먼저 읽을 것.** 로컬 Postgres 가
                                       0.0.0.0 + 비번 postgres 로 뚫려 DB 가 삭제됐다.
                                       타임라인·처치·팀 전파 문구·점검 명령
  04_이슈\2026-08-05_GH이슈_PostGIS_공간연산전환_중단.md  ← S5 중단 근거(팀 공유용)
  01_설계결정\프런트_설계.md            ← 프런트 세션이 쓴다. 화면↔산출물 대응·rewrite 경계
  작업 노트\배제구역_점면판정_지목배수.md  ← S9
  작업 노트\S10_조례_단서조항_설치가부.md ← S10
```

설계 결정을 바꾸거나 새 함정을 발견하면 **해당 문서에 근거와 함께 기록**한다.
되돌린 결정은 지우지 말고 이유를 남긴다.

---

## 현재 우선순위 (2026-08-05)

**1차 목표는 화면 1~4 다.** 화면 5(공청회)·6(PDF)은 다른 팀원에게 넘어갔다 —
먼저 손대지 않는다. 아래 🔴 는 그 인계 사실을 반영한 잔여분이다.

```
✅ 화면1 (/upload)       **2026-08-09 재작성 완료** (`check_upload_api.py` 25/25).
                        옛 구현은 `uploads/regulations/` 에 저장했다 — **파이프라인이
                        읽지 않는 곳**이라 올려도 아무 영향이 없었다(200 이라 안 걸린다).
                        지금은 조례 → `data_임시/<도메인>/law/`(= `load_ordinance()` 가
                        읽는 곳) · 데이터 → `.../data/`(= `profile_folder()`).
                        **도메인은 필수 인자**다(기본값을 두면 성동구 파일이 흡연
                        폴더로 조용히 들어간다). 조례·데이터 **둘 다 다중 파일**이고
                        조례는 폴더 안 txt·md 를 전부 합쳐 쓴다(EV 5·흡연 3·재활용 2).
                        파서는 데이터팀 `statute_parser` 로 통일, Redis 에는
                        **메타데이터만**(흡연 data/ 만 537MB · 단일 최대 279MB).
                        `facility_type` 은 요청값 → `audit_result_reviewed.json` →
                        **400**(추측 안 함)이고 출처를 응답에 남긴다.
                        `dataset_id` 는 파일명 가나다순이라 앞 번호로 끼어들면 뒤가
                        전부 밀린다 → 응답 `renumbered` 로 알린다.
                        상세: `02_작업일지\2026-08-09.md` §8-2·8-3
⬜ 이슈 #205 되묻기      **3건 → 1건.** 2026-08-08 실측으로 3건 다 철회했다
                        (근거: `02_작업일지\2026-08-04b.md` §7-2). DDL 은 세 코드를
                        다 들고 있었고, 미매칭은 4가 아니라 6이며 전부 서울 밖이고
                        **#205 본문이 이미 답해놨다**
                        남은 1건 = `national_owned_properties`(schema_cleaned_data.sql)
                        ↔ `national_properties`(schema_cleaned_data_add.sql) **중복 정의.**
                        같은 원본(국유부동산_위경도.csv 2,486행)이 컬럼명만 다르게 두 번.
                        둘 다 있으니 에러가 안 난다 — 정본을 정해야 한다
⬜ 조문 선별 검증        app\tools\check_ordinance_select.py 재활용 — 누락 조문 확인
⬜ 인용 법령 한정        규제 조문에서만 추출 (한 줄, 크레딧 절약)
⬜ 성동구 완주           OpenAI 크레딧 충전 후

✅ S9  배제 점/면 판정 — 지목 배수 결정론화 (S8 흡수)   2026-08-03 완료
       임계 HITL 은 C안 — 실행은 안 막고 gap `배제판정_확인요청` 으로 내보낸다.
       뒤집는 UI 는 A2(HITL→API) 때 프런트에 태운다
✅ S4  diagnostics 를 산출물에 기록                  2026-08-03 완료
✅ S6  지오코딩 디스크캐시 + 토큰버킷 + ThreadPool     2026-08-03 완료
       STEP2 흡연 267s → cold 74.6s / warm 24.8s. 전 컬럼 행 단위 diff 0건
✅ S12 회귀 픽스처 고정  data_임시/흡연_FIX/          2026-08-03 완료
       check_fixture.py 46 → **57항목**(2026-08-04). 남은 것: `--fixture` 경로 주입
✅ A1  파이프라인 실행 API  app/api/v1/pipeline.py    2026-08-04 완료
       픽스처 재실행(STEP2~4)만. 계약은 `pipeline_run_contract.md` 단독 기준
       ✅ **`mode: "full"` 신설로 해소**(2026-08-10, 사람 결정 · 계약 8절).
          예전엔 `fixture`·`hitl` 둘 다 `<도메인>_FIX/` 를 요구해 업로드 도메인이
          **한 단계도 못 돌았다**(`_PLAN` 에 STEP0·1 이 없었다). 막힌 건 코드가 아니라
          **값의 출처**였다 — 이제 `full` 이 조건을 **명시 선언**한다:
          `alpha 0.3 · decay gaussian · scale log · spacing 20`(출처 `흡연_FIX/기준값.json`).
          🔴 CLI 기본값은 `scale=minmax`·`decay=null` 이라 **이 넷을 안 넘기면 Top-N 이
          통째로 갈린다**(아래 `--auto-*` 함정). 도메인 값이 아니라 **계산 방식**이라
          원칙 2 에 안 걸린다 — 대신 `runs/<id>/params.json` 에 요청값을 남긴다.
          사람이 정하는 건 **`user_input`(필수) · `topn`(기본 20) 둘뿐**이다.
          계획 11칸 — `0-1 → seed → 게이트A → 2 → 3-1 → propose → 게이트B → 3-2 → 4
          → load-audit → load`
          (게이트 뒤쪽은 `hitl` 과 **같은 배열·같은 `_proc_of`**. 다르게 짜면 갈린다)
          단계 표시는 **모드가 정한다**(fixture 6 · full 10). 상수로 갖지 말 것.
          `seed` = STEP1 의 `enriched`(없으면 `audit_result`) → `reviewed` 로 앉히는 칸.
          폴백 순서는 정본 `review_hitl()` 과 같다. `full` 은 `_prepare_dirs` 가
          **아무것도 복사하지 않는다** — 정본을 깔아두면 감리 실패 시 남의 결과로 진행한다.
          🔴 **화면4 와 화면5 사이의 다리는 둘이다**(2026-08-10 실측으로 정정).
          `load-audit` = reviewed → `audit_rules`(**무엇을 근거로**) ·
          `load` = topN.geojson → `booth_candidates`(**어디를**). 화면5 는 둘 다 읽는다 —
          뒤만 넣으면 `/candidates` 는 20행인데 `/stream` 이 0.4초에 죽는다(`r_20260810_001`).
          "유일한 다리"라고 적었던 건 틀렸다. 둘 다 `full` 에만 있다
          (fixture·hitl 은 정본이 이미 DB 에 `run_id='정본'` 으로 있다 — 2026-08-10
          어휘 통일 전에는 `step1_output` ↔ `step4_output` 로 갈려 조인이 0건이었다).
          ✅ **완주 실측 (2026-08-10)** — 흡연 `data/`·`law/` 를 **화면1 업로드 API 로**
          새 도메인에 넣고 STEP0→화면6 까지: `r_20260810_002` **9분 11초 · 10칸 succeeded**.
          값은 픽스처 기준선과 **전부 일치**(후보 42,216 / 후보점 66,915 /
          union 1.1107 / w_final 6개 / gap 6건). 화면5 1,564 events 39.6s · 화면6 PDF 82,551 B.
          `fixture` 도 같은 날 재확인 — `r_20260810_004` **71초 · 6칸 succeeded**.
          상세는 `pipeline_run_contract.md` §8-8.
          🔴 **여기 "손으로 거든 단계 0" 이라고 적었던 건 틀렸다.** 근거는 계약 §8-8
          **같은 절의 표**다 — 「게이트 A·B 둘 다 정상 정지 → POST 로 재개」라고 적혀 있고
          `_PLAN["full"]` 에도 게이트 칸이 둘이다. **`full` 은 무인 완주 모드가 아니다.**
          그때 답이 **픽스처 값 그대로**(`radius 150/300/50/150/250`)여서 `w_final` 이
          기준선과 같았던 것이다 — 「업로드 도메인이면 기준선이 재현된다」가 아니라
          **「같은 답을 넣으면 같은 값이 나온다」**.
          🔴 **`run_id` 는 재사용된다 — 문서에 적은 run_id 가 나중에 다른 run 을 가리킨다.**
          `_new_run_id`(`pipeline_runner.py:667`)가 `runs/r_<날짜>_*` **폴더를 세어** 다음
          번호를 매기므로, 폴더를 지우면 번호가 되돌아간다. 위 정정을 쓰면서 나도 밟았다 —
          `runs/r_20260810_002/hitl/` 에 답 파일이 있는 걸 근거로 삼았는데 **그건 흡연_E2E
          run 이었다**(이 문단이 말하는 흡연업로드 run 이 아니다. `data_임시/흡연업로드` 는
          이제 없다). 결론은 그대로지만 **근거는 틀렸었다.** run 폴더를 지울 땐 **그 번호를
          인용한 문서가 있는지 먼저 본다.**
          ✅ **재실측 (2026-08-10, 서버 재시작 후 · 사람 지시)** — 같은 경로를 `흡연_E2E2` 로
          다시 관통했다: `r_20260810_006` **10칸 succeeded** ·
          `loaded {audit_rules:13, booth_candidates:20}` · 화면5 1,440 events 53.5s ·
          화면6 PDF 85,039 B + **HWPX 4,520 B**(QR 2개, 선언 4곳 전부 일치).
          결정론 구간(후보 42,216 / 후보점 66,915 / 생존 56,967 / union 1.1107 / gap 6)은
          **전부 일치**했고 `w_final` 만 갈렸다 — 이번엔 게이트에 **LLM 제안값을 승인**했고
          그 값이 픽스처 조건과 다르다(`07+02` 150→200 · `09` 150→100 · `10` 250→150 ·
          슬라이더 `04` 0.7→0.8 등). **회귀가 아니라 실행 조건 차이**다.
          🔴 **결과 문서가 어느 run 에 붙었는지는 조인으로만 확인된다.**
          `conflict_simulations` 에는 **`run_id` 컬럼이 없다** — 경로는
          `parcel_id → booth_candidates.id → booth_candidates.run_id` **하나뿐**이다.
          실측: `sim id=18 · parcel_id=122 · result_json 5,736자 · worst_scenario 만 채움 ·
          debate_logs 14행` → 조인하면 `run_id='r_20260810_006' rank=1`. 세 run
          (`정본`/`r_20260810_002`/`r_20260810_006`)이 각각 13·20행으로 **격리**돼 있다.
          토론의 `conflict_factors` 가 **8개**(=`positive_factor` 8행)로 나온 것이
          §12 조회 `run_id` 조건이 실제로 걸렸다는 증거다(안 걸리면 16개다).
          상세: `02_작업일지\2026-08-10.md` §17.
          ✅ **같은 도메인 2회차 누적 해소**(2026-08-10, 사람 승인 · 계약 6절 B안).
          적재는 예전부터 `(domain, run_id)` 교체였고 **조회만** `(domain, facility)` 를
          봤다 → 26행. 조회에 `run_id` 를 넣고(값은 `booth_candidates` 행에서) 정본
          어휘를 `"정본"` 으로 통일했다. 26행 저장 ↔ **13행 조회** 실측. 계약 §8-9.
          🔴 **화면 번호와 STEP 번호를 섞어 쓰지 말 것**(프런트 설계 §3). "화면1→3"
          은 화면 번호이고 **화면2 를 건너뛴다는 뜻이 아니다** — 1·2·3 을 잇는다는 뜻이다.
       산출물 화이트리스트 8키(reviewed·exclusion 포함) · 응답 media_type 명시
       ✅ **`status.json` 에 `loaded` 신설**(2026-08-10, 사람 결정 · 계약 3-1).
          이 run 이 **DB 에 넣은 것**이다 — `{run_id, audit_rules, booth_candidates}`,
          안 넣었으면 `null`(fixture·hitl 은 계획에 적재 칸이 없다).
          프런트는 여기 있는 `run_id` 를 `/candidates` 에 그대로 넘긴다 —
          "full 이면 run_id 와 같다"는 **규칙을 양쪽이 각자 구현하지 않게** 값으로 준다.
          행 수는 적재기가 찍는 약속된 줄(`[LOADED] table=… run_id=… rows=…`)에서만
          읽는다. 러너가 DB 에 다시 세면 **적재 이후 남이 건드린 값**을 이 run 의
          성과로 적게 된다. 그 줄의 run_id 가 어긋나면 단계를 `failed` 로 닫는다.
          ⚠ 옛 run 은 키가 없어 `null` 로 채워지는데, 그 `null` 은 "기록 없음"도
          포함한다 — 구분이 필요하면 `steps` 의 `적재-감리`·`적재-후보` 칸을 본다
       화이트리스트에 키를 추가하면 **옛 run 의 status.json 에는 그 키가 없다**
       (생성 시점 ARTIFACTS 로 굳는다) → `read_status` 가 빠진 키만 디스크 보고
       채운다. 있는 값은 안 건드리고 파일에도 안 쓴다
       `GET /runs/{id}/log` 추가(2026-08-05) — **유일하게 원본을 안 내보내는 응답**이다.
       run.log 는 우리가 뭘 찍을지 통제하지 않는 자식 stdout 이라 마스킹 후 내보낸다:
       `<repo>`·`<home>`(OS 계정명)·`<python>`·`<마스킹:KEY이름>`. 지운 자리는 표시를
       남긴다 — 조용히 없애면 원본인 척한다(원칙 4)
✅ A2  HITL API — **게이트 방식**  2026-08-05 완료 (사람 승인 · 계약 7절)
       HITL 은 파이프라인이 멈춰서 사람을 기다리는 게이트다. **재실행 0회.**
       `mode: "hitl"` · `POST /runs/{id}/hitl/{audit,weight}` ·
       `status: awaiting_hitl` + `gate` 로 멈추고 POST 로 이어간다
       게이트A(STEP1 끝: 배제반경·데이터의도·지역코드) ·
       게이트B(STEP3 중간: [R] 집계반경 + [W] 슬라이더 -1~+1 을 **한 게이트로**).
       게이트B 앞에 `--propose-only` 제안 패스 1회(9.6초, LLM mini 1회)를 둔다 —
       제안값은 돌려봐야 나오고, API 쪽에서 다시 구현하면 CLI 와 갈라진다
       STEP4 는 게이트가 없다(`input()` 0개, 설계와 일치)
       🔴 "다 돌린 뒤 뒤집고 재실행" 으로 설계했던 건 **틀렸다.** 재실행범위·
          reused 상태·부분재실행이 전부 그 전제에서 나온 가짜 문제였다.
          원인 — 그때 mode 가 fixture(무입력 완주) 하나뿐이라 **내가 만든 것을
          파이프라인의 모습으로 착각**했다. `stdin=DEVNULL` 은 러너가 박은 것이지
          파이프라인의 성질이 아니다. 실측하니 바로 보였다: `input()` 은
          gam2_audit_judgment_test 4개 · run_weight_model 3개 · 나머지 전부 0개
       정본(`gam2_*`) 수정 없음. 답변 적용은 `apply_radius_answer`·
       `apply_intent_answer`·`apply_weight_hitl` **정본 함수로만** 한다
       검증 — `check_hitl_gate.py` **57/57** · `check_hitl_e2e.py` 로
       fixture ↔ hitl **10항목 전부 일치**(같은 답을 넣으면 같은 값이 나온다)
       ✅ **배제는 이제 hitl·full 에서 전부 사람이 본다**(2026-08-10, 사람 지시 · 계약 §7-7).
          "확정분은 읽기 전용" 이라고 적어뒀던 건 폐기한다 — `_prepare_dirs` 가
          **run 안의 사본만** `reset_exclusion_confirmations()` 로 되돌려 5건 전부
          편집 가능해진다(값은 `제안값` 으로 보존, 원본 픽스처는 무변경).
          `fixture` 는 게이트가 없으므로 되돌리지 **않는다**(되돌리면 STEP2 가 멈춘다).
✅ 라우터 표면 확정  /api 경로 **9개**(auth 2·audit 2·pipeline 5)  2026-08-05 갱신
       🔴 `merge_Back_2` 는 **31개**다(2026-08-10 PR #224 병합 후 실측 — `app.routes`).
          auth **4**(PR #221 듀얼 토큰) · audit 2 · pipeline 5 · simulation **5 × 두 prefix**
          (`/simulation`·`/simulations`) · upload 7 · **stakeholders 2 · report 1**(PR #224).
          🔴 **31개 중 「돌려본 것」은 그보다 적다**(2026-08-10 로그 실측). 구현됐다고
          실행 근거가 생기는 게 아니다. `pipeline` 5 · `upload` 7 · `simulations`(복수) 5 ·
          `report/hwpx` 1 = **실행 확인됨**. `simulation`(단수) 5 는 안 쳤지만
          **같은 라우터 객체**라(`main.py:148·153`) 핸들러가 동일하다. `auth` 4 는
          통과했으나 **InMemoryDB 목**이다. 🔴 **`audit` 2 · `stakeholders` 2(B 다인
          토론)는 한 번도 안 쳤다** — PR #224 로 들어온 뒤 미실행. 장부는
          `02_작업일지\2026-08-10.md` §18-3.
          "20개"·"26개" 로 적어뒀던 건 그때그때 틀렸다 — upload 를 3개로 알던 시절,
          auth 를 2개로 알던 시절의 숫자가 그대로 남았다. 두 prefix 라 simulation 은
          **하나 늘면 둘 는다**. `develop2` 는 아직 9개다.
          **어느 브랜치를 보고 있는지부터 확인할 것.**
       🔴 **화면5 토론 엔진은 둘이다**(PR #224 병합, 2026-08-10). 하나로 합치지 않았다 —
          **A 대립 토론**(찬반 + evaluator · `app/core/sim_ai/` · `/simulation(s)/stream`,
          동현님) · **B 다인 토론**(이해관계자 페르소나 N명 · `app/core/stakeholder_mode/` ·
          `/stakeholders/*`, 민영님). 입력은 공통이다 — **화면4 에서 사람이 고른 추천입지**.
          어느 쪽으로 갈지는 **프런트가 정한다**(분기 UI 동현님 담당). 프롬프트·평가 로직을
          고칠 때 **어느 엔진 얘기인지 먼저 확인한다** — 한쪽만 바뀐다.
          화면6 도 둘이다: 기존 **PDF**(`pdf_service.py`, playwright) · 신규 **HWPX**
          (`/report/download/hwpx`).
          ✅ **QR 조용한 실패 제거**(2026-08-10, 사람 지시). `generate_qr_png_bytes` 는
          예외를 전부 삼키고 `b""` 만 돌려줬다 — QR 이 통째로 빠진 문서가 **왜 없는지도
          없이** 나갔다(원칙 1·4). 이제 `(png, 사유)` 를 돌려주고, 실패하면 그 자리에
          **`[ X ] QR 코드 생성 실패 … (사유: …)`** 대체 문구가 들어간다 + `warning` 로그.
          🔴 이 함수는 주소·좌표로 **URL 문자열만** 만든다 — **카카오/네이버 API 키와 무관**하다.
          실제 원인은 `qrcode` 패키지 미설치였다(PR #224 병합 때 설치).
          🔴 이미지 선언이 **네 군데**다(`manifest.xml` · `content.hpf` ·
          `header.xml`의 `<hh:bindataList>` · `section0.xml`의 `<hp:pic>`). 한 곳만 고치면
          나머지가 **zip 에 없는 이미지**를 가리켜 한글이 파일을 못 연다 — 넷 다 같은
          `qr_images` 목록에서 만든다. 한쪽만 실패해도 **성공한 쪽은 그대로 들어간다**
          (예전엔 `has_qr = 둘 다 성공` 이라 하나 실패하면 둘 다 사라졌다).
          실측: 정상 4,441 B/이미지 2/선언 (2,2,2) · 전체실패 2,844 B/(0,0,0)/대체문구 2 ·
          한쪽실패 이미지 1 + `<hp:pic>` 1 + 대체문구 1. 네 XML 전부 파싱 통과.
       services/dummy(4248ff3) · api/v1/{ahp,lands}.py(7f66fd9) 삭제.
       ahp_service 는 **미구현이 아니라 폐기** — 만들면 안 된다.
       🔴 `gis_service` 를 여기 같이 넣었던 건 **틀렸다**(정정 2026-08-06, 사람 지시).
          폐기가 아니라 **STEP5 가 쓰는 코드**다 — `simulations.py:23` 이 최상단에서
          import 하고 `:247` 이 `get_poi_context_from_db` 로 후보 주변 POI 문맥을
          LLM 프롬프트에 넣는다. 빼면 공청회 시뮬레이션이 안 돈다.
          `/lands`·`/ahp` 라우터를 지울 때 **그 라우터가 부르던 서비스**라는 이유로
          같이 묶었다 — 호출자 하나가 죽었다고 피호출자까지 죽는 게 아니다.
          S5 결론(PostGIS 가 6~10배 느리다)은 **파이프라인 공간연산** 얘기이고
          `gis_service` 의 단건 POI 조회와는 다른 문제다. 같이 판단하지 말 것
       🔴 pdf_service·simulations 를 여기 같이 넣었던 건 **틀렸다**(정정 2026-08-04).
          simulations = 공청회 시뮬레이션(512행+graph.py 335행) · pdf_service =
          화면6 PDF 빌더(42행). 둘 다 폐기가 아니라 **배선 대기**이고 담당이 넘어갔다.
          `dummy/` 라는 위치·이름만 보고 분류했다 — 파일을 열지 않은 단정(원칙 5)
       여기 없는 경로는 404 다. try/except 로 감싸서 건너뛰지 않았다(원칙 1)
S10  조례 단서 조항 "금연구역 ≠ 설치 불가"           설계 확정
S11  조례 없을 때 상위법 직접 검색 + x좌표→4326
🔴 S5  공간 연산 PostGIS 전환 — **실측하고 중단했다 (2026-08-04)**
     `app\tools\bench_postgis.py 흡연` — 정합성은 맞았지만 **6~10배 느리다.**
       neighbors_within  geopandas 1.24s ↔ PostGIS 12.9s (**0.10x**, 쌍 7.0M)
       inscribed_width   8.41s ↔ 9.6s (0.87x) · buffer_union 은 비김
     반증 2건 다 실패 — work_mem 4MB→1GB 무변화 · SQL 안에서 집계해 전송 0 으로
     만들어도 0.16x. 병목은 설정도 전송도 아니고 **행 단위 실행기 오버헤드**다.
     shapely 는 STRtree+벡터화 numpy 로 700만 쌍을 연속 메모리에서 한 번에 돈다.
     ✅ 정합성은 확인됨: ST_DWithin ≡ neighbors_within, 커버 쌍 7,014,079 완전 일치
        (GEOS 3.13 ↔ 3.9, 4버전 차이에도 짝까지 같다)
     ⚠ ST_MaximumInscribedCircle 만 **tolerance 정책 차이**로 갈린다(GEOS 탓 아님).
        경계 8필지 폭 2.0005~2.0115m — 옮길 일이 생기면 tolerance 를 맞출 것
     🔵 PostGIS 가 이기는 건 속도가 아니라 **메모리 한계**(out-of-core)다.
        전환 방아쇠는 "확장성"이 아니라 **실제로 RAM 이 터지는 시점**이어야 한다.
        `gam4_spatial_ops.py` 격리층은 그대로 둔다 — 갈아끼울 지점은 여전히 한 곳이다
     S5 의 Redis·크로스워크 항목은 별개다. 위 결론이 그쪽까지 부정하지 않는다
```

S9 결과 실측: 배제 union 0.5478 → **1.1358 km²** (×2.07, 예상했던 3 km² 보다 작다 —
인접확장이 도로에서 끊기기 때문. 과다배제 0%). 상세는 `작업 노트\배제구역_점면판정_지목배수.md`.
