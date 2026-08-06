# AI 페르소나 공청회 시뮬레이션 — 학습 가이드

방금 `run_ai_console_rag.py`로 직접 테스트해본 그 시스템의 원리를 정리한 문서입니다. "찬성/반대/정부 AI가 실제 조례를 인용하면서 토론하고, 마지막에 시나리오를 도출한다" — 이게 어떤 부품들로, 어떤 순서로 돌아가는지 설명합니다.

---

## 1. 이 시스템이 하는 일 (한 줄 요약)

**후보 부지 하나를 주면, 그 부지에 시설(흡연부스 등)을 설치했을 때 벌어질 법한 주민 공청회를 AI 3자(찬성 상인·반대 주민·중재 공무원)가 실제 조례를 근거로 시뮬레이션하고, 갈등 민감도 점수와 최종 타결 시나리오를 산출하는 엔진**입니다.

`CLAUDE.md`에 적힌 프로젝트 정의 그대로: "GIS 최적화(MCLP) + 다중에이전트 공청회 시뮬레이션" 중 **후자**가 이 문서의 대상입니다.

---

## 2. 전체 그림 — 데이터가 흐르는 순서

```
① 후보 부지 정보 (지번·위경도·AHP 가중치)
        │
② RAG 검색 (vector_db.py)
   ├─ 2-1. pgvector 1차 검색 (Recall) ── "관련 있을 법한 것 넉넉히"
   └─ 2-2. XGBoost Re-ranking (Precision) ── "그중 진짜 관련 있는 top-k"
        │
③ common_rag 텍스트로 조립 ("[DOC_ID: 1] 국민건강증진법 제34조...")
        │
④ LangGraph 상태기계 실행 (graph.py)
   supervisor(라우터) → pro(찬성) → supervisor → con(반대) → supervisor
   → evaluator(평가) → [3라운드 반복] → gov(정부 중재) → reporter(최종 시나리오)
        │
⑤ 각 노드가 GPT-4o-mini 호출 (prompts.py로 조립한 프롬프트 + ④의 RAG 컨텍스트 주입)
        │
⑥ reporter가 최종 JSON 산출 + used_doc_ids로 "실제 어느 조문을 근거로 썼는지" 기록
        │
⑦ (실서비스 경로에서는) ConflictSimulation 테이블에 저장 + Redis로 SSE 스트리밍
```

---

## 3. 사용된 기술 스택

| 기술 | 역할 | 우리 코드에서 |
|---|---|---|
| **LangGraph** | 여러 AI 에이전트를 "상태 기계(State Machine)"로 조율 | `graph.py`의 `StateGraph`, `build_discussion_graph()` |
| **LangChain** | LLM·벡터스토어를 감싸는 공통 인터페이스 | `ChatOpenAI`, `OpenAIEmbeddings`, `PGVector` |
| **OpenAI GPT-4o-mini** | 실제 텍스트 생성(페르소나 발언·평가·리포트) | `graph.py`의 `llm = ChatOpenAI(model="gpt-4o-mini", ...)` |
| **OpenAI text-embedding-3-small** | 텍스트 → 1536차원 벡터 변환 | `vector_db.py`의 `self.embeddings` |
| **pgvector + PGVector** | 벡터 유사도 검색 (지난번 학습한 그것) | `vector_db.py`의 `self.statutes_store` |
| **XGBoost (+ scikit-learn)** | 1차 검색 결과를 재정렬(Re-rank)하는 2단계 검색 | `xgboost_rag_service.py` |
| **Jinja2** | 역할별 프롬프트 템플릿 렌더링 | `prompts.py` → `app/templates/default/*.txt` |
| **asyncio** | 여러 LLM 호출을 순서대로 비동기 실행 | `graph.py`의 `async def ..._node` |
| **FastAPI + SSE (실서비스만)** | 토론 내용을 프론트에 실시간 스트리밍 | `simulations.py`의 `/stream` (지금은 라우터 미등록 상태) |

---

## 4. 핵심 개념 1 — 왜 검색을 2단계(RAG + Re-rank)로 하는가

지난번 pgvector 가이드에서 "코사인 유사도로 가장 가까운 K개를 찾는다"까지 배웠는데, 이 시스템은 한 단계 더 나갑니다.

```python
# vector_db.py
recall_k = max(top_k * 3, 15)          # 1단계: top_k=5인데 15개를 넉넉히 뽑음
docs_with_scores = await self.statutes_store.asimilarity_search_with_relevance_scores(query, k=recall_k)
...
final_docs = xgboost_rag_service.rerank_chunks(query, candidate_chunks, top_k=top_k)  # 2단계: 15개 중 5개로 추림
```

**왜 한 번에 5개를 안 뽑고 15개를 뽑았다가 5개로 줄이나?**

