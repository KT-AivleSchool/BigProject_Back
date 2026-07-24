from fastapi import APIRouter, BackgroundTasks, Depends
from sse_starlette.sse import EventSourceResponse

from app.utils.redis_pubsub import RedisPubSubManager
from app.core.gam2_pipeline import gam2_clean_data
from app.api.deps import get_redis

router = APIRouter()


def run_full_gam2_pipeline(session_id: str):
    import redis
    import json
    from app.config import settings

    r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

    try:

        def progress_callback(step: str, progress: int, text: str):
            payload = {
                "step": step,
                "progress": progress,
                "text": text,
                "is_finished": False,
            }
            r.publish(f"pipeline:{session_id}", json.dumps(payload, ensure_ascii=False))

        progress_callback("audit", 5, "GAM2 데이터 프로파일링 시작")
        import shutil
        import os
        import scripts.gam2.run_audit_judgment_test as A
        from app.config import (
            STEP1_OUTPUT_DIR,
            STEP2_OUTPUT_DIR,
            domain_prefix,
            domain_paths,
        )

        prefix = domain_prefix(session_id)
        paths = domain_paths(session_id)

        A.set_domain(session_id)

        # 1. 프로파일 생성
        from app.core.gam2_pipeline.gam2_profile import profile_folder, save_profiles

        profiles = profile_folder(paths["data"])
        save_profiles(profiles, paths["profiles"])
        progress_callback("audit", 15, "데이터 프로파일링 완료, LLM 정제 룰 판정 시작")

        # 2. AI 판정 결과 생성
        fixtures = A.build_fixtures()
        llm = A.OpenAIClient(model="gpt-4o-mini")

        def harness_progress(did):
            progress_callback("audit", 30, f"LLM 판정 중: {did}")

        judgments, raw_preds = A.run_harness(
            llm, fixtures, A._DOMAIN, progress=harness_progress
        )

        # 결과를 파일로 저장
        A.save_results(
            session_id, judgments, raw_preds, facility="지상형", region="전체"
        )
        progress_callback("audit", 45, "LLM 판정 룰(JSON) 생성 완료")

        # 2-5. 배제반경 상위법 검색 (Ordinance Acquisition)
        progress_callback("audit", 50, "상위법 배제반경 자동 검색 시작")
        try:
            A.enrich_with_search(region="전체")
            src_json = os.path.join(
                STEP1_OUTPUT_DIR, f"{prefix}_audit_result_enriched.json"
            )
            if not os.path.exists(src_json):
                src_json = os.path.join(STEP1_OUTPUT_DIR, f"{prefix}_audit_result.json")
        except Exception:
            src_json = os.path.join(STEP1_OUTPUT_DIR, f"{prefix}_audit_result.json")

        # Zero-Click 이므로 HITL 단계를 자동 통과 처리 (reviewed.json 복사)
        dst_json = os.path.join(
            STEP1_OUTPUT_DIR, f"{prefix}_audit_result_reviewed.json"
        )
        shutil.copy2(src_json, dst_json)

        # 3. 데이터 정제 시작 (Step 2)
        progress_callback("clean", 55, "데이터 정제 시작")
        gam2_clean_data.clean_domain(
            session_id,
            csv_preview=False,
            prune=True,
            progress_callback=progress_callback,
        )
        progress_callback("clean", 85, "데이터 정제 완료")

        # 4. 가중치 모델 초기화 (Step 3) - 지표 및 반경 제안 도출
        progress_callback("weight", 90, "가중치 산출 시작 (AHP 초기화)")
        import app.core.gam2_pipeline.gam2_weight_model as W

        try:
            reviewed = json.load(open(dst_json, encoding="utf-8"))
            rpt_path = os.path.join(STEP2_OUTPUT_DIR, f"{prefix}_clean_report.json")
            report = json.load(open(rpt_path, encoding="utf-8"))

            facility = reviewed.get("facility_inference", {}).get("facility", "시설")
            inds = W.define_indicators(reviewed, report)
            W.suggest_radius(facility, inds)
            progress_callback(
                "weight", 95, f"지표 {len(inds)}개 정의 및 반경 제안 완료"
            )
        except Exception as we:
            progress_callback(
                "weight", 95, f"가중치 초기화 생략 (정보 부족): {str(we)}"
            )

        payload = {
            "step": "clean_done",
            "progress": 100,
            "text": "전체 파이프라인 가동 완료",
            "is_finished": True,
        }
        r.publish(f"pipeline:{session_id}", json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        import traceback

        traceback.print_exc()
        payload = {
            "step": "error",
            "progress": 100,
            "text": f"에러 발생: {str(e)}",
            "is_finished": True,
        }
        r.publish(f"pipeline:{session_id}", json.dumps(payload, ensure_ascii=False))
    finally:
        r.close()


@router.post("/full")
async def start_full_pipeline(session_id: str, background_tasks: BackgroundTasks):
    """
    1~3단계 통합: 프로파일 ➔ AI 감리 ➔ 데이터 정제 ➔ 가중치 산출 파이프라인 시작 (Zero-Click)
    """
    background_tasks.add_task(run_full_gam2_pipeline, session_id)
    return {"message": "통합 파이프라인 시작", "session_id": session_id}


@router.get("/progress/{session_id}")
async def stream_progress(session_id: str, redis_client=Depends(get_redis)):
    """
    파이프라인 진행 상태를 SSE로 스트리밍
    """
    pubsub = RedisPubSubManager(redis_client)

    async def event_generator():
        import json

        async for data in pubsub.subscribe_pipeline_stream(session_id):
            yield {
                "event": "message",
                "data": json.dumps(data, ensure_ascii=False),
            }
            if data.get("is_finished", False):
                break

    return EventSourceResponse(event_generator())
