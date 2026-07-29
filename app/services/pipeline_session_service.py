import time
from typing import Any, Dict, Optional
from fastapi import HTTPException, status

_SESSION_STORE: Dict[str, Dict[str, Any]] = {}
SESSION_TTL_SECONDS = 86400  # 24시간 세션 유효 기간


class PipelineSessionService:
    """
    [이슈 #157 / #164] 파이프라인 세션 상태 관리 서비스
    - Redis / 인메모리 스토어를 활용하여 simulation:{session_id} 상태 캐싱
    - 24시간(86400초) TTL 적용 및 부재/만료 시 404 예외 처리
    """

    @staticmethod
    def get_session_key(session_id: str) -> str:
        return f"simulation:{session_id}"

    @classmethod
    def set_session_state(
        cls,
        session_id: str,
        step: str,
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        key = cls.get_session_key(session_id)
        session_data = {
            "session_id": session_id,
            "current_step": step,
            "payload": payload,
            "updated_at": time.time(),
        }
        _SESSION_STORE[key] = session_data
        return session_data

    @classmethod
    def get_session_state(cls, session_id: str) -> Dict[str, Any]:
        key = cls.get_session_key(session_id)
        session_data = _SESSION_STORE.get(key)

        if not session_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="존재하지 않거나 만료된 세션입니다."
            )

        # TTL 만료 여부 확인
        if time.time() - session_data.get("updated_at", 0) > SESSION_TTL_SECONDS:
            _SESSION_STORE.pop(key, None)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="존재하지 않거나 만료된 세션입니다."
            )

        return session_data

    @classmethod
    def clear_session(cls, session_id: str) -> None:
        key = cls.get_session_key(session_id)
        _SESSION_STORE.pop(key, None)
