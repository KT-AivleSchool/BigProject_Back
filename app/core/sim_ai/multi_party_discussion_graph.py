import operator
import json
from typing import TypedDict, Annotated, Sequence, List, Dict
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.core.sim_ai.multi_party_discussion_prompts import (
    build_multi_party_prompt,
    RESIDENT_ROLE_PROMPT,
    MERCHANT_ROLE_PROMPT,
    OFFICER_ROLE_PROMPT,
    EVALUATOR_PROMPT,
    REPORTER_PROMPT,
    ECOLOGIST_ROLE_PROMPT,
    YOUTH_ROLE_PROMPT,
    SAFETY_OFFICER_ROLE_PROMPT
)
from app.core.sim_ai.vector_db import RagVectorStorage
from app.config import settings

# 1. 다자간 대화 상태 (Multi-Party Agent State)
class MultiPartyAgentState(TypedDict):
    messages: Annotated[Sequence[str], operator.add]  # 대화 이력 누적
    site_information: str                             # 입지 및 시설 데이터
    active_participants: List[str]                    # 현재 회의 참여 페르소나 리스트
    css_levels: Dict[str, str]                        # 페르소나별 갈등 민감도 ("resident": "HIGH", ...)
    round_count: int                                  # 토론 진행 라운드 수
    evaluations: dict                                 # 수용도 정량 평가 결과
    final_scenarios: dict                             # 최종 3대 시나리오 결과
    is_finished: bool                                 # 세션 종료 여부
    next_speaker: str                                 # 라우터가 지정한 다음 발화자

# 전역 LLM 및 Vector DB 인스턴스
llm = ChatOpenAI(api_key=settings.OPENAI_API_KEY, model="gpt-4o-mini", temperature=0.7)
vector_db = RagVectorStorage()

def _format_history(messages: Sequence[str]) -> str:
    return "\n".join(messages)

# 2. 다자간 사회자/라우터 노드 (Supervisor)
async def multi_party_router_node(state: MultiPartyAgentState) -> dict:
    """참여 중인 페르소나들 중 대화 맥락상 다음 발화자를 동적으로 선택"""
    history_text = _format_history(state.get("messages", []))
    participants = state.get("active_participants", ["resident", "merchant", "officer", "ecologist", "youth", "safety_officer"])
    
    if not history_text:
        return {"next_speaker": "resident"}
        
    system_msg = f"""당신은 다자간 심의위원회의 사회자(Supervisor)입니다.
현재 참여 중인 페르소나 리스트: {participants}

이전 대화 내용을 검토한 후, 토론의 활성화 및 논의 진전을 위해 다음에 가장 발언이 필요한 페르소나를 1명 선택하세요.
선택 가능한 키워드:
- resident (주민대표)
- merchant (상인대표)
- officer (조정공무원)
- ecologist (환경운동가)
- youth (청년/입주민)
- safety_officer (교통안전관)
- evaluator (평가자 - 주요 참가자 발언 완료 시 선택)

규칙: 주요 참가자들이 이번 라운드에 충분히 발언을 마쳤다면 반드시 'evaluator'를 선택하세요.
오직 위 키워드 중 하나만 단어로 출력하세요."""

    response = await llm.ainvoke([
        SystemMessage(content=system_msg),
        HumanMessage(content=f"이전 대화:\n{history_text}\n\n다음 발언자 선택:")
    ])
    
    next_speaker = response.content.strip().lower()
    valid_speakers = participants + ["evaluator"]
    if next_speaker not in valid_speakers:
        next_speaker = "evaluator"
        
    return {"next_speaker": next_speaker}

# 3. 페르소나별 실행 노드 생성기 헬퍼
async def _execute_persona_node(
    state: MultiPartyAgentState,
    role_name: str,
    role_title: str,
    role_prompt: str,
    default_query_suffix: str
) -> dict:
    css_dict = state.get("css_levels", {})
    css_level = css_dict.get(role_name, "HIGH")
    site_info = state.get("site_information", "입지 정보 미지정")
    history_text = _format_history(state.get("messages", []))
    
    query = f"{site_info} {default_query_suffix}" if not history_text else history_text[-200:]
    retrieved_docs = await vector_db.retrieve_similar_statutes(query)
    
    prompt = build_multi_party_prompt(
        role_prompt=role_prompt,
        site_information=site_info,
        rag_context="\n".join(retrieved_docs),
        discussion_history=history_text,
        css_level=css_level
    )
    
    response = await llm.ainvoke([SystemMessage(content=prompt)])
    return {"messages": [f"{role_title}: {response.content}"]}

# 각 페르소나 노드 함수 정의
async def resident_node(state: MultiPartyAgentState) -> dict:
    return await _execute_persona_node(state, "resident", "주민대표", RESIDENT_ROLE_PROMPT, "주거 소음 반대")

async def merchant_node(state: MultiPartyAgentState) -> dict:
    return await _execute_persona_node(state, "merchant", "상인대표", MERCHANT_ROLE_PROMPT, "상권 활성화 찬성")

