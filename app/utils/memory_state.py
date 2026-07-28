"""
파이프라인 단계 간 프론트엔드로 거대한 JSON 페이로드를 넘겨주기 위한 
임시 인메모리 저장소입니다.
(Redis 대체 및 로컬 파일 시스템 의존성 제거 목적)
"""

# session_id -> dict (파이프라인 상태 JSON)
_pipeline_states = {}

def set_state(session_id: str, state: dict):
    """특정 세션의 파이프라인 진행 상태 저장"""
    _pipeline_states[session_id] = state

def get_state(session_id: str) -> dict:
    """특정 세션의 파이프라인 진행 상태 조회 (없으면 빈 dict 반환)"""
    return _pipeline_states.get(session_id, {})

def clear_state(session_id: str):
    """메모리 확보를 위해 특정 세션 상태 삭제"""
    if session_id in _pipeline_states:
        del _pipeline_states[session_id]
