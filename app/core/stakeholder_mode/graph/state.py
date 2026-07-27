from typing import TypedDict, List, Dict, Any, Optional


class StakeholderGraphState(TypedDict, total=False):
    project_id: str
    topic: str
    candidate_sites: List[Dict[str, Any]]
    ordinance_contexts: List[Dict[str, Any]]
    recommended_stakeholders: List[Dict[str, Any]]
    selected_stakeholders: List[Dict[str, Any]]
    persona_configs: List[Dict[str, Any]]
    rendered_prompts: Dict[str, Dict[str, str]]
    opinions: List[Dict[str, Any]]
    final_result: Optional[Dict[str, Any]]
