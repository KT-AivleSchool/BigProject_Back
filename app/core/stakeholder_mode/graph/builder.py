from langgraph.graph import StateGraph, START, END

from app.core.stakeholder_mode.graph.state import StakeholderGraphState
from app.core.stakeholder_mode.graph.nodes import (
    receive_input_node,
    recommend_stakeholders_node,
    review_stakeholders_node,
    create_personas_node,
    render_prompts_node,
    run_personas_node,
    aggregate_results_node
)


def create_stakeholder_graph():
    """이해관계자 페르소나 모드 LangGraph를 생성하고 컴파일합니다."""
    workflow = StateGraph(StakeholderGraphState)

    # 1. 노드 등록
    workflow.add_node("receive_input", receive_input_node)
    workflow.add_node("recommend_stakeholders", recommend_stakeholders_node)
    workflow.add_node("review_stakeholders", review_stakeholders_node)
    workflow.add_node("create_personas", create_personas_node)
    workflow.add_node("render_prompts", render_prompts_node)
    workflow.add_node("run_personas", run_personas_node)
    workflow.add_node("aggregate_results", aggregate_results_node)

    # 2. 엣지 연결 (순차 파이프라인)
    workflow.add_edge(START, "receive_input")
    workflow.add_edge("receive_input", "recommend_stakeholders")
    workflow.add_edge("recommend_stakeholders", "review_stakeholders")
    workflow.add_edge("review_stakeholders", "create_personas")
    workflow.add_edge("create_personas", "render_prompts")
    workflow.add_edge("render_prompts", "run_personas")
    workflow.add_edge("run_personas", "aggregate_results")
    workflow.add_edge("aggregate_results", END)

    # 3. 그래프 컴파일
    return workflow.compile()


stakeholder_graph = create_stakeholder_graph()
