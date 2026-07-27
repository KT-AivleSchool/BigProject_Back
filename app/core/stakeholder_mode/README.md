# 🎭 이해관계자 페르소나 모드 (Stakeholder Persona Mode)

본 모듈은 감리 AI가 정리한 후보지 데이터와 조례 RAG 결과를 입력받아 사용자가 이해관계자를 선택하고, 선택된 이해관계자를 AI 페르소나로 생성하여 각자의 관점에서 후보지를 평가하도록 지원하는 별도의 모의 심의 파이프라인입니다.

---

## 📌 1. 개발 진행 상황 (Status)

- [x] **1단계 MVP 데이터 모델 및 독립 평가 구축 완료**
- [x] **2단계 1차 확장 기능 구현 완료 (`persona_expansion_phase2` 브랜치)**:
  - [x] **중요도 등급 및 참여 유형**: `importance_grade` (A~D), `participation_type` (essential, optional, reference)
  - [x] **우선순위 가중치**: 관심사별 `Priority(criterion, weight)` 추가
  - [x] **절대 수용 불가 조건**: `non_negotiable_conditions` (Non-negotiable) 배제 조건 추가
  - [x] **후보지 정량 점수화**: 후보지별 100점 만점 평가 점수(`CandidateScore`) 및 사유 산출
  - [x] **근거/조례 ID 무결성 검증 엔진 (`services/validator.py`)**: 존재하지 않는 ID 참조 탐지 및 `ValidationIssue` 생성
- [x] **단위 테스트 스위트 확장 (`tests/`)**: Pydantic 스키마, Jinja2 프롬프트 렌더링, 검증 엔진 테스트 통과 (4/4 passed)

---

## 📁 2. 전체 디렉터리 구조

```text
app/core/stakeholder_mode/
├── schemas/                      # Pydantic 데이터 구조 정의
│   ├── stakeholder.py           # 입력 데이터, 조례 Context, 추천 이해관계자(연관성 점수 포함)
│   ├── persona.py               # Phase 2 확장 PersonaConfig (중요도/가중치/절대조건)
│   └── output.py                # 후보지 정량 점수(CandidateScore), 근거 검증(ValidationIssue) 포함
├── prompts/                      # Jinja2 동적 프롬프트 관리
│   ├── templates/
│   │   ├── persona_system.j2    # 가중치 및 절대조건이 바인딩된 시스템 프롬프트
│   │   └── initial_opinion.j2   # 후보지 정량 점수 산출 지시 유저 프롬프트
│   └── renderer.py              # Jinja2 Environment 로더 및 렌더링 헬퍼 함수
├── services/                     # LLM 비동기 서비스 및 검증 모듈
│   ├── stakeholder_recommender.py # 연관성 높은 순 이해관계자 LLM 자동 추천
│   ├── persona_factory.py        # 중요도/가중치/절대조건이 설정된 PersonaConfig 팩토리
│   ├── persona_executor.py       # 후보지 점수화가 포함된 비동기 독립 평가 실행기
│   └── validator.py              # 근거 ID 및 조례 Chunk ID 유효성 검증 엔진 (Phase 2)
├── graph/                        # LangGraph 워크플로우
│   ├── state.py                  # StakeholderGraphState (validation_issues 추가)
│   ├── nodes.py                  # 7개 노드 함수 (검증 엔진 통합)
│   └── builder.py                # StateGraph 빌더 및 컴파일러
├── samples/                      # 테스트용 샘플 데이터
│   └── sample_input.json        # 감리 AI 데이터 + 조례 Context 입력 샘플 JSON
└── tests/                        # 단위 테스트 스위트 (4개 통과)
    ├── test_persona_factory.py   # 스키마 및 Pydantic 파싱 유효성 테스트
    ├── test_prompt_renderer.py   # Jinja2 템플릿 바인딩 렌더링 검증 테스트
    └── test_validator.py         # 근거/조례 ID 검증 엔진 단위 테스트 (Phase 2)
```

---

## 🧪 3. 테스트 실행 방법

```bash
# 이해관계자 페르소나 모드 전체 단위 테스트 실행
python -m pytest app/core/stakeholder_mode/tests/
```
