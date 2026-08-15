from langgraph.graph import StateGraph, START, END

from app.core.stakeholder_mode.graph.dynamic_state import DynamicDiscussionState
from app.core.stakeholder_mode.graph.dynamic_nodes import (
    dynamic_supervisor_node,
    dynamic_persona_speaker_node,
    dynamic_factchecker_node,
    dynamic_evaluator_node,
    dynamic_reporter_node
)

def route_from_supervisor(state: DynamicDiscussionState) -> str:
    """supervisor에서 반환한 next_speaker 값에 따라 라우팅"""
    ns = state.get("next_speaker", "evaluator")
    if ns == "evaluator":
        return "evaluator"
    return "persona_speaker"

def route_from_evaluator(state: DynamicDiscussionState) -> str:
    """evaluator 평가 후 종료 조건 판단"""
    evals = state.get("evaluations", {})
    round_cnt = state.get("round_count", 0)
    active_participants = state.get("active_participants", [])
    
    # 평균 수용도 계산
    total_acc = 0.0
    for pid in active_participants:
        total_acc += float(evals.get(f"{pid}_acceptance", evals.get(pid, 0.0)))
        
    avg_acc = total_acc / len(active_participants) if active_participants else 0.0
    
    # 1라운드(모두발언) 및 2라운드(본격토론) 완료 시까지는 무조건 진행 (최소 2라운드 토론 보장)
    if round_cnt < 2:
        return "supervisor"
        
    # 평균 수용도 0.7 이상이거나 3라운드 진행 시 종료 (reporter로 이동)
    if avg_acc >= 0.7 or round_cnt >= 3:
        return "reporter"
    return "supervisor"

def build_dynamic_discussion_graph():
    """동적 페르소나들을 이용한 다자간 심의 그래프를 빌드합니다."""
    workflow = StateGraph(DynamicDiscussionState)
    
    # 1. 노드 등록
    workflow.add_node("supervisor", dynamic_supervisor_node)
    workflow.add_node("persona_speaker", dynamic_persona_speaker_node)
    workflow.add_node("factchecker", dynamic_factchecker_node)
    workflow.add_node("evaluator", dynamic_evaluator_node)
    workflow.add_node("reporter", dynamic_reporter_node)
    
    # 2. 시작 노드
    workflow.set_entry_point("supervisor")
    
    # 3. 라우팅 로직 추가
    workflow.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "persona_speaker": "persona_speaker",
            "evaluator": "evaluator"
        }
    )
    
    # 스피커가 발언을 마치면 팩트체커에게 넘김
    workflow.add_edge("persona_speaker", "factchecker")
    # 팩트체커가 검증을 마치면 무조건 다시 supervisor에게 제어권을 넘김
    workflow.add_edge("factchecker", "supervisor")
    
    # 평가가 끝나면 조건에 따라 reporter로 가거나 다시 supervisor로 돌아감
    workflow.add_conditional_edges(
        "evaluator",
        route_from_evaluator,
        {
            "reporter": "reporter",
            "supervisor": "supervisor"
        }
    )
    
    # 리포팅이 끝나면 종료
    workflow.add_edge("reporter", END)
    
    return workflow.compile()

# 외부 접근용 글로벌 인스턴스
dynamic_discussion_graph = build_dynamic_discussion_graph()
