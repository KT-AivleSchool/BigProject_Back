import operator
from typing import TypedDict, Annotated, Sequence
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import json

from app.core.sim_ai.prompts import (
    build_prompt,
    RESIDENT_ROLE_PROMPT,
    MERCHANT_ROLE_PROMPT,
    OFFICER_ROLE_PROMPT,
    EVALUATOR_PROMPT,
    REPORTER_PROMPT
)
from app.core.sim_ai.vector_db import RagVectorStorage
from app.config import settings

# [동현님 담당] LangGraph에서 노드 간에 전송될 대화 상태 객체 정의
class AgentState(TypedDict):
    # operator.add를 사용하여 배열에 자동으로 추가되도록 설정 (LangGraph 표준)
    messages: Annotated[Sequence[str], operator.add]
    site_information: str      # 시설 입지 정보 (GIS 정량적 결과)
    css_resident: str          # 주민 갈등 민감도 (HIGH / MEDIUM / LOW)
    css_merchant: str          # 상인 갈등 민감도
    css_officer: str           # 공무원 갈등 민감도
    round_count: int           # 토론 반복 횟수 (최대 3라운드)
    evaluations: dict          # 내부 평가 결과 (수용도)
    final_scenarios: dict      # 도출된 최종 시나리오 결과 객체
    is_finished: bool          # 토론 종결 여부
    next_speaker: str          # 라우터가 결정한 다음 발화자

# LLM 및 Vector DB 전역 인스턴스 (온도는 창의적 역할극을 위해 0.7 유지)
llm = ChatOpenAI(api_key=settings.OPENAI_API_KEY, model="gpt-4o-mini", temperature=0.7)
vector_db = RagVectorStorage()

def _format_chat_history(messages: Sequence[str]) -> str:
    return "\n".join(messages)

# 1. 라우터 (사회자) 노드
async def router_node(state: AgentState) -> dict:
    """대화 문맥을 보고 다음에 발언할 페르소나를 결정합니다."""
    history_text = _format_chat_history(state.get("messages", []))
    
    if not history_text:
        return {"next_speaker": "resident"}
        
    system_msg = """당신은 토론의 사회자(Supervisor)입니다. 
지금까지의 대화 내역을 보고 다음에 가장 발언이 필요한 페르소나를 결정하세요.
선택 가능 항목 (반드시 아래 영문 키워드 중 하나만 출력): 
- resident
- merchant
- officer
- evaluator

[라우팅 규칙]
주민대표, 상인대표, 조정공무원이 이번 라운드에 최소 1번씩 발언을 마쳤다면 반드시 'evaluator'를 선택하여 평가를 진행하세요.
절대 다른 설명 없이 오직 선택 항목 중 하나의 단어(resident, merchant, officer, evaluator)만 출력하세요."""
    
    response = await llm.ainvoke([
        SystemMessage(content=system_msg),
        HumanMessage(content=f"이전 대화:\n{history_text}\n\n다음 발언자를 선택하세요:")
    ])
    
    next_node = response.content.strip().lower()
    if next_node not in ["resident", "merchant", "officer", "evaluator"]:
        next_node = "evaluator"
        
    return {"next_speaker": next_node}

# 2. 페르소나 노드들
async def resident_node(state: AgentState) -> dict:
    """주민대표 (반대 페르소나) 노드"""
    css_level = state.get("css_resident", "HIGH").upper()
    site_info = state.get("site_information", "입지 정보 없음")
    history_text = _format_chat_history(state.get("messages", []))
    
    query = f"{site_info} 반대" if not history_text else history_text[-200:]
    retrieved_docs = await vector_db.retrieve_similar_statutes(query)
    
    prompt = build_prompt(
        role_prompt=RESIDENT_ROLE_PROMPT,
        site_information=site_info,
        rag_context="\n".join(retrieved_docs),
        discussion_history=history_text,
        css_level=css_level
    )
    
    response = await llm.ainvoke([SystemMessage(content=prompt)])
    return {"messages": [f"주민대표: {response.content}"]}

async def merchant_node(state: AgentState) -> dict:
    """상인대표 (찬성 페르소나) 노드"""
    css_level = state.get("css_merchant", "HIGH").upper()
    site_info = state.get("site_information", "입지 정보 없음")
    history_text = _format_chat_history(state.get("messages", []))
    
    query = f"{site_info} 상권 활성화" if not history_text else history_text[-200:]
    retrieved_docs = await vector_db.retrieve_similar_statutes(query)
    
    prompt = build_prompt(
        role_prompt=MERCHANT_ROLE_PROMPT,
        site_information=site_info,
        rag_context="\n".join(retrieved_docs),
        discussion_history=history_text,
        css_level=css_level
    )
    
    response = await llm.ainvoke([SystemMessage(content=prompt)])
    return {"messages": [f"상인대표: {response.content}"]}

