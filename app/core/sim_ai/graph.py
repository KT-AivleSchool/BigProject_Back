from typing import TypedDict, Annotated, Sequence
from langgraph.graph import StateGraph, END

# [동현님 담당] LangGraph에서 노드 간에 전송될 대화 상태 객체 정의
class AgentState(TypedDict):
    messages: Annotated[Sequence[str], "대화 이력 배열"]
    conflict_sensitivity: str  # 상 / 중 / 하
    current_sender: str        # 주민대표, 상인대표, 조정공무원 등
    is_finished: bool          # 토론 종결 여부
    final_scenarios: dict      # 도출된 최종 3대 시나리오 결과 객체

# 1. 노드 정의 (가상의 롤플레잉 노드 스텁)
def resident_node(state: AgentState) -> dict:
    """주민대표 (반대 페르소나) 노드"""
    # TODO: RAG 조례 정보와 CSS 민감도를 주입하여 OpenAI API로 반대 의견을 생성해 주세요.
    return {"messages": ["주민대표 노드가 가동되었습니다."]}

def merchant_node(state: AgentState) -> dict:
    """상인대표 (찬성 페르소나) 노드"""
    # TODO: 상권 활성화 및 입지 장점을 피력하는 LLM 찬성 의견을 생성해 주세요.
    return {"messages": ["상인대표 노드가 가동되었습니다."]}

def officer_node(state: AgentState) -> dict:
    """조정공무원 (중재 페르소나) 노드"""
    # TODO: 양측 의견을 취합하여 중재안을 도출하는 중재 로직을 작성해 주세요.
    return {"messages": ["조정공무원 노드가 가동되었습니다."]}

# 2. 그래프 빌더 빌드 함수
def build_discussion_graph():
    """
    [동현님 담당] 3자 토론용 LangGraph 워크플로우 빌드 및 컴파일
    """
    workflow = StateGraph(AgentState)
    
    # 노드 등록
    workflow.add_node("resident", resident_node)
    workflow.add_node("merchant", merchant_node)
    workflow.add_node("officer", officer_node)
    
    # 엣지 및 조건부 분기 설정 (TODO: 비즈니스 흐름 설계에 맞게 연결해 주세요)
    workflow.set_entry_point("resident")
    workflow.add_edge("resident", "merchant")
    workflow.add_edge("merchant", "officer")
    workflow.add_edge("officer", END)
    
    # 컴파일
    compiled_graph = workflow.compile()
    return compiled_graph
