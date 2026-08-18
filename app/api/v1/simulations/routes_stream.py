import json
import asyncio
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from app.schemas.simulations import StreamRequest
from app.api.deps import get_redis
from app.utils.redis_pubsub import RedisPubSubManager
from app.core.security_limiter import rate_limiter
from app.api.v1.simulations.services import run_debate_and_publish

router = APIRouter()


@router.post("/stream", dependencies=[Depends(rate_limiter)])
async def stream_ai_discussion(
    request: StreamRequest, redis: aioredis.Redis = Depends(get_redis)
):
    parcel_id = request.parcel_id
    facility_type = request.facility_type

    asyncio.create_task(
        run_debate_and_publish(
            parcel_id=parcel_id,
            facility_type=facility_type,
            redis=redis,
        )
    )

    pubsub_manager = RedisPubSubManager(redis)

    async def event_generator():
        async for data in pubsub_manager.subscribe_debate_stream(parcel_id):
            yield {"event": "message", "data": json.dumps(data, ensure_ascii=False)}

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Credentials": "true",
        "Cache-Control": "no-cache, no-transform",
    }
    return EventSourceResponse(event_generator(), headers=headers)
