import json
import logging
from typing import Optional, Dict, Any
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

SESSION_KEY_PREFIX = "simulation"
DEFAULT_TTL_SECONDS = 86400  # 24시간


def get_session_key(session_id: str) -> str:
    """Redis 세션 키 생성 (예: simulation:sim_12345:step_state)"""
    return f"{SESSION_KEY_PREFIX}:{session_id}:step_state"


async def save_session_state(
    redis: aioredis.Redis,
    session_id: str,
    step_name: str,
    payload: Dict[str, Any],
    ttl: int = DEFAULT_TTL_SECONDS,
) -> bool:
    """
    [장천명 풀스택] 파이프라인 중간/최종 진행 상태를 Redis 인메모리 캐시에 24시간 저장
    """
    key = get_session_key(session_id)
    doc = {
        "session_id": session_id,
        "current_step": step_name,
        "payload": payload,
    }
    try:
        json_str = json.dumps(doc, ensure_ascii=False)
        await redis.set(key, json_str, ex=ttl)
        logger.info(
            f"✅ [Pipeline Session] Redis 세션 저장 완료 ({key}, step={step_name}, ttl={ttl}s)"
        )
        return True
    except Exception as e:
        logger.error(f"⚠️ [Pipeline Session] Redis 세션 저장 실패 ({key}): {str(e)}")
        return False


async def get_session_state(
    redis: aioredis.Redis,
    session_id: str,
) -> Optional[Dict[str, Any]]:
    """
    [장천명 풀스택] Redis 캐시에서 파이프라인 세션 상태를 초고속 수신 (없거나 만료 시 None)
    """
    key = get_session_key(session_id)
    try:
        raw_val = await redis.get(key)
        if not raw_val:
            logger.warning(f"🔍 [Pipeline Session] Redis 세션 없음 또는 만료됨 ({key})")
            return None
        return json.loads(raw_val)
    except Exception as e:
        logger.error(f"⚠️ [Pipeline Session] Redis 세션 조회 실패 ({key}): {str(e)}")
        return None


async def update_session_hitl(
    redis: aioredis.Redis,
    session_id: str,
    review_data: Dict[str, Any],
    ttl: int = DEFAULT_TTL_SECONDS,
) -> Optional[Dict[str, Any]]:
    """
    [장천명 풀스택] 프론트엔드가 전송한 HITL 인간 확정 결과로 Redis 세션 상태 갱신
    """
    existing = await get_session_state(redis, session_id)
    if not existing:
        return None

    payload = existing.get("payload", {})
    payload["reviewed_hitl"] = review_data
    existing["current_step"] = "WAITING_FOR_CLEANING"
    existing["payload"] = payload

    success = await save_session_state(
        redis,
        session_id=session_id,
        step_name="WAITING_FOR_CLEANING",
        payload=payload,
        ttl=ttl,
    )
    return existing if success else None
