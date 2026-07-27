# 🎭 이해관계자 페르소나 모드 (Stakeholder Persona Mode)

본 모듈은 감리 AI가 정리한 후보지 데이터와 조례 RAG 결과를 입력받아 사용자가 이해관계자를 선택하고, 선택된 이해관계자를 AI 페르소나로 생성하여 각자의 관점에서 후보지를 독립 평가하도록 지원하는 별도의 모의 심의 파이프라인입니다.

---

## 📌 1. 개발 진행 상황 (Status)

- [x] **1단계 MVP 데이터 모델 설계 (`schemas/`)**: Pydantic 기반 입력/설정/출력 규격 정의 완료
- [x] **Jinja2 동적 프롬프트 렌더러 구축 (`prompts/`)**: `PersonaConfig` 데이터 바인딩 템플릿 및 로더 작성 완료
- [x] **LLM 서비스 레이어 구축 (`services/`)**: 추천, 페르소나 생성, 독립 평가 비동기 로직 완료
- [x] **LangGraph 7단계 워크플로우 구축 (`graph/`)**: StateGraph 노드 및 파이프라인 빌드 완료
- [x] **단위 테스트 스위트 작성 (`tests/`)**: 스키마 파싱 및 Jinja2 렌더링 테스트 통과 (`pytest`)
- [ ] **2단계~7단계 확장 (차후 진행 예정)**: 중요도 가중치, 상호 토론, 진행자 AI, 외부 검색, 검증 AI 등

> 💡 **개발 원칙**: 기존 찬반 토론 파이프라인(`app/core/sim_ai/`) 및 기존 백엔드 파일에 영향을 전혀 주지 않는 **완전 격리된 사이드 모듈**로 작성되었습니다.

---

## 📁 2. 전체 디렉터리 구조

```text
app/core/stakeholder_mode/
├── schemas/                      # Pydantic 데이터 구조 정의
│   ├── stakeholder.py           # 감리 AI 입력 데이터, 조례 Context, 추천 이해관계자 모델
│   ├── persona.py               # MVP PersonaConfig 설정 모델
│   └── output.py                # 페르소나별 독립 평가(PersonaOpinion) 및 모드 집계 결과(StakeholderModeResult)
├── prompts/                      # Jinja2 동적 프롬프트 관리
│   ├── templates/
│   │   ├── persona_system.j2    # 페르소나 역할/관심사/우려사항/수용조건 바인딩 템플릿
│   │   └── initial_opinion.j2   # 후보지 및 조례 Context 바인딩 유저 평가 템플릿
│   └── renderer.py              # Jinja2 Environment 로더 및 렌더링 헬퍼 함수
├── services/                     # LLM 비동기 서비스 레이어
│   ├── stakeholder_recommender.py # 3~5개 이해관계자 LLM 자동 추천 서비스
│   ├── persona_factory.py        # 선택된 이해관계자 기반 PersonaConfig 변환 생성 서비스
│   └── persona_executor.py       # 페르소나별 비동기 독립 평가 실행 서비스
├── graph/                        # LangGraph 워크플로우
│   ├── state.py                  # StakeholderGraphState TypedDict 정의
│   ├── nodes.py                  # 7개 노드 함수 (receive_input ~ aggregate_results)
│   └── builder.py                # StateGraph 빌더 및 컴파일러
├── samples/                      # 테스트용 샘플 데이터
│   └── sample_input.json        # 감리 AI 데이터 + 조례 Context 입력 샘플 JSON
└── tests/                        # 단위 테스트 스위트
    ├── test_persona_factory.py   # 스키마 및 Pydantic 파싱 유효성 테스트
    └── test_prompt_renderer.py   # Jinja2 템플릿 바인딩 렌더링 검증 테스트
```

---

## 📄 3. 파일별 역할 상세 설명

