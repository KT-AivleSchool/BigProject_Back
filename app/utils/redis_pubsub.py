import json
import redis.asyncio as aioredis
from typing import AsyncGenerator


class RedisPubSubManager:
    """
    [SSE 중계 엔진] Redis Pub/Sub을 활용한 실시간 AI 토론 및 파이프라인 진행률 발행/구독 유틸리티
    """

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client

    async def publish_debate_message(
        self, session_id: str, sender: str, text: str, is_finished: bool = False
    ):
        """
        AI 토론 대사 한 묶음을 특정 세션 ID 채널로 발행(Publish)합니다.
        """
        channel = f"debate:{session_id}"
        payload = {"sender": sender, "text": text, "is_finished": is_finished}
        await self.redis.publish(channel, json.dumps(payload, ensure_ascii=False))

    async def subscribe_debate_stream(
        self, session_id: str
    ) -> AsyncGenerator[dict, None]:
        """
        특정 세션 ID 채널을 구독(Subscribe)하여 실시간 발행되는 대사 메시지를 yield합니다.
        """
        channel = f"debate:{session_id}"
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    yield data
                    if data.get("is_finished", False):
                        break
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    async def publish_pipeline_message(
        self, session_id: str, payload: dict
    ):
        """
        [이슈 #185] GAM2 파이프라인 진행률 및 라이브 로그 이벤트를 특정 세션 ID 채널로 발행(Publish)합니다.
        """
        channel = f"pipeline:{session_id}"
        await self.redis.publish(channel, json.dumps(payload, ensure_ascii=False))

    async def subscribe_pipeline_stream(
        self, session_id: str
    ) -> AsyncGenerator[dict, None]:
        """
        [이슈 #185] GAM2 파이프라인 진행 채널을 구독(Subscribe)하여 실시간 발행 이벤트를 SSE로 yield합니다.
        """
        channel = f"pipeline:{session_id}"
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)

        try:
            # 초기 연결 확정 메시지
            yield {
                "step": "connected",
                "progress": 0,
                "text": f"Redis SSE 스트림 연결 완료 (session_id: {session_id})",
                "is_finished": False
            }

            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    yield data
                    if data.get("is_finished", False):
                        break
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
