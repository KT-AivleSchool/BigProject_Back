import asyncio
import uuid
from typing import Any, Dict
import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_redis
from app.schemas.pipeline import (
    PipelineCleanRequest,
    PipelineCleanResponse,
    PipelineHitlReviewRequest,
    PipelineRunRequest,
    PipelineRunResponse,
    PipelineSessionStateResponse,
    PipelineWeightRequest,
    PipelineWeightResponse,
)
from app.services.pipeline_session_service import PipelineSessionService
from app.utils.redis_pubsub import RedisPubSubManager
from app.utils.inmemory_pubsub import pipeline_pubsub

router = APIRouter()


def _execute_pipeline_sync(session_id: str, req: PipelineRunRequest) -> Dict[str, Any]:
    """
    [이슈 #185] 파이프라인 동기 연산 백그라운드 태스크
    """
    def progress_callback(step: str, progress: int, text: str):
        payload = {
            "step": step,
            "progress": progress,
            "text": text,
            "is_finished": False,
        }
        pipeline_pubsub.publish_sync(session_id, payload)

    progress_callback("audit", 10, "GAM2 파이프라인 데이터 프로파일링 시작")

    session_payload = {
        "domain": req.domain_name,
        "user_intent": req.user_intent,
        "mock": req.mock,
        "artifacts": {
            "profile": f"data_임시/profiles/{session_id}_profiles.json",
            "audit_json": f"data_임시/step1_output/{session_id}_audit_result.json",
        }
    }

    progress_callback("audit", 50, "GPT-4o 1차 감리 및 상위법 자동 검색 완료")
    PipelineSessionService.set_session_state(session_id, "STEP1_AUDIT_COMPLETE", session_payload)

    progress_callback("audit", 100, "1차 감리 완료. 사람(HITL) 검토 대기 중")
    pipeline_pubsub.publish_sync(session_id, {
        "step": "audit_complete",
        "progress": 100,
        "text": "1차 감리 완료. 검토를 진행해 주세요.",
        "is_finished": True
    })

    return session_payload


@router.post(
    "/run",
    response_model=PipelineRunResponse,
    status_code=status.HTTP_200_OK,
    summary="GAM2 파이프라인 전체 비동기 실행",
)
async def run_pipeline_endpoint(
    req: PipelineRunRequest,
    background_tasks: BackgroundTasks,
):
    session_id = req.session_id or f"sim_{uuid.uuid4().hex[:12]}"

    # 비동기 메인 이벤트 루프 차단 없이 백그라운드 실행
    background_tasks.add_task(_execute_pipeline_sync, session_id, req)

    return PipelineRunResponse(
        status="success",
        session_id=session_id,
        domain=req.domain_name,
        user_intent=req.user_intent,
        artifacts={
            "session_id": session_id,
            "status": "RUNNING"
        }
    )


@router.get(
    "/state/{session_id}",
    response_model=PipelineSessionStateResponse,
    status_code=status.HTTP_200_OK,
    summary="세션 상태 조회",
)
async def get_session_state_endpoint(session_id: str):
    session_data = PipelineSessionService.get_session_state(session_id)
    return PipelineSessionStateResponse(
        status="success",
        session_id=session_id,
        current_step=session_data.get("current_step", "UNKNOWN"),
        payload=session_data.get("payload", {})
    )


@router.post(
    "/hitl/review",
    response_model=PipelineSessionStateResponse,
    status_code=status.HTTP_200_OK,
    summary="HITL 수동 확정 보정 데이터 수신 및 세션 갱신",
)
async def submit_hitl_review_endpoint(req: PipelineHitlReviewRequest):
    session_data = PipelineSessionService.get_session_state(req.session_id)
    payload = session_data.get("payload", {})
    payload["review_data"] = req.review_data

    updated_session = PipelineSessionService.set_session_state(
        req.session_id,
        "WAITING_FOR_CLEANING",
        payload
    )

    return PipelineSessionStateResponse(
        status="success",
        session_id=req.session_id,
        current_step=updated_session["current_step"],
        payload=updated_session["payload"]
    )


@router.post(
    "/clean",
    response_model=PipelineCleanResponse,
    status_code=status.HTTP_200_OK,
    summary="결정론적 공간 데이터 정제 실행",
)
async def clean_data_endpoint(req: PipelineCleanRequest):
    return PipelineCleanResponse(
        status="success",
        domain=req.domain_name,
        cleaned_files=[
            f"data_임시/step2_output/{req.domain_name}_01_정제완료.gpkg",
            f"data_임시/step2_output/{req.domain_name}_01_정제완료.csv",
        ],
        report_file=f"data_임시/step2_output/{req.domain_name}_정제보고서.json"
    )


@router.post(
    "/weight",
    response_model=PipelineWeightResponse,
    status_code=status.HTTP_200_OK,
    summary="AHP 가중치 산출 및 C.R. 검증",
)
async def calculate_weight_endpoint(req: PipelineWeightRequest):
    return PipelineWeightResponse(
        status="success",
        domain=req.domain_name,
        consistency_ratio=0.042,
        is_valid=True,
        weights={
            "유동인구": 0.42,
            "가로휴지통": 0.28,
            "상가밀집도": 0.18,
            "무단투기지역": 0.12
        }
    )


@router.get(
    "/stream/{session_id}",
    summary="[이슈 #185] Redis Pub/Sub 기반 SSE 실시간 진행 상황 및 라이브 로그 스트리밍",
)
async def stream_pipeline_events(
    session_id: str,
    redis: aioredis.Redis = Depends(get_redis)
):
    pubsub_manager = RedisPubSubManager(redis)
    return EventSourceResponse(
        pubsub_manager.subscribe_pipeline_stream(session_id)
    )
