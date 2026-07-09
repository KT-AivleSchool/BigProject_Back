import operator
from typing import TypedDict, Annotated, Sequence
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import json
import re

from app.core.sim_ai.prompts import (
    build_prompt,
    PRO_ROLE_PROMPT,
    CON_ROLE_PROMPT,
    GOV_ROLE_PROMPT,
    EVALUATOR_PROMPT,
    REPORTER_PROMPT,
)
from app.core.sim_ai.vector_db import RagVectorStorage
from app.config import settings


# [동현님 담당] LangGraph에서 노드 간에 전송될 대화 상태 객체 정의
class AgentState(TypedDict):
    # operator.add를 사용하여 배열에 자동으로 추가되도록 설정 (LangGraph 표준)
    messages: Annotated[Sequence[str], operator.add]
    # site_information 필드는 개별 메타데이터로 대체됨
    css_pro: str
    css_con: str
    round_count: int  # 토론 반복 횟수 (최대 3라운드)
    current_phase: str  # "토론", "중재"
    eval_score: float
    spoken_this_round: list[str]  # 이번 라운드에 발언한 페르소나 추적

    # JSON 결과값 도출용 GIS 메타데이터
    candidate_jibun: str
    candidate_lat: float
    candidate_lng: float
    facility_type: str
    intensity_level: str
    ahp_weights: dict
    timestamp: str

    rag_pro: str  # 찬성
    rag_con: str  # 반대
    rag_gov: str  # 정부
    evaluations: dict  # 내부 평가 결과 (수용도)
    final_scenarios: dict  # 도출된 최종 시나리오 결과 객체
    is_finished: bool  # 토론 종결 여부
    next_speaker: str  # 라우터가 결정한 다음 발화자


# LLM 및 Vector DB 전역 인스턴스 (온도는 창의적 역할극을 위해 0.7 유지)
llm = ChatOpenAI(api_key=settings.OPENAI_API_KEY, model="gpt-4o-mini", temperature=0.7)
vector_db = RagVectorStorage()


def _format_chat_history(messages: Sequence[str]) -> str:
    """메시지 리스트를 하나의 텍스트로 포맷팅"""
    return "\n".join(messages)


