import asyncio
import logging
from typing import Dict, List, AsyncGenerator, Any

logger = logging.getLogger(__name__)

# 세션 ID별 asyncio.Queue 관리를 위한 인메모리 Pub/Sub 채널 저장소
_CHANNELS: Dict[str, List[asyncio.Queue]] = {}


class InMemoryPubSubManager:
    """
    [이슈 #164 오버엔지니어링 방지] Redis 인프라 없이 단일 FastAPI 백엔드 서버 메모리 상에서
    asyncio.Queue 기반으로 SSE 라이브 파이프라인 진행 로그를 실시간 릴레이 중계하는 매니저
    """

    @staticmethod
    async def publish_pipeline_message(session_id: str, data: Dict[str, Any]) -> None:
        """파이프라인 진행 상태 메세지를 세션 구독자들에게 멀티캐스트 전송"""
        if session_id in _CHANNELS:
            queues = _CHANNELS[session_id]
            for q in queues:
                await q.put(data)
            logger.info(
                f"📡 [In-Memory PubSub] {session_id} 파이프라인 메세지 전송 ({len(queues)}개 구독자)"
            )

    @staticmethod
    async def subscribe_pipeline_stream(
        session_id: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """SSE 클라이언트 연결 시 전용 asyncio.Queue 수신기 생성 및 스트리밍 구독"""
        queue: asyncio.Queue = asyncio.Queue()
        if session_id not in _CHANNELS:
            _CHANNELS[session_id] = []
        _CHANNELS[session_id].append(queue)
        logger.info(f"🔌 [In-Memory PubSub] {session_id} SSE 스트리밍 새로운 구독 시작")

        try:
            while True:
                # 메세지 유입 대기
                msg = await queue.get()
                yield msg
                queue.task_done()
                if msg.get("status") in ("complete", "failed", "success"):
                    break
        except asyncio.CancelledError:
            logger.info(f"🔌 [In-Memory PubSub] {session_id} SSE 스트리밍 연결 종료됨")
        finally:
            if session_id in _CHANNELS and queue in _CHANNELS[session_id]:
                _CHANNELS[session_id].remove(queue)
                if not _CHANNELS[session_id]:
                    del _CHANNELS[session_id]
