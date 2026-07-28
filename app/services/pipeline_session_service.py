import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# [이슈 #164 기술 스택 확정] Redis 의존성을 완전히 쳐내고 가벼운 인메모리 딕셔너리로 세션 상태 보관
_SESSION_STORE: Dict[str, Dict[str, Any]] = {}


def get_session_key(session_id: str) -> str:
    """세션 키 식별자 (예: simulation:sim_12345)"""
    return f"simulation:{session_id}"


async def save_session_state(
    session_id: str,
    step_name: str,
    payload: Dict[str, Any],
) -> bool:
    """
    [장천명 풀스택] [이슈 #164] Redis 없이 파이프라인 진행 상태를 백엔드 서버 인메모리 캐시에 즉시 저장
    """
    key = get_session_key(session_id)
    doc = {
        "session_id": session_id,
        "current_step": step_name,
        "payload": payload,
    }
    try:
        _SESSION_STORE[key] = doc
        logger.info(f"✅ [In-Memory Session] 세션 저장 완료 ({key}, step={step_name})")
        return True
    except Exception as e:
        logger.error(f"⚠️ [In-Memory Session] 세션 저장 실패 ({key}): {str(e)}")
        return False


async def get_session_state(
    session_id: str,
) -> Optional[Dict[str, Any]]:
    """
    [장천명 풀스택] [이슈 #164] 인메모리 캐시에서 파이프라인 세션 상태 초고속 조회 (없거나 만료 시 None)
    """
    key = get_session_key(session_id)
    state = _SESSION_STORE.get(key)
    if not state:
        logger.warning(f"🔍 [In-Memory Session] 세션 없음 또는 만료됨 ({key})")
        return None
    return state


async def update_session_hitl(
    session_id: str,
    review_data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    [장천명 풀스택] [이슈 #164] 프론트엔드가 전송한 HITL 인간 확정 결과로 인메모리 세션 상태 갱신
    """
    existing = await get_session_state(session_id)
    if not existing:
        return None

    payload = existing.get("payload", {})
    payload["reviewed_hitl"] = review_data
    existing["current_step"] = "WAITING_FOR_CLEANING"
    existing["payload"] = payload

    success = await save_session_state(
        session_id=session_id,
        step_name="WAITING_FOR_CLEANING",
        payload=payload,
    )
    return existing if success else None
