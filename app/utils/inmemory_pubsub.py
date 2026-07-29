import asyncio
import json
from typing import AsyncGenerator, Dict, List


class InMemoryPubSubManager:
    """
    [이슈 #164] In-Memory SSE 실시간 스트리밍 중계 매니저
    - asyncio.Queue를 사용하여 파이프라인 진행 상태 및 로그를 SSE 클라이언트에게 멀티캐스트 전송
    """

    def __init__(self):
        self._channels: Dict[str, List[asyncio.Queue]] = {}

    def _get_queues(self, channel_id: str) -> List[asyncio.Queue]:
        if channel_id not in self._channels:
            self._channels[channel_id] = []
        return self._channels[channel_id]

    def publish_sync(self, channel_id: str, message: dict) -> None:
        queues = self._channels.get(channel_id, [])
        for q in queues:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass

        # 백그라운드 이벤트 루프가 있을 때 put 처리
        try:
            loop = asyncio.get_running_loop()
            for q in queues:
                loop.call_soon_threadsafe(q.put_nowait, message)
        except RuntimeError:
            pass

    async def subscribe_pipeline_stream(self, channel_id: str) -> AsyncGenerator[dict, None]:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        queues = self._get_queues(channel_id)
        queues.append(q)

        try:
            # 초기 연결 메시지
            yield {
                "event": "message",
                "data": json.dumps({
                    "step": "connected",
                    "progress": 0,
                    "text": f"SSE 스트림 연결 완료 (session_id: {channel_id})",
                    "is_finished": False
                }, ensure_ascii=False)
            }

            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield {
                        "event": "message",
                        "data": json.dumps(data, ensure_ascii=False)
                    }
                    if data.get("is_finished", False):
                        break
                except asyncio.TimeoutError:
                    # 핑(Ping) 핑퐁유지
                    yield {
                        "event": "ping",
                        "data": json.dumps({"type": "ping", "time": asyncio.get_event_loop().time()})
                    }
        finally:
            if channel_id in self._channels and q in self._channels[channel_id]:
                self._channels[channel_id].remove(q)
                if not self._channels[channel_id]:
                    del self._channels[channel_id]


pipeline_pubsub = InMemoryPubSubManager()