### 🔹 `schemas/` (데이터 구조)
- **`stakeholder.py`**:
  - `CandidateSite`: 후보지 ID, 명칭, 속성 정보(접근성, 예산 등)
  - `OrdinanceContext`: 조례 청크 ID, 조례명, 텍스트 내용
  - `StakeholderCandidate`: AI 추천 이해관계자 (이름, 유형, 관계, 추천사유)
  - `StakeholderModeInput`: 전체 입력 페이로드 (`project_id`, `topic`, `candidate_sites`, `ordinance_contexts`)
- **`persona.py`**:
  - `PersonaConfig`: 페르소나 설정 객체 (`persona_id`, `display_name`, `interests`, `concerns`, `initial_position`, `acceptable_conditions`, `evidence_ids`, `ordinance_chunk_ids`)
- **`output.py`**:
  - `PersonaOpinion`: 페르소나별 독립 평가 결과 (`preferred_candidate_id`, `position`, `benefits`, `concerns`, `required_conditions`, `evidence_ids`)
  - `StakeholderModeResult`: 모드 전체 최종 집계 결과 객체

### 🔹 `prompts/` (프롬프트 템플릿 & Jinja2 렌더러)
- **`templates/persona_system.j2`**: `PersonaConfig`를 주입받아 페르소나의 신분, 관심사, 우려사항, 수용조건을 동적으로 정의하는 시스템 프롬프트
- **`templates/initial_opinion.j2`**: 후보지 데이터와 조례 내용을 제공하여 독립적인 후보지 평가 작성을 지시하는 유저 프롬프트
- **`renderer.py`**: Jinja2 환경 로더 및 렌더링 헬퍼 함수 (`render_persona_system_prompt`, `render_initial_opinion_prompt`)

### 🔹 `services/` (LLM 비동기 서비스)
- **`stakeholder_recommender.py`**: 입력 안건/후보지/조례 데이터를 분석해 3~5개 이해관계자를 LLM 추천 (`recommend_stakeholders`)
- **`persona_factory.py`**: 사용자가 선택/수정한 이해관계자 목록을 바탕으로 구체적인 `PersonaConfig` 객체 목록 생성 (`create_persona_configs`)
- **`persona_executor.py`**: 각 페르소나에 대해 비동기 병렬로 LLM 평가를 수행하여 `PersonaOpinion` 생성 (`execute_all_personas`)

### 🔹 `graph/` (LangGraph 파이프라인)
- **`state.py`**: LangGraph 노드 간 전달되는 `StakeholderGraphState` 정의
- **`nodes.py`**: 7개 실행 노드 구현 (`receive_input`, `recommend_stakeholders`, `review_stakeholders`, `create_personas`, `render_prompts`, `run_personas`, `aggregate_results`)
- **`builder.py`**: `StateGraph` 순차 연결 및 `stakeholder_graph` 컴파일

### 🔹 `samples/` & `tests/`
- **`samples/sample_input.json`**: 개발 테스트용 모의 안건 및 조례 입력 데이터
- **`tests/test_persona_factory.py`**: Pydantic 모델 및 스키마 유효성 테스트
- **`tests/test_prompt_renderer.py`**: Jinja2 템플릿 바인딩 렌더링 단위 테스트

---

## 🔄 4. LangGraph 워크플로우 실행 흐름 (MVP)

```text
START
  ↓
receive_input              : 감리 AI 데이터 및 조례 결과 수신/파싱
  ↓
recommend_stakeholders     : LLM 기반 이해관계자 3~5개 추천
  ↓
review_stakeholders        : 사용자 선택 및 수정 (HITL 단계)
  ↓
create_personas            : PersonaConfig 설정 객체 생성
  ↓
render_prompts             : Jinja2 프롬프트 동적 렌더링
  ↓
run_personas               : 각 페르소나별 독립 평가 비동기 실행
  ↓
aggregate_results          : 모든 평가 결과를 하나의 JSON 결과로 모음
  ↓
END
```

---

## 🧪 5. 테스트 실행 방법

```bash
# 이해관계자 페르소나 모드 전체 단위 테스트 실행
python -m pytest app/core/stakeholder_mode/tests/
```
