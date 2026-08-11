# [이해관계자 페르소나 모드] LangGraph 워크플로우 빌더 및 컴파일러
from langgraph.graph import StateGraph, START, END

from app.core.stakeholder_mode.graph.state import StakeholderGraphState
from app.core.stakeholder_mode.graph.nodes import (
    receive_input_node,
    recommend_stakeholders_node,
    generate_interest_profiles_node,
    validate_persona_diversity_node,
    review_stakeholders_node,
    create_personas_node,
    select_persona_contexts_node,
    render_prompts_node,
    run_personas_node,
    aggregate_results_node
)


def create_stakeholder_graph():
    """
    [LangGraph 그래프 구축 및 컴파일 함수]
    이해관계자 페르소나 모드 7단계 파이프라인 노드를 등록하고 순차적 엣지를 연결한 후 컴파일된 그래프 객체를 반환합니다.
    """
    # 1. StakeholderGraphState 상태 객체를 기반으로 StateGraph 파이프라인 생성
    workflow = StateGraph(StakeholderGraphState)

    # 2. 7개 실행 노드 등록
    workflow.add_node("receive_input", receive_input_node)               # 1. 입력 데이터 수신/파싱
    workflow.add_node("recommend_stakeholders", recommend_stakeholders_node) # 2. 이해관계자 3~5개 LLM 추천
    workflow.add_node("generate_interest_profiles", generate_interest_profiles_node) # 2-1. 이해관계 프로필 생성
    workflow.add_node("validate_persona_diversity", validate_persona_diversity_node) # 2-2. 페르소나 차별성 검사
    workflow.add_node("review_stakeholders", review_stakeholders_node)    # 3. 사용자 선택 및 수정 (HITL)
    workflow.add_node("create_personas", create_personas_node)           # 4. PersonaConfig 객체 생성
    workflow.add_node("select_persona_contexts", select_persona_contexts_node) # 4-1. 페르소나별 데이터 선택
    workflow.add_node("render_prompts", render_prompts_node)             # 5. Jinja2 프롬프트 렌더링
    workflow.add_node("run_personas", run_personas_node)                 # 6. 페르소나별 독립 평가 실행
    workflow.add_node("aggregate_results", aggregate_results_node)       # 7. 평가 결과 종합 집계

    # 3. 순차적 파이프라인 엣지(Edge) 연결
    workflow.add_edge(START, "receive_input")
    workflow.add_edge("receive_input", "recommend_stakeholders")
    workflow.add_edge("recommend_stakeholders", "generate_interest_profiles")
    workflow.add_edge("generate_interest_profiles", "validate_persona_diversity")
    workflow.add_edge("validate_persona_diversity", "review_stakeholders")
    workflow.add_edge("review_stakeholders", "create_personas")
    workflow.add_edge("create_personas", "select_persona_contexts")
    workflow.add_edge("select_persona_contexts", "render_prompts")
    workflow.add_edge("render_prompts", "run_personas")
    workflow.add_edge("run_personas", "aggregate_results")
    workflow.add_edge("aggregate_results", END)

    # 4. 실행 가능한 그래프로 컴파일하여 반환
    return workflow.compile()


# 외부에서 바로 불러와 실행할 수 있는 전역 그래프 인스턴스 export
stakeholder_graph = create_stakeholder_graph()
