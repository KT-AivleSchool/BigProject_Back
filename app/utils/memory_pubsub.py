import asyncio
import json
from typing import AsyncGenerator


class MemoryPubSubManager:
    """
    [SSE 중계 엔진] 인메모리(asyncio.Queue)를 활용한 실시간 대사 발행 및 구독 유틸리티
    FastAPI의 단일 쓰레드풀(BackgroundTasks)과 메인 이벤트 루프 간의 통신을 담당합니다.
    """

    def __init__(self):
        # session_id -> asyncio.Queue
        self.queues = {}
        # FastAPI lifespan에서 획득할 메인 이벤트 루프
        self.loop: asyncio.AbstractEventLoop | None = None

    def get_queue(self, session_id: str) -> asyncio.Queue:
        """세션 ID에 대한 비동기 큐를 반환(없으면 생성)"""
        if session_id not in self.queues:
            self.queues[session_id] = asyncio.Queue()
        return self.queues[session_id]

    def publish_sync(self, session_id: str, payload: dict):
        """
        BackgroundTasks 등 동기(Sync) 쓰레드 환경에서 안전하게 이벤트를 발생시킵니다.
        """
        if self.loop is None:
            print(f"[{session_id}] MemoryPubSubManager loop is not initialized.")
            return
        
        queue = self.get_queue(session_id)
        # 메인 이벤트 루프에 안전하게 put_nowait 스케줄링
        self.loop.call_soon_threadsafe(queue.put_nowait, payload)

    async def subscribe_pipeline_stream(self, session_id: str) -> AsyncGenerator[dict, None]:
        """
        특정 세션 ID 채널(GAM2 파이프라인)을 구독(Subscribe)하여 실시간 발행되는 메시지를 yield합니다.
        """
        queue = self.get_queue(session_id)
        try:
            while True:
                data = await queue.get()
                yield data
                if data.get("is_finished", False):
                    break
        finally:
            # 구독 종류 시 메모리 큐 정리
            if session_id in self.queues:
                del self.queues[session_id]


# 전역 싱글톤 인스턴스
pipeline_pubsub = MemoryPubSubManager()
