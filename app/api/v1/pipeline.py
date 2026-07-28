import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_redis
from app.schemas.pipeline import (
    PipelineRunRequest,
    PipelineRunResponse,
    PipelineCleanRequest,
    PipelineCleanResponse,
    PipelineWeightRequest,
    PipelineWeightResponse,
    PipelineSessionStateResponse,
    PipelineHitlReviewRequest,
)
from app.services import gam2_run_pipeline
from app.services import gam2_clean_data
from app.services import ahp_service
from app.services import pipeline_session_service
from app.utils.redis_pubsub import RedisPubSubManager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/run", response_model=PipelineRunResponse)
async def run_gam2_pipeline(
    request: PipelineRunRequest,
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    [장천명 풀스택] GAM2(Geospatial AI Model 2) 데이터 감리 및 정제 파이프라인 전체 과정 비동기 실행 API
    - STEP 0(프로파일링) ➔ STEP 1(AI 감리/배제반경 파싱) ➔ STEP 2(상위법 검색) 파이프라인을 비동기로 실행합니다.
    - 이슈 #157: 파이프라인 진행 상태 및 결과를 Redis 캐시(simulation:{session_id}:step_state)에 24시간 저장합니다.
    """
    session_id = request.session_id or f"sim_{uuid.uuid4().hex[:12]}"
    try:
        # Non-blocking async wrapping to prevent blocking FastAPI event loop
        artifacts = await asyncio.to_thread(
            gam2_run_pipeline.run,
            request.domain_name,
            request.user_intent,
            request.skip_search,
            request.mock,
        )

        # 이슈 #157: Redis 인메모리 세션에 파이프라인 결과 페이로드 및 상태 24시간 저장
        payload = {
            "domain_name": request.domain_name,
            "user_intent": request.user_intent,
            "artifacts": artifacts,
        }
        await pipeline_session_service.save_session_state(
            redis=redis,
            session_id=session_id,
            step_name="STEP2_ENRICHED_COMPLETE",
            payload=payload,
            ttl=86400,
        )

        return {
            "status": "success",
            "session_id": session_id,
            "domain": request.domain_name,
            "user_intent": request.user_intent,
            "artifacts": artifacts,
            "timer_report": None,
        }
    except FileNotFoundError as e:
        logger.error(f"[Pipeline Error] File not found: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"파이프라인 실행에 필요한 도메인 데이터/파일을 찾을 수 없습니다: {str(e)}",
        )
    except Exception as e:
        logger.error(f"[Pipeline Failure] Execution failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"GAM2 파이프라인 실행 중 오류가 발생했습니다: {str(e)}",
        )


@router.get("/state/{session_id}", response_model=PipelineSessionStateResponse)
async def get_pipeline_session_state(
    session_id: str,
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    [장천명 풀스택] [이슈 #157] Redis 세션 캐시에서 파이프라인 세션 상태 조회 API
    - 세션이 존재하지 않거나 24시간 TTL 만료 시 '존재하지 않거나 만료된 세션입니다.' (HTTP 404) 반환
    """
    state = await pipeline_session_service.get_session_state(redis, session_id)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="존재하지 않거나 만료된 세션입니다.",
        )

    return {
        "status": "success",
        "session_id": session_id,
        "current_step": state.get("current_step", "UNKNOWN"),
        "payload": state.get("payload", {}),
    }


@router.post("/hitl/review", response_model=PipelineSessionStateResponse)
async def update_pipeline_hitl_review(
    request: PipelineHitlReviewRequest,
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    [장천명 풀스택] [이슈 #157] 프론트엔드 모달에서 사용자가 확정한 HITL 보정 결과를 Redis 세션에 갱신하는 API
    """
    updated = await pipeline_session_service.update_session_hitl(
        redis=redis,
        session_id=request.session_id,
        review_data=request.review_data,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="존재하지 않거나 만료된 세션입니다.",
        )

    return {
        "status": "success",
        "session_id": request.session_id,
        "current_step": updated.get("current_step", "WAITING_FOR_CLEANING"),
        "payload": updated.get("payload", {}),
    }


@router.post("/clean", response_model=PipelineCleanResponse)
async def run_data_cleaning(request: PipelineCleanRequest):
    """
    [장천명 풀스택] STEP 2 결정론적 정제 엔진 실행 API
    - 감리 확정 지침에 따라 실물 데이터셋을 지오코딩/좌표계변환/공간조인하여 .gpkg 및 .csv 생성
    """
    try:
        report_path = await asyncio.to_thread(
            gam2_clean_data.clean_domain,
            request.domain_name,
            request.csv_preview,
            not request.no_prune,
        )
        return {
            "status": "success",
            "domain": request.domain_name,
            "cleaned_files": [report_path],
            "report_file": report_path,
        }
    except Exception as e:
        logger.error(f"[Clean Engine Failure] {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"데이터 정제 엔진 연산 중 오류가 발생했습니다: {str(e)}",
        )


@router.post("/weight", response_model=PipelineWeightResponse)
async def run_ahp_weight_model(request: PipelineWeightRequest):
    """
    [장천명 풀스택] STEP 3 AHP 기반 가중치 모델 연산 API
    """
    try:
        # 기본 5인자 기준 쌍대비교 행렬 기반 AHP 일관성 검증 연산
        pairwise_matrix = [
            [1.0, 1.0, 1.0, 1.0, 1.0],
            [1.0, 1.0, 1.0, 1.0, 1.0],
            [1.0, 1.0, 1.0, 1.0, 1.0],
            [1.0, 1.0, 1.0, 1.0, 1.0],
            [1.0, 1.0, 1.0, 1.0, 1.0],
        ]
        ahp_result = ahp_service.calculate_ahp_consistency(
            matrix_size=5, pairwise_matrix=pairwise_matrix
        )
        weights_dict = {
            f"factor_{idx + 1}": w
            for idx, w in enumerate(ahp_result.get("weights", []))
        }

        return {
            "status": "success",
            "domain": request.domain_name,
            "consistency_ratio": ahp_result.get("consistency_ratio", 0.0),
            "is_valid": ahp_result.get("is_valid", True),
            "weights": weights_dict,
        }
    except Exception as e:
        logger.error(f"[Weight Model Failure] {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"가중치 모델 산출 중 오류가 발생했습니다: {str(e)}",
        )


@router.get("/stream/{session_id}")
async def stream_pipeline_progress(
    session_id: str,
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    [장천명 풀스택] GAM2 파이프라인 실시간 진행 상태 SSE 스트리밍 API
    """
    pubsub_mgr = RedisPubSubManager(redis)

    async def event_generator() -> AsyncGenerator[dict, None]:
        async for msg in pubsub_mgr.subscribe_pipeline_stream(session_id):
            yield {
                "event": "message",
                "data": json.dumps(msg, ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())