async def officer_node(state: AgentState) -> dict:
    """조정공무원 (중재 페르소나) 노드"""
    css_level = state.get("css_officer", "HIGH").upper()
    site_info = state.get("site_information", "입지 정보 없음")
    history_text = _format_chat_history(state.get("messages", []))
    
    query = f"{site_info} 중재안 조례" if not history_text else history_text[-200:]
    retrieved_docs = await vector_db.retrieve_similar_statutes(query)
    
    prompt = build_prompt(
        role_prompt=OFFICER_ROLE_PROMPT,
        site_information=site_info,
        rag_context="\n".join(retrieved_docs),
        discussion_history=history_text,
        css_level=css_level
    )
    
    response = await llm.ainvoke([SystemMessage(content=prompt)])
    return {"messages": [f"조정공무원: {response.content}"]}

# 3. 평가 및 최종 노드
async def evaluator_node(state: AgentState) -> dict:
    """내부 평가 노드 (1라운드 종료 시점)"""
    history_text = _format_chat_history(state.get("messages", []))
    round_count = state.get("round_count", 0) + 1
    
    # JSON 모드로 강제하여 평가 점수 도출
    llm_json = llm.bind(response_format={"type": "json_object"})
    response = await llm_json.ainvoke([
        SystemMessage(content=EVALUATOR_PROMPT),
        HumanMessage(content=f"이전 대화:\n{history_text}\n\n위 대화 내용을 바탕으로 평가 JSON을 반환하세요.")
    ])
    
    try:
        evals = json.loads(response.content)
    except:
        evals = {"resident_acceptance": 0.0, "merchant_acceptance": 0.0, "officer_acceptance": 0.0}
        
    # CSS 유동적 변경 로직: 각 페르소나의 개별 수용도를 기준으로 민감도 개별 조정
    def get_css(acc: float) -> str:
        if acc < 0.3: return "HIGH"
        elif acc < 0.7: return "MEDIUM"
        else: return "LOW"
        
    return {
        "evaluations": evals, 
        "round_count": round_count, 
        "css_resident": get_css(evals.get("resident_acceptance", 0.0)),
        "css_merchant": get_css(evals.get("merchant_acceptance", 0.0)),
        "css_officer": get_css(evals.get("officer_acceptance", 0.0))
    }

async def reporter_node(state: AgentState) -> dict:
    """토론 종료 후 최종 시나리오 도출 노드"""
    history_text = _format_chat_history(state.get("messages", []))
    
    llm_json = llm.bind(response_format={"type": "json_object"})
    response = await llm_json.ainvoke([
        SystemMessage(content=REPORTER_PROMPT),
        HumanMessage(content=f"전체 토론 내용:\n{history_text}\n\n위 대화 내용을 바탕으로 최종 시나리오 JSON을 도출하세요.")
    ])
    
    try:
        final_scenarios = json.loads(response.content)
    except:
        final_scenarios = {}
        
    return {"final_scenarios": final_scenarios, "is_finished": True}

# 4. 조건부 분기 (Edge Routing) 함수
def route_next(state: AgentState) -> str:
    """라우터 노드가 결정한 다음 발화자로 이동"""
    return state.get("next_speaker", "resident")

def check_evaluation(state: AgentState) -> str:
    """평가 점수에 따라 토론 종료 여부 결정"""
    evals = state.get("evaluations", {})
    officer_acceptance = evals.get("officer_acceptance", 0.0)
    round_count = state.get("round_count", 0)
    
    # 0.8 이상이거나 3라운드 이상이면 토론 종료
    if officer_acceptance >= 0.8 or round_count >= 3:
        return "reporter"
    # 0.8 미만이면 다음 라운드를 위해 라우터로 복귀
    return "supervisor"

# 5. 그래프 빌드 함수
def build_discussion_graph():
    workflow = StateGraph(AgentState)
    
    workflow.add_node("supervisor", router_node)
    workflow.add_node("resident", resident_node)
    workflow.add_node("merchant", merchant_node)
    workflow.add_node("officer", officer_node)
    workflow.add_node("evaluator", evaluator_node)
    workflow.add_node("reporter", reporter_node)
    
    # 시작점은 무조건 라우터(사회자)
    workflow.set_entry_point("supervisor")
    
    # 라우터 -> 페르소나 또는 평가 노드
    workflow.add_conditional_edges(
        "supervisor",
        route_next,
        {
            "resident": "resident",
            "merchant": "merchant",
            "officer": "officer",
            "evaluator": "evaluator"
        }
    )
    
    # 각 페르소나 발언 후 라우터로 복귀
    workflow.add_edge("resident", "supervisor")
    workflow.add_edge("merchant", "supervisor")
    workflow.add_edge("officer", "supervisor")
    
    # 평가 노드 실행 후 조건부 분기 (토론 종료 or 새 라운드)
    workflow.add_conditional_edges(
        "evaluator",
        check_evaluation,
        {
            "reporter": "reporter",
            "supervisor": "supervisor"
        }
    )
    
    workflow.add_edge("reporter", END)
    
    return workflow.compile()
