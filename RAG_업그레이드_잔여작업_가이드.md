# 조례 RAG 업그레이드 — 잔여 작업 가이드라인

> 발제 v3(7건) 대비 코드 실측 결과 및 남은 작업의 구현 가이드.
> 기준일: 2026-07-23 / 브랜치: `poc/statute-ingest`

---

## 진척 현황 (발제 7건 대조)

| # | 항목 | 상태 | 근거 |
|---|------|------|------|
| 1 | 조 단위 파싱 + 위계 헤더 | ✅ 완료 | `app/core/data_pipeline/statute_parser.py`, `ingest_statutes.py` — 시드 6종·222청크 적재 |
| 2 | facility_type 필터 정식화 | ✅ 완료 | `app/core/sim_ai/vector_db.py:110` `filter=`, ingest 문서별 태깅 |
| 3 | 유사도 임계치 | ✅ 완료 | `vector_db.py:14` `SIMILARITY_THRESHOLD=0.36` (실측) |
| 4 | metadatas 확장 | ✅ 완료 | `vector_db.py:75` 시그니처 개방 + `extract_doc_meta` |
| 5 | 승인 게이트 (`ordinance_documents`) | ❌ 미구현 | 주석에만 존재 (`statute_parser.py:52`) |
| 6 | 룰 라이브러리 + 공간 배제 연동 | ❌ 미구현 | 테이블 없음, `gis_service.py`가 10m 하드코딩 |
| 7a | 3-small 임베딩 명시 | ✅ 완료 | `vector_db.py:21-23` |
| 7b | fallback 문구 전환 | ✅ 사실상 완료 | `simulations.py:127-129`가 이미 "조례·법령 없음" 반환 |

**진짜 남은 것: 5번(승인 게이트), 6번(룰 라이브러리 + 배제 쿼리).**
6번이 7/24 E2E "공간 배제 검증"에 직결됨.

---

## 5. 승인 게이트 (`ordinance_documents`)

### 문제
현재 `upload.py:92`가 업로드 즉시 `add_statute_chunks`로 벡터에 적재 → 미검증 문서가 바로 오염원(#91). "저장 → 검토 → 승인해야 적재"로 흐름을 끊어야 함.

### 작업

**1) ORM 모델 신설** — `app/db/models/ordinance.py` (기존 `spatial.py` 패턴)

```python
class OrdinanceDocument(Base):
    __tablename__ = "ordinance_documents"
    id = Column(Integer, primary_key=True)
    document_id = Column(String(64), unique=True, index=True)  # 벡터 metadata 조인키
    title = Column(String(250), nullable=False)
    ordinance_no = Column(String(50))       # extract_doc_meta 결과
    effective_date = Column(Date)           # 〃
    district_id = Column(Integer, ForeignKey("districts.id"))
    facility_type = Column(String(50))
    file_path = Column(String(500))
    status = Column(String(20), default="pending")  # pending/approved/rejected
    uploaded_at = Column(DateTime, server_default=func.now())
```

`extract_doc_meta`(`statute_parser.py:51`)가 시행일·조례번호를 이미 추출 → INSERT 재료로 사용.

**2) 업로드 흐름 분리** — `upload.py` `upload_regulation`에서 `add_statute_chunks` 호출 **제거**, 파일 저장 + `ordinance_documents` INSERT(status=pending)까지만.

**3) 승인 엔드포인트 신설** — `POST /regulations/{id}/approve`
- status → approved
- **이 시점에** `parse_statute` → `add_statute_chunks(texts, metadatas)` 실행 (metadata에 `document_id` 필수)
- `reject`는 status만 변경, 적재 안 함

**4) 개정 재적재** — 벡터에서 `document_id`로 삭제 후 재적재 → 콜렉션 전체 재적재 회피 (4번 metadata가 가능케 함).

---

## 6. 룰 라이브러리 (`domain_regulation_rules`) + 배제 쿼리 연동 ★E2E 직결