- 순수 벡터 유사도(코사인 거리)는 "의미가 비슷한 것"만 볼 뿐, "실제로 이 상황에 법적으로 적용되는 조문인지"까지는 판단 못 합니다.
- 그래서 1단계(Recall)는 **놓치지 않는 것**이 목표 — 일부러 넉넉하게(3배) 뽑습니다.
- 2단계(Re-ranking)는 **그중 진짜 중요한 것만 추리는 것**이 목표 — XGBoost 모델(또는 학습이 안 됐으면 규칙 기반 폴백)이 벡터 점수 외의 특징(예: 키워드 일치, 조문 구조)까지 반영해서 재점수를 매깁니다.

이게 바로 오늘 우리가 겪은 버그의 원인이기도 했습니다 — `xgboost` 패키지가 없으면 2단계가 통째로 실패해서, 1단계 결과까지 같이 버려지고 검색 0건이 됐던 거죠.

---

## 5. 핵심 개념 2 — LangGraph는 뭘 하는 라이브러리인가

일반 챗봇은 "질문 → LLM → 답변" 한 번으로 끝나지만, 여기는 **찬성이 말하면 반대가 반박하고, 평가자가 판정하고, 3라운드가 지나면 정부가 개입하는** 식으로 **여러 에이전트가 순서를 지키며 대화를 주고받아야** 합니다. 이 흐름 제어를 코드로 직접 짜면(if/else 지옥) 유지보수가 힘들어지는데, LangGraph는 이걸 **그래프(노드+엣지)**로 선언적으로 표현하게 해줍니다.

### AgentState — 노드들이 공유하는 "공용 칠판"

```python
class AgentState(TypedDict):
    messages: Annotated[Sequence[str], operator.add]  # 대화 로그 (자동으로 append됨)
    css_pro: str        # 찬성측 갈등 민감도 (LOW/MEDIUM/HIGH)
    css_con: str         # 반대측 갈등 민감도
    round_count: int     # 몇 라운드째인지
    current_phase: str   # "debate"(찬반토론) | "intervention"(정부개입)
    common_rag: str       # 4번 검색해온 조례 컨텍스트
    ...
```

모든 노드는 이 `AgentState`를 읽고, 자기가 바꾼 부분만 `dict`로 반환합니다. LangGraph가 알아서 병합해줍니다.

### 노드(Node) = 함수 하나

```python
async def pro_node(state: AgentState) -> dict:
    prompt = build_prompt(role_prompt=PRO_ROLE_PROMPT, ..., rag_context=state["common_rag"], ...)
    response = await llm.ainvoke([SystemMessage(content=prompt)])
    return {"messages": [f"찬성: {response.content}"], "spoken_this_round": [...]}
```

노드 하나 = "이 시점에 어떤 페르소나가 무슨 말을 할지 LLM에게 묻고, 결과를 상태에 기록"하는 함수입니다.

### 라우터(Supervisor) — 다음 차례를 결정하는 노드

```python
async def supervisor_node(state: AgentState) -> dict:
    if phase == "debate":
        if not spoken: return {"next_speaker": "pro"}
        elif spoken == ["pro"]: return {"next_speaker": "con"}
        else: return {"next_speaker": "evaluator"}
    elif phase == "intervention":
        ...  # gov → pro → con → gov_wrapup → reporter
```

이게 흔히 말하는 **"멀티에이전트 오케스트레이션"**입니다. 매 스텝마다 `supervisor_node`가 "다음은 누구 차례인가"를 결정론적으로(코드로, LLM 없이) 판단하고, `route_next()`가 그 결정을 실제 그래프의 다음 노드로 연결합니다.

### 그래프 조립

```python
workflow = StateGraph(AgentState)
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("pro", pro_node)
...
workflow.set_entry_point("supervisor")
workflow.add_conditional_edges("supervisor", route_next, {"pro": "pro", "con": "con", ...})
workflow.add_edge("pro", "supervisor")   # pro가 끝나면 다시 라우터로
...
workflow.add_edge("reporter", END)       # reporter가 끝나면 종료
```

**핵심 패턴**: 모든 페르소나 노드는 일을 마치면 다시 `supervisor`로 돌아갑니다(바퀴살 구조). `supervisor`가 매번 "지금까지 누가 말했는지(`spoken_this_round`)"와 "현재 페이즈"를 보고 다음 발언자를 정하기 때문에, 3라운드 찬반 → 평가 → 정부개입 → 종료라는 복잡한 흐름이 **재귀적으로 반복**됩니다.

---

## 6. 핵심 개념 3 — 프롬프트는 어떻게 조립되는가

```
common_system_prompt.txt   (후보지 정보·RAG 컨텍스트·감사 데이터·토론 이력 — 공통)
        +
css_{high,medium,low}.txt  (갈등 수준별 "얼마나 강하게 반박할지" 행동 지침)
        +
{pro,con,gov}_role.txt     (그 페르소나만의 역할 설명)
        ↓
   build_prompt()가 셋을 이어붙여 최종 SystemMessage 생성
```