async def officer_node(state: MultiPartyAgentState) -> dict:
    return await _execute_persona_node(state, "officer", "조정공무원", OFFICER_ROLE_PROMPT, "도시계획 중재")

async def ecologist_node(state: MultiPartyAgentState) -> dict:
    return await _execute_persona_node(state, "ecologist", "환경운동가", ECOLOGIST_ROLE_PROMPT, "녹지 환경보전")

async def youth_node(state: MultiPartyAgentState) -> dict:
    return await _execute_persona_node(state, "youth", "청년대표", YOUTH_ROLE_PROMPT, "스마트 편의시설")

async def safety_officer_node(state: MultiPartyAgentState) -> dict:
    return await _execute_persona_node(state, "safety_officer", "교통안전관", SAFETY_OFFICER_ROLE_PROMPT, "도로교통 안전")

# 4. 평가자 및 리포터 노드
async def evaluator_node(state: MultiPartyAgentState) -> dict:
    history_text = _format_history(state.get("messages", []))
    round_count = state.get("round_count", 0) + 1
    
    llm_json = llm.bind(response_format={"type": "json_object"})
    response = await llm_json.ainvoke([
        SystemMessage(content=EVALUATOR_PROMPT),
        HumanMessage(content=f"이전 대화:\n{history_text}\n\n평가 JSON 작성:")
    ])
    
    try:
        evals = json.loads(response.content)
    except:
        evals = {}
        
    def score_to_css(score: float) -> str:
        if score < 0.3: return "HIGH"
        elif score < 0.7: return "MEDIUM"
        else: return "LOW"
        
    css_levels = {
        "resident": score_to_css(evals.get("resident_acceptance", 0.0)),
        "merchant": score_to_css(evals.get("merchant_acceptance", 0.0)),
        "officer": score_to_css(evals.get("officer_acceptance", 0.0)),
        "ecologist": score_to_css(evals.get("ecologist_acceptance", 0.0)),
        "youth": score_to_css(evals.get("youth_acceptance", 0.0)),
        "safety_officer": score_to_css(evals.get("safety_acceptance", 0.0)),
    }
    
    return {
        "evaluations": evals,
        "round_count": round_count,
        "css_levels": css_levels
    }

async def reporter_node(state: MultiPartyAgentState) -> dict:
    history_text = _format_history(state.get("messages", []))
    
    llm_json = llm.bind(response_format={"type": "json_object"})
    response = await llm_json.ainvoke([
        SystemMessage(content=REPORTER_PROMPT),
        HumanMessage(content=f"전체 토론 내역:\n{history_text}\n\n최종 시나리오 보고서 JSON 작성:")
    ])
    
    try:
        scenarios = json.loads(response.content)
    except:
        scenarios = {}
        
    return {"final_scenarios": scenarios, "is_finished": True}

# 5. 분기 조건 헬퍼 함수
def route_next_speaker(state: MultiPartyAgentState) -> str:
    return state.get("next_speaker", "resident")

def check_evaluation_status(state: MultiPartyAgentState) -> str:
    evals = state.get("evaluations", {})
    officer_acc = evals.get("officer_acceptance", 0.0)
    round_cnt = state.get("round_count", 0)
    
    if officer_acc >= 0.8 or round_cnt >= 3:
        return "reporter"
    return "supervisor"

# 6. 다자간 그래프 빌드 함수
def build_multi_party_discussion_graph():
    workflow = StateGraph(MultiPartyAgentState)
    
    # 노드 등록
    workflow.add_node("supervisor", multi_party_router_node)
    workflow.add_node("resident", resident_node)
    workflow.add_node("merchant", merchant_node)
    workflow.add_node("officer", officer_node)
    workflow.add_node("ecologist", ecologist_node)
    workflow.add_node("youth", youth_node)
    workflow.add_node("safety_officer", safety_officer_node)
    workflow.add_node("evaluator", evaluator_node)
    workflow.add_node("reporter", reporter_node)
    
    workflow.set_entry_point("supervisor")
    
    # 라우터 조건부 분기
    workflow.add_conditional_edges(
        "supervisor",
        route_next_speaker,
        {
            "resident": "resident",
            "merchant": "merchant",
            "officer": "officer",
            "ecologist": "ecologist",
            "youth": "youth",
            "safety_officer": "safety_officer",
            "evaluator": "evaluator"
        }
    )
    
    # 각 발화 후 다시 라우터로
    for node in ["resident", "merchant", "officer", "ecologist", "youth", "safety_officer"]:
        workflow.add_edge(node, "supervisor")
        
    # 평가자 점수 후 보고서 작성 혹은 재라운드
    workflow.add_conditional_edges(
        "evaluator",
        check_evaluation_status,
        {
            "reporter": "reporter",
            "supervisor": "supervisor"
        }
    )
    
    workflow.add_edge("reporter", END)
    
    return workflow.compile()