### 문제
`gis_service.py:85` `screen_available_lands`가 **모든 규제구역을 `exclusion_meters=10.0` 단일 하드코딩**으로 버퍼링. "학교 200m, 버스정류소 10m" 등 시설별 법정 이격거리 미반영. 법적 배제 판정은 결정적 SQL(승인된 룰)로 분리해야 함.

### 작업

**1) ORM 모델 신설**

```python
class DomainRegulationRule(Base):
    __tablename__ = "domain_regulation_rules"
    id = Column(Integer, primary_key=True)
    facility_type = Column(String(50))          # 설치하려는 시설 (흡연부스)
    target_zone_type = Column(String(50))       # 배제 기준 시설 (학교/버스정류소…)
    distance_m = Column(Float, nullable=False)  # 이격거리
    source_ordinance_id = Column(Integer, ForeignKey("ordinance_documents.id"))
    source_span = Column(Text)                  # 원문 근거 문장 (환각 방지)
    status = Column(String(20), default="pending")  # approved만 쿼리 참조
```

**2) 수동 시딩 ~10건** — `seed_rules.py` 스크립트.
`target_zone_type`은 `ingest_statutes.py:41-42` 주석의 주변시설 값과 **정확히 일치**시킬 것
("학교", "버스정류소", "지하철역", "어린이집", "어린이보호구역") — 안 맞으면 조인 0건.

**3) 배제 쿼리 연동** — `gis_service.py:83` `screen_available_lands` 개조 (몸통)
- 시그니처: `exclusion_meters` 단일값 → `facility_type` 기반
- approved 룰을 `target_zone_type`별 조회 → 각 규제 소스 테이블(`RestrictedZone`/`ChildcareCenter`/`TransitStation`)을 **자기 `distance_m`로 각각 버퍼링** 후 `ST_Union`
- 현재 단일 `buffer_subquery`(94~104행)를 "룰별 buffer 합집합"으로 확장
- **approved 룰만** WHERE에 태움 (5번 게이트와 짝)

> 팁: 현재 배제 대상이 `RestrictedZone` 하나뿐 → 1차 E2E는 "RestrictedZone에 룰 distance 적용"까지만 해도 검증 성립. 다중 소스는 다음 단계.

---

## 7/24 E2E 검증 시나리오

1. **인용 경로**: 토론 API → `simulations.py:123` RAG 검색이 조례명·조번호 헤더 포함 content 반환 확인 (#89)
2. **배제 경로**: `screen_available_lands(facility_type="흡연부스")` → 학교 반경이 10m가 아닌 **룰 distance로** 잘려나가는지, `usable_geometry` 면적 변화로 검증
3. **게이트**: pending 문서는 검색에 안 잡히고, approve 후에만 잡히는지
4. `ingest_statutes.py`의 **필터 걸고/없이 이중 검증**(214~244행) 패턴을 배제 쿼리에도 적용

---

## 작업 순서 추천

E2E("공간 배제 검증")가 6번에 직결 → **6번 → 5번** 순서.
6번도 통짜 말고 3단계로:

- **6-(a)** 룰 테이블 + 시드 스크립트
- **6-(b)** `screen_available_lands`를 RestrictedZone 단일 소스로 룰 연동  ← 여기까지면 내일 E2E 성립
- **6-(c)** 다중 소스(학교/정류소) 확장

---

## 참고: 정리 대상 파일

- `dummy/poc_statute_ingest.py` — PoC 원본. `statute_parser.py`로 승격 완료.
  🔴 "git untracked 라 삭제 가능" 은 **틀렸다** — 실측하면 tracked 다(2026-08-10).
  지우지 않고 루트에서 `dummy/` 로 옮겼다. 이력이 필요하면 git 에서 꺼낸다.
- `omnisite_backup.sql` — 임시 백업으로 추정, 정리 여부 확인 필요
