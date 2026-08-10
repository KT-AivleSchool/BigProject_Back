# Multi-Stakeholder Mode (동적 이해관계자 모드) 디렉터리 & 파일 역할 정의서

본 문서는 **`multi-stakeholder` 브랜치**에서 개발한 백엔드(`BigProject_Back_New`) 및 프런트엔드(`BigProject_Front_New`)의 전체 폴더 구조와 각 파일의 역할 및 기능을 체계적으로 정리한 명서입니다.

---

## 🏛️ 1. 백엔드 (`BigProject_Back_New`) 구조 및 파일 역할

### 📁 `app/core/stakeholder_mode/` (동적 이해관계자 메인 엔진)
AI가 안건, GIS 지리공간 정보, 조례 데이터를 분석하여 맞춤형 이해관계자를 자동 발굴하고 다자간 심의 토론을 수행하는 핵심 모듈입니다.

```
app/core/stakeholder_mode/
├── README.md                          # 동적 이해관계자 파이프라인 개요 및 사용법
├── graph/                             # LangGraph 에이전트 그래프 및 실행 노드
│   ├── dynamic_builder.py             # 5대 에이전트(Supervisor, Persona, Evaluator, Factchecker, Reporter) 그래프 정의
│   ├── dynamic_nodes.py               # 각 노드별 비동기 실행 로직 (지연 LLM 생성, 대화 제어, 팩트체크 등)
│   ├── dynamic_state.py               # 토론 진행 상태 데이터(DynamicDiscussionState) 스키마
│   ├── builder.py                     # [레거시] 정적 레거시 워크플로우 그래프
│   ├── nodes.py                       # [레거시] 정적 노드 실행 로직
│   └── state.py                       # [레거시] 정적 그래프 상태 스키마
├── schemas/                           # Pydantic 입출력 및 내부 데이터 스키마
│   ├── persona.py                     # PersonaConfig (페르소나 역할, 관심사, 우려사항, 수용/불가 조건, Priority 등)
│   ├── dynamic_stakeholder.py         # StakeholderCandidate (LLM이 안건/GIS/조례 분석으로 도출한 이해관계자)
│   ├── stakeholder.py                 # CandidateSite, OrdinanceContext 데이터 스키마 및 호환 프로퍼티
│   ├── factcheck.py                   # 팩트체커 판정 상태 코드 (SUPPORTED, CONTRADICTED, DISTORTED 등)
│   ├── context.py                     # 컨텍스트 정보 보조 스키마
│   ├── interest_profile.py            # 이해관계자 프로필 상세 스키마
│   └── output.py                      # 최종 평가 결과 출력 규격
├── prompts/                           # Jinja2 프롬프트 템플릿 및 렌더링 모듈
│   ├── renderer.py                    # PersonaConfig 및 조례 데이터를 Jinja2 템플릿에 안전하게 바인딩하는 헬퍼
│   ├── discovery_prompt.py            # 안건 기반 이해관계자 자동 발굴 프롬프트
│   ├── evaluation_prompt.py           # 수용도 평가 및 심의 프롬프트
│   ├── refinement_prompt.py           # 페르소나 정제 프롬프트
│   └── templates/
│       ├── persona_system.j2          # 페르소나의 관심사/우려/수용조건을 LLM 프롬프트로 변환하는 메인 템플릿
│       ├── initial_opinion.j2         # 개별 페르소나 초기의견 평가 템플릿
│       └── stakeholder/               # 이해관계자 프로필 생성 Jinja2 서브 템플릿
├── services/                          # 핵심 비즈니스 로직 및 파이프라인 연동 서비스
│   ├── stakeholder_generator.py       # 안건, GIS 데이터, 조례 데이터를 분석하여 맞춤형 이해관계자를 도출하는 LLM 서비스
│   ├── spatial_context.py             # topN.geojson과 clean_*.gpkg 파일의 200m 반경 공간 조인으로 주변 시설 요약 추출
│   ├── persona_factory.py             # 도출된 이해관계자 후보를 실행 가능한 PersonaConfig 객체로 빌드하는 서비스
│   ├── interest_profile_generator.py  # 상세 심층 프로필 생성 서비스
│   ├── persona_context_selector.py    # 페르소나별 관련 조례/공간 맥락 추려내기 서비스
│   ├── persona_diversity_validator.py # 발굴된 페르소나들의 입장 중복성/다양성 검증 서비스
│   └── validator.py                   # 입출력 데이터 유효성 검증기
└── tests/                             # stakeholder_mode 전용 단위 테스트 모듈

```

### 📁 `app/api/v1/` (API 엔드포인트 라우터)
- **`stakeholders.py`**:
  - `POST /api/v1/stakeholders/generate`: 안건/GIS/조례 입력 시 AI 이해관계자 자동 발굴 API
  - `POST /api/v1/stakeholders/dynamic/discuss/stream`: LangGraph 기반 실시간 토론 SSE 스트리밍 API
- **`report.py`**:
  - `POST /api/v1/report/download/hwpx`: 공문서 규격 HWPX 한글 보고서 바이너리 생성 및 다운로드 API

### 📁 `app/services/` (보고서 생성 서비스)
- **`hwpx_generator.py`**:
  - 행정업무운영 편람 표준 양식에 따라 HWPX XML 구조 생성 (XML 이스케이프 적용, 지도 핀 링크, AHP 가중치 표, 3대 시나리오 평가 내역 포함)

