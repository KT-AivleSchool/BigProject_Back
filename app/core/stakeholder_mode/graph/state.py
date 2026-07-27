# [이해관계자 페르소나 모드] Phase 2 확장 LangGraph State 정의서
from typing import TypedDict, List, Dict, Any, Optional


class StakeholderGraphState(TypedDict, total=False):
    """
    [Phase 2 확장 StakeholderGraphState 상태 객체]
    LangGraph 파이프라인 각 노드 간 전달되는 상태 타입입니다.
    근거/조례 검증 리포트(validation_issues) 항목이 추가되었습니다.
    """
    project_id: str                              # 프로젝트 고유 식별 코드
    topic: str                                   # 심의 및 토론 주제
    candidate_sites: List[Dict[str, Any]]        # 후보지 정량/정성 데이터 객체 리스트
    ordinance_contexts: List[Dict[str, Any]]     # 조례 RAG Context 객체 리스트
    recommended_stakeholders: List[Dict[str, Any]] # LLM이 추천한 이해관계자 3~5개 리스트
    selected_stakeholders: List[Dict[str, Any]]  # 사용자가 선택/수정한 최종 이해관계자 리스트 (HITL)
    persona_configs: List[Dict[str, Any]]        # 페르소나 설정 객체 (PersonaConfig) 리스트
    rendered_prompts: Dict[str, Dict[str, str]]  # 페르소나 ID별 Jinja2 렌더링 결과 프롬프트
    opinions: List[Dict[str, Any]]               # 각 페르소나별 독립 평가 결과 (PersonaOpinion) 리스트
    validation_issues: List[Dict[str, Any]]      # 근거 및 조례 검증 리포트 (ValidationIssue) 리스트 (Phase 2 추가)
    final_result: Optional[Dict[str, Any]]       # 최종 모드 종합 집계 결과 (StakeholderModeResult)