`css_level`(갈등 민감도)에 따라 같은 찬성 페르소나라도 "순순히 타협하는 톤"과 "강하게 밀어붙이는 톤"이 갈립니다. 이 값은 `evaluator_node`가 매 라운드 수용도 점수를 보고 자동으로 갱신합니다(`_map_css_by_score`).

---

## 7. 오늘 우리가 실행한 트레이스 그대로 따라가기

```
1. RagVectorStorage() 생성 → Postgres(pgvector) 연결
2. retrieve_similar_statutes("설치 기준 허가 규제 갈등 중재 혜택", facility_type="흡연부스")
     → 1단계: 15건 후보 (코사인 유사도 0.35~0.37대)
     → 2단계: XGBoost Re-rank → 최종 5건 (국민건강증진법 제34조 등)
3. common_rag = "[DOC_ID: 1] ... [DOC_ID: 5] ..."
4. build_discussion_graph() 컴파일
5. graph.astream(initial_state) 실행
     supervisor → pro (찬성 발언, common_rag 참고해서 "제34조에 따라..." 인용)
     → supervisor → con (반대 발언)
     → supervisor → evaluator (수용도 채점, css_pro/css_con 갱신)
     → (round_count < 3 이면 다시 pro로) ... 3라운드 반복
     → phase가 "intervention"으로 전환
     → gov → pro → con → gov_wrapup
     → reporter (최종 시나리오 JSON + used_doc_ids=[1,2] 산출)
6. END
```

`used_doc_ids: [1, 2]`가 찍힌다는 건, `reporter_node`가 LLM에게 "너가 실제로 인용한 DOC_ID를 알려달라"고 요청했고, LLM이 그중 1번·2번 문서를 실제로 근거 삼았다고 답했다는 뜻입니다. 이게 실서비스 경로(`simulations.py`)에서는 `RagFeedbackLog` 테이블에 "이 조문이 실제로 쓰였는지(label=1/0)"로 기록되어, 나중에 검색 품질 개선(XGBoost 재학습)에 쓰일 재료가 됩니다.

---

## 8. 파일별 역할 지도

| 파일 | 역할 |
|---|---|
| `app/core/sim_ai/vector_db.py` | `RagVectorStorage` — pgvector 연결, 2단계 RAG 검색 |
| `app/core/sim_ai/graph.py` | LangGraph 상태기계 정의 — 노드·라우팅·그래프 조립 |
| `app/core/sim_ai/prompts.py` | 역할별 프롬프트 조립 (`build_prompt`) |
| `app/templates/default/*.txt` | Jinja2 프롬프트 템플릿 원문 |
| `app/services/xgboost_rag_service.py` | 2단계 Re-ranking 모델(또는 규칙 기반 폴백) |
| `run_ai_console_rag.py` | **콘솔에서 서버 없이 통째로 테스트**하는 진입점(오늘 쓴 것) |
| `app/api/v1/simulations.py` | 실서비스 API 경로 — SSE 스트리밍 + DB 저장까지 포함 (지금은 `main.py`에서 라우터 미등록) |

---

## 9. 콘솔 스크립트 vs 실서비스 API — 뭐가 다른가

| | `run_ai_console_rag.py` | `simulations.py` (`/simulations/stream`) |
|---|---|---|
| 실행 방식 | 터미널에서 직접 `python` 실행 | FastAPI HTTP 엔드포인트 |
| 출력 방식 | 완료된 노드 단위로 print | 토큰 단위 실시간 SSE 스트리밍 (`on_chat_model_stream`) |
| 후보지 데이터 | 코드에 하드코딩(`CANDIDATE` dict) | DB(`parcels` 테이블)에서 조회 |
| 결과 저장 | 안 함 | `ConflictSimulation` 테이블에 저장 + Redis 10분 캐시 |
| 지금 상태 | ✅ 정상 작동 확인됨 | 🔴 라우터 미등록(import 시점 DB 접속 문제로 임시 비활성화, `CLAUDE.md` 참조) |

즉 오늘 테스트한 건 **엔진 자체(LangGraph + RAG)가 정상 작동하는지**를 확인한 것이고, 이걸 실제 웹 서비스로 노출하려면 `main.py`의 라우터 등록 작업이 별도로 더 필요합니다.

---

## 10. 참고 자료

- [LangGraph 공식 문서](https://langchain-ai.github.io/langgraph/) — StateGraph, 조건부 엣지 개념
- [LangChain PGVector 통합](https://python.langchain.com/docs/integrations/vectorstores/pgvector/)
- [XGBoost 공식 문서](https://xgboost.readthedocs.io/) — Re-ranking에 쓰인 `predict_proba` 개념
- 프로젝트 내부: [벡터DB_pgvector_학습가이드.md](벡터DB_pgvector_학습가이드.md) (1단계 검색의 기반 개념)
- 프로젝트 내부: `CLAUDE.md` (이 시스템의 알려진 함정·설계 결정 전체)