### 📁 `app/core/sim_ai/` (다자간 토론 프롬프트 모듈)
- **`multi_party_discussion_prompts.py`**:
  - 다자간 토론 공통 규칙(`COMMON_SYSTEM_PROMPT`), 갈등 민감도(`CSS_PROMPT_TEMPLATE`), 최종 보고서(`REPORTER_PROMPT`), 동적 프롬프트 생성 함수(`build_multi_party_prompt`) 포함

### 📁 `tests/` 단위/통합 테스트 스크립트

🔴 2026-08-10 에 **루트에서 `tests/` 로 옮겼다.** 파이프라인·API 가 부르지 않는
실행용 스크립트다(참조 0회 확인). 저장소 루트를 `sys.path` 에 올리는 세 줄을
같이 넣었으므로 `python tests/<파일>.py` 로 그대로 실행된다.

- **`tests/test_dynamic_discussion.py`**: 다자간 동적 토론 LangGraph 엔드투엔드(E2E) 실행 스크립트
- **`tests/test_stakeholder_generator.py`**: 안건 및 조례 기반 이해관계자 자동 도출 스크립트
- **`tests/test_spatial_persona.py`**: 지리공간 200m 버퍼 연산 및 전체 워크플로우 검증 스크립트
  - ⚠ 이 스크립트가 찾는 `shared_data/` 는 **이 저장소에 없다.** 옮기기 전에도
    "topN.geojson 파일을 찾을 수 없습니다" 로 끝나고 있었다(2026-08-10 실측).

---

## 🎨 2. 프런트엔드 (`BigProject_Front_New`) 구조 및 파일 역할

### 📁 `src/app/dynamic-hearing/` (동적 공청회 시뮬레이션 메인)
AI가 이해관계자를 도출하고, 사용자가 승인(HITL)한 후, 다자간 공청회를 진행하는 3단계 위자드 페이지입니다.

```
src/app/dynamic-hearing/
├── page.tsx                           # 3단계 위자드 메인 상태 관리 및 백엔드 SSE 스트리밍 연결 페이지
└── _components/
    ├── SetupStep.tsx                  # Step 1: 안건 주제, 설치 목적, 지자체 조례 설정 입력 폼
    ├── PersonaStep.tsx                # Step 2: AI가 도출한 이해관계자 카드 목록, 사용자 선택/승인(HITL) 및 추가 도출
    └── DiscussionStep.tsx             # Step 3: 실시간 채팅 스트리밍, 발언자 뱃지([목표], [우려] 등) 파싱, 수용도 평가 및 시나리오 리포트 카드 표시
```

### 📁 `src/app/hearing-pdf/` (공청회 결과 보고서 및 HWPX 다운로드)
심의 결과를 표준 공문서 형식으로 출력하는 리포트 전용 페이지입니다.

```
src/app/hearing-pdf/
├── page.tsx                           # PDF 인쇄(window.print) 및 백엔드 HWPX 바이너리 다운로드 통합 페이지
└── _components/
    ├── PdfReportHeader.tsx            # 공문서 두문/상단 제목 헤더 컴포넌트
    ├── CandidateInfoSection.tsx       # 후보지 위치, 카카오/네이버 지도 핀 URL, 공간 인프라 요약 정보
    ├── AhpWeightSection.tsx           # AHP 지표별 가중치 분석 결과 표/차트 컴포넌트
    ├── ScenarioEvaluationSection.tsx   # AI가 최종 도출한 3대 시나리오(A/B/C) 수용도 및 갈등위험지수 카드
    ├── PdfReportFooter.tsx            # 공문서 결문/하단 발신 기관 정보 컴포넌트
    └── types.ts                       # 보고서 데이터 인터페이스 정의서
```

---

## 🔄 3. 전체 데이터 흐름 요약

1. **[사용자] 안건 및 조례 설정 (`SetupStep.tsx`)**
   - 토론 주제와 조례 정보를 입력하고 `이해관계자 도출` 버튼을 클릭.
2. **[백엔드] 동적 이해관계자 자동 도출 (`POST /api/v1/stakeholders/generate`)**
   - `StakeholderGenerator`가 안건 주제, GIS 지리공간 200m 버퍼 정보(`spatial_context.py`), 조례 데이터를 LLM에 전달하여 맞춤형 이해관계자 후보(`StakeholderCandidate`) 목록 생성.
3. **[사용자] 페르소나 선택 및 승인 (`PersonaStep.tsx`)**
   - AI가 추천한 이해관계자 카드 중 심의에 참여시킬 페르소나 선택 (Human-In-The-Loop).
4. **[백엔드] 다자간 실시간 토론 SSE 스트리밍 (`POST /api/v1/stakeholders/dynamic/discuss/stream`)**
   - `dynamic_discussion_graph`가 작동하여 노드별 비동기 수행:
     - **Supervisor**: 다음 발언자 지목
     - **Persona Speaker**: `PersonaConfig` & `persona_system.j2` 프롬프트로 맞춤형 의견 발화
     - **Factchecker**: GIS 데이터/조례 대조 후 사실 왜곡 시 정정 개입
     - **Evaluator**: 라운드별 수용도 정량 평가 (0.0~1.0)
     - **Reporter**: 최종 3대 시나리오 예측 보고서 작성
5. **[프런트엔드] 실시간 토론 시각화 및 리포트 다운로드 (`DiscussionStep.tsx` / `hearing-pdf`)**
   - 스트리밍 발화 패킷 렌더링 ➔ 최종 심의 결과 확인 ➔ `window.print()` PDF 출력 또는 `HWPX` 한글 공문서 다운로드.