def _extract_json(text: str) -> dict:
    """LLM의 응답에서 마크다운 태그를 제거하고 안전하게 JSON 파싱"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


# 1. 라우터 (사회자) 노드
async def supervisor_node(state: AgentState) -> dict:
    """다음 발화자를 결정하는 결정적(deterministic) 라우터"""
    phase = state.get("current_phase", "debate")
    spoken = state.get("spoken_this_round", [])

    if phase == "debate":
        if not spoken:
            return {"next_speaker": "pro"}
        elif spoken == ["pro"]:
            return {"next_speaker": "con"}
        else:
            return {"next_speaker": "evaluator"}
    elif phase == "intervention":
        if not spoken:
            return {"next_speaker": "gov"}
        elif spoken == ["gov"]:
            return {"next_speaker": "pro"}
        elif spoken == ["gov", "pro"]:
            return {"next_speaker": "con"}
        elif spoken == ["gov", "pro", "con"]:
            return {"next_speaker": "gov_wrapup"}
        else:
            return {"next_speaker": "reporter"}

    return {"next_speaker": "pro"}


# 2. 페르소나 노드들
async def pro_node(state: AgentState) -> dict:
    """찬성 페르소나 노드"""
    css_level = state.get("css_pro", "HIGH").upper()
    facility_type = state.get("facility_type", "알 수 없음")
    history_text = _format_chat_history(state.get("messages", []))

    rag_context = state.get("rag_pro", "")
    if not rag_context:
        # [캐시 라이프사이클 안내] 
        # 이 RAG 캐싱은 단일 API 요청(1회 시뮬레이션) 동안만 유지되는 In-Memory 상태입니다.
        # 새로운 시뮬레이션 요청 시마다 상태가 초기화되므로 Stale Cache(오염된 캐시)가 발생하지 않습니다.
        query = f"{facility_type} 시설 설치 찬성 긍정적 효과 경제적 이익 편익"
        retrieved_docs = await vector_db.retrieve_similar_statutes(query, facility_type=facility_type)
        rag_context = "\n".join(retrieved_docs)

    prompt = build_prompt(
        role_prompt=PRO_ROLE_PROMPT,
        candidate_jibun=state.get("candidate_jibun", "지번 정보 없음"),
        candidate_lat=state.get("candidate_lat", 0.0),
        candidate_lng=state.get("candidate_lng", 0.0),
        facility_type=facility_type,
        intensity_level=state.get("intensity_level", "normal"),
        ahp_weights=state.get("ahp_weights", {}),
        rag_context=rag_context,
        discussion_history=history_text,
        css_level=css_level,
    )

    response = await llm.ainvoke([SystemMessage(content=prompt)])
    spoken = state.get("spoken_this_round", [])
    return {
        "messages": [f"찬성: {response.content}"],
        "spoken_this_round": spoken + ["pro"],
        "rag_pro": rag_context,
    }


async def con_node(state: AgentState) -> dict:
    """반대 페르소나 노드"""
    css_level = state.get("css_con", "HIGH").upper()
    facility_type = state.get("facility_type", "알 수 없음")
    history_text = _format_chat_history(state.get("messages", []))

    rag_context = state.get("rag_con", "")
    if not rag_context:
        # [캐시 라이프사이클 안내] 단일 세션 전용 캐시로 PDF 신규 업로드 시에도 문제 없이 최신 DB를 반영합니다.
        query = f"{facility_type} 시설 설치 반대 피해 환경 규제 주민 우려"
        retrieved_docs = await vector_db.retrieve_similar_statutes(query, facility_type=facility_type)
        rag_context = "\n".join(retrieved_docs)

    prompt = build_prompt(
        role_prompt=CON_ROLE_PROMPT,
        candidate_jibun=state.get("candidate_jibun", "지번 정보 없음"),
        candidate_lat=state.get("candidate_lat", 0.0),
        candidate_lng=state.get("candidate_lng", 0.0),
        facility_type=facility_type,
        intensity_level=state.get("intensity_level", "normal"),
        ahp_weights=state.get("ahp_weights", {}),
        rag_context=rag_context,
        discussion_history=history_text,
        css_level=css_level,
    )

    response = await llm.ainvoke([SystemMessage(content=prompt)])
    spoken = state.get("spoken_this_round", [])
    return {
        "messages": [f"반대: {response.content}"],
        "spoken_this_round": spoken + ["con"],
        "rag_con": rag_context,
    }


async def gov_node(state: AgentState) -> dict:
    """정부 페르소나 노드 (중재안 제시 및 마무리)"""
    facility_type = state.get("facility_type", "알 수 없음")
    history_text = _format_chat_history(state.get("messages", []))
    spoken = state.get("spoken_this_round", [])

    rag_context = state.get("rag_gov", "")
    if not rag_context:
        # [캐시 라이프사이클 안내] 단일 세션 전용 캐시로 PDF 신규 업로드 시에도 문제 없이 최신 DB를 반영합니다.
        query = f"{facility_type} 설치 기준 갈등 조정 공공 시설 중재안 법률"
        retrieved_docs = await vector_db.retrieve_similar_statutes(query, facility_type=facility_type)
        rag_context = "\n".join(retrieved_docs)

    prompt = build_prompt(
        role_prompt=GOV_ROLE_PROMPT,
        candidate_jibun=state.get("candidate_jibun", "지번 정보 없음"),
        candidate_lat=state.get("candidate_lat", 0.0),
        candidate_lng=state.get("candidate_lng", 0.0),
        facility_type=facility_type,
        intensity_level=state.get("intensity_level", "normal"),
        ahp_weights=state.get("ahp_weights", {}),
        rag_context=rag_context,
        discussion_history=history_text,
        css_level="LOW",  # 정부는 객관적 중재를 위해 LOW 유지
    )

    if spoken == ["gov", "pro", "con"]:
        system_msg = (
            prompt
            + "\n\n현재 상황: 정부의 중재안에 대한 양측의 입장을 들었습니다. 토론을 최종 마무리하는 발언을 짧게 하십시오."
        )
    else:
        system_msg = (
            prompt
            + "\n\n현재 상황: 3라운드의 찬반 토론이 종료되거나 합의점이 도달하여 정부가 개입할 차례입니다. 양측 의견을 수렴하여 공정한 중재안을 제시하십시오."
        )

    response = await llm.ainvoke([SystemMessage(content=system_msg)])
    return {
        "messages": [f"정부: {response.content}"],
        "spoken_this_round": spoken + ["gov"],
        "rag_gov": rag_context,
    }


# 3. 평가 및 최종 노드
async def evaluator_node(state: AgentState) -> dict:
    """내부 평가 노드"""
    history_text = _format_chat_history(state.get("messages", []))
    round_count = state.get("round_count", 0) + 1

    llm_json = llm.bind(response_format={"type": "json_object"})
    response = await llm_json.ainvoke(
        [
            SystemMessage(content=EVALUATOR_PROMPT),
            HumanMessage(
                content=f"이전 대화:\n{history_text}\n\n위 대화 내용을 바탕으로 평가 JSON을 반환하세요."
            ),
        ]
    )

    try:
        evals = _extract_json(response.content)
    except Exception as e:
        print(f"JSON Parsing Error: {e}")
        evals = {}

    pro_acc = evals.get("pro_acceptance", 0.0)
    con_acc = evals.get("con_acceptance", 0.0)
    avg_acc = (pro_acc + con_acc) / 2.0

    # ACC 점수와 무관하게 평가자 AI가 내린 순수 감정 민감도(CSS)를 적용 (실패 시 기존 값 유지)
    new_css_pro = evals.get("pro_css", state.get("css_pro", "HIGH"))
    new_css_con = evals.get("con_css", state.get("css_con", "HIGH"))

    next_phase = "debate"
    if round_count >= 3 or avg_acc >= 0.8:
        next_phase = "intervention"

    return {
        "evaluations": evals,
        "eval_score": avg_acc,
        "round_count": round_count,
        "css_pro": new_css_pro,
        "css_con": new_css_con,
        "spoken_this_round": [],  # 다음 라운드/페이즈를 위해 초기화
        "current_phase": next_phase,
    }


async def reporter_node(state: AgentState) -> dict:
    """토론 종료 후 최종 시나리오 도출 노드"""
    history_text = _format_chat_history(state.get("messages", []))

    llm_json = llm.bind(response_format={"type": "json_object"})
    response = await llm_json.ainvoke(
        [
            SystemMessage(content=REPORTER_PROMPT),
            HumanMessage(
                content=f"전체 토론 내용:\n{history_text}\n\n위 대화 내용을 바탕으로 최종 시나리오 JSON을 도출하세요."
            ),
        ]
    )

    try:
        final_scenarios = _extract_json(response.content)
    except Exception as e:
        print(f"JSON Parsing Error: {e}")
        final_scenarios = {}

    return {"final_scenarios": final_scenarios, "is_finished": True}


def route_next(state: AgentState) -> str:
    """라우터 노드가 결정한 다음 발화자로 이동"""
    return state.get("next_speaker", "pro")


def check_evaluation(state: AgentState) -> str:
    """평가 점수에 따라 토론 종료 여부 결정 (현재 사용 안함. supervisor가 처리)"""
    return "supervisor"


# 5. 그래프 빌드 함수
def build_discussion_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("pro", pro_node)
    workflow.add_node("con", con_node)
    workflow.add_node("gov", gov_node)
    workflow.add_node("gov_wrapup", gov_node)
    workflow.add_node("evaluator", evaluator_node)
    workflow.add_node("reporter", reporter_node)

    # 시작점은 무조건 라우터(사회자)
    workflow.set_entry_point("supervisor")

    workflow.add_conditional_edges(
        "supervisor",
        route_next,
        {
            "pro": "pro",
            "con": "con",
            "gov": "gov",
            "gov_wrapup": "gov_wrapup",
            "evaluator": "evaluator",
            "reporter": "reporter",
        },
    )

    # 각 페르소나 및 평가 후 라우터로 복귀
    workflow.add_edge("pro", "supervisor")
    workflow.add_edge("con", "supervisor")
    workflow.add_edge("gov", "supervisor")
    workflow.add_edge("gov_wrapup", "supervisor")
    workflow.add_edge("evaluator", "supervisor")

    workflow.add_edge("reporter", END)

    return workflow.compile()
