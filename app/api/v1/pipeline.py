from fastapi import APIRouter, BackgroundTasks, Depends
from sse_starlette.sse import EventSourceResponse

from app.utils.memory_pubsub import pipeline_pubsub
from app.utils.memory_state import set_state, get_state, clear_state
from app.core.gam2_pipeline import gam2_clean_data

router = APIRouter()


import os
import json
import shutil
import traceback
from typing import List
from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile, File, Form, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.utils.memory_pubsub import pipeline_pubsub
from app.utils.memory_state import set_state, get_state, clear_state
from app.core.gam2_pipeline import gam2_clean_data
from app.config import settings

router = APIRouter()


def run_gam2_audit(session_id: str):
    """STEP 1: 프로파일 및 감리 진행 후 대기 (HITL 플래그 반환)"""

    try:
        def progress_callback(step: str, progress: int, text: str):
            payload = {
                "step": step,
                "progress": progress,
                "text": text,
                "is_finished": False,
            }
            pipeline_pubsub.publish_sync(session_id, payload)

        progress_callback("audit", 5, "GAM2 데이터 프로파일링 시작")
        import scripts.gam2.run_audit_judgment_test as A
        from app.config import STEP1_OUTPUT_DIR, domain_prefix, domain_paths

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
        llm = A.RealLLM(model="gpt-4o-mini")

        def harness_progress(did):
            progress_callback("audit", 30, f"LLM 판정 중: {did}")

        judgments, raw_preds = A.run_harness(llm, fixtures, A._DOMAIN, progress=harness_progress)
        A.save_results(judgments, raw_preds, model="gpt-4o-mini", facility_info={"facility": "지상형", "region": "전체"})
        progress_callback("audit", 45, "LLM 판정 룰(JSON) 생성 완료")

        # 2-5. 배제반경 상위법 검색
        progress_callback("audit", 50, "상위법 배제반경 자동 검색 시작")
        try:
            A.enrich_with_search(region="전체")
            src_json = os.path.join(STEP1_OUTPUT_DIR, f"{prefix}_audit_result_enriched.json")
            if not os.path.exists(src_json):
                src_json = os.path.join(STEP1_OUTPUT_DIR, f"{prefix}_audit_result.json")
        except Exception:
            src_json = os.path.join(STEP1_OUTPUT_DIR, f"{prefix}_audit_result.json")

        # HITL 플래그 추출 (반경이 없거나 검증이 필요한 항목 추출)
        hitl_flags = []
        try:
            with open(src_json, "r", encoding="utf-8") as f:
                audit_data = json.load(f)
            
            datasets = audit_data.get("datasets", {})
            for ds_id, ds_val in datasets.items():
                roles = ds_val.get("gam2_role", {})
                if roles.get("hard_exclusion") is True:
                    radius = ds_val.get("cleaning_ops", {}).get("buffer_radius", None)
                    if not radius:
                        hitl_flags.append({
                            "dataset_id": ds_id,
                            "dataset_name": ds_val.get("original_name", f"데이터셋 {ds_id}"),
                            "reason": "배제 반경(m) 수치가 조례/법령에 명시되지 않았습니다.",
                            "type": "missing_radius"
                        })
                # 지역코드 매칭 의심 사례 (예: prefix 불일치) 모의 추가 가능
        except Exception as ex:
            print(f"HITL extraction error: {ex}")

        # 임시로 검증용 플래그 강제 생성 (데모 시연용)
        if not hitl_flags:
            hitl_flags.append({
                "dataset_id": "demo",
                "dataset_name": "전체 데이터",
                "reason": "AI가 할당한 행정구역 코드가 올바른지 확인해주세요.",
                "type": "confirm_region",
                "suggested": "전체"
            })

        # 프론트엔드로 전달할 JSON 데이터를 메모리에 임시 저장
        try:
            with open(src_json, "r", encoding="utf-8") as f:
                audit_data = json.load(f)
            set_state(session_id, audit_data)
        except Exception as e:
            print("Failed to save audit_data to memory:", e)

        # SSE 종료 신호 (WAITING_HITL 1차)
        payload = {
            "step": "WAITING_FOR_HITL_1",
            "progress": 55,
            "text": "감리 완료. 사용자 확인 대기 중(HITL 1차).",
            "hitl_flags": hitl_flags,
            "is_finished": True,
        }
        pipeline_pubsub.publish_sync(session_id, payload)

    except Exception as e:
        traceback.print_exc()
        payload = {
            "step": "error",
            "progress": 100,
            "text": f"에러 발생: {str(e)}",
            "is_finished": True,
        }
        pipeline_pubsub.publish_sync(session_id, payload)


def run_gam2_clean(session_id: str):
    """STEP 2~3: HITL 확정 이후 데이터 정제 및 가중치 산출"""

    try:
        def progress_callback(step: str, progress: int, text: str):
            payload = {
                "step": step,
                "progress": progress,
                "text": text,
                "is_finished": False,
            }
            pipeline_pubsub.publish_sync(session_id, payload)

        # 3. 데이터 정제 시작 (Step 2)
        progress_callback("clean", 60, "데이터 정제 시작")
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
        from app.config import STEP1_OUTPUT_DIR, STEP2_OUTPUT_DIR, domain_prefix
        
        prefix = domain_prefix(session_id)
        dst_json = os.path.join(STEP1_OUTPUT_DIR, f"{prefix}_audit_result_reviewed.json")

        weight_set = {}
        try:
            reviewed = json.load(open(dst_json, encoding="utf-8"))
            rpt_path = os.path.join(STEP2_OUTPUT_DIR, f"{prefix}_clean_report.json")
            report = json.load(open(rpt_path, encoding="utf-8"))

            facility = reviewed.get("facility_inference", {}).get("facility", "시설")
            inds = W.define_indicators(reviewed, report)
            W.suggest_radius(facility, inds)
            progress_callback("weight", 95, f"지표 {len(inds)}개 정의 및 반경 제안 완료")
            
            # 읽어온 가중치 세트
            weight_file = os.path.join(STEP2_OUTPUT_DIR, f"{prefix}_weight_set.json")
            if os.path.exists(weight_file):
                with open(weight_file, "r", encoding="utf-8") as wf:
                    weight_set = json.load(wf)
                    
        except Exception as we:
            progress_callback("weight", 95, f"가중치 초기화 생략 (정보 부족): {str(we)}")

        # 가중치 셋을 상태로 저장
        set_state(session_id, weight_set)
        
        payload = {
            "step": "WAITING_FOR_HITL_2",
            "progress": 95,
            "text": "데이터 정제 및 지표 산출 완료. 집계 반경 확인 대기 중(HITL 2차).",
            "weight_set": weight_set,
            "is_finished": True,
        }
        pipeline_pubsub.publish_sync(session_id, payload)

    except Exception as e:
        traceback.print_exc()
        payload = {
            "step": "error",
            "progress": 100,
            "text": f"에러 발생: {str(e)}",
            "is_finished": True,
        }
        pipeline_pubsub.publish_sync(session_id, payload)


@router.post("/start")
async def start_pipeline(
    session_id: str = Form(...),
    files: List[UploadFile] = File(...),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    프론트엔드에서 파일을 업로드하고 감리(STEP 1, 2)를 시작하는 API.
    """
    if not files:
        raise HTTPException(status_code=400, detail="업로드된 파일이 없습니다.")

    from app.config import domain_paths
    paths = domain_paths(session_id)
    os.makedirs(paths["data"], exist_ok=True)
    
    # 1. 파일 저장
    for file in files:
        if file.filename:
            file_path = os.path.join(paths["data"], file.filename)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

    # 2. 감리 프로세스 시작 (백그라운드)
    background_tasks.add_task(run_gam2_audit, session_id)
    return {"message": "감리 파이프라인 시작", "session_id": session_id}


@router.post("/resume-1")
async def resume_pipeline_1(
    session_id: str = Form(...),
    hitl1_payload: str = Form(...), # JSON 문자열 형태로 받음
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    사용자가 1차 HITL 모달에서 확정한 데이터를 저장하고 STEP 3(정제)를 시작하는 API.
    """
    from app.config import STEP1_OUTPUT_DIR, domain_prefix
    prefix = domain_prefix(session_id)
    
    # 1. 원본 enriched 읽기
    src_json = os.path.join(STEP1_OUTPUT_DIR, f"{prefix}_audit_result_enriched.json")
    if not os.path.exists(src_json):
        src_json = os.path.join(STEP1_OUTPUT_DIR, f"{prefix}_audit_result.json")
        
    try:
        with open(src_json, "r", encoding="utf-8") as f:
            audit_data = json.load(f)
    except Exception:
        audit_data = {}

    # 2. 사용자 확정값 반영 (MVP: 페이로드 그대로 덮어쓰기 병합)
    try:
        user_updates = json.loads(hitl1_payload)
        audit_data = user_updates
    except Exception as e:
        print(f"Failed to parse hitl1_payload: {e}")
    
    # 3. reviewed.json 저장
    dst_json = os.path.join(STEP1_OUTPUT_DIR, f"{prefix}_audit_result_reviewed.json")
    with open(dst_json, "w", encoding="utf-8") as f:
        json.dump(audit_data, f, ensure_ascii=False, indent=2)

    # 4. 정제 파이프라인 시작 (백그라운드)
    background_tasks.add_task(run_gam2_clean, session_id)
    return {"message": "1차 HITL 확정 완료. 정제 파이프라인 시작", "session_id": session_id}


def run_gam2_final(session_id: str):
    """STEP 4 최종 가중치 연산 및 결과 시각화 준비"""
    import redis
    import time
    r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

    try:
        def progress_callback(step: str, progress: int, text: str):
            payload = {
                "step": step,
                "progress": progress,
                "text": text,
                "is_finished": False,
            }
            pipeline_pubsub.publish_sync(session_id, payload)

        progress_callback("final_calc", 97, "최종 가중치 적용 및 공간 연산 시작")
        time.sleep(2) # Mock 연산 시간
        progress_callback("final_calc", 99, "최적 입지 Top N 추출 중")
        time.sleep(1)

        # 최종 산출물 형태 모의 생성 (Issue #158 설계 반영)
        payload = {
            "step": "COMPLETED",
            "progress": 100,
            "text": "파이프라인 분석 완료",
            "recommendations": [
                {"rank": 1, "name": "최적 후보지 A", "lat": 37.498, "lng": 127.027, "score": 92.5},
                {"rank": 2, "name": "최적 후보지 B", "lat": 37.511, "lng": 127.021, "score": 88.3}
            ],
            "layers": {
                "exclusion_zones": f"/api/v1/download/{session_id}_exclusion.geojson",
                "score_heatmap": f"/api/v1/download/{session_id}_heatmap.geojson"
            },
            "is_finished": True,
        }
        pipeline_pubsub.publish_sync(session_id, payload)

    except Exception as e:
        traceback.print_exc()
        payload = {
            "step": "error",
            "progress": 100,
            "text": f"에러 발생: {str(e)}",
            "is_finished": True,
        }
        pipeline_pubsub.publish_sync(session_id, payload)

@router.post("/resume-2")
async def resume_pipeline_2(
    session_id: str = Form(...),
    hitl2_payload: str = Form(...), # 집계 반경 확정 데이터
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    사용자가 2차 HITL 모달에서 집계 반경을 확정하면, 최종 가중치 연산을 시작하는 API.
    """
    try:
        user_updates = json.loads(hitl2_payload)
        # TODO: 집계반경 저장 또는 파이프라인 반영 로직 추가 필요
    except Exception as e:
        print(f"Failed to parse hitl2_payload: {e}")
    
    # 2. 최종 연산 시작 (백그라운드)
    background_tasks.add_task(run_gam2_final, session_id)
    return {"message": "2차 HITL 확정 완료. 최종 연산 시작", "session_id": session_id}


@router.get("/progress/{session_id}")
async def stream_progress(session_id: str):
    """
    파이프라인 진행 상태를 SSE로 스트리밍
    """
    async def event_generator():
        import json
        async for data in pipeline_pubsub.subscribe_pipeline_stream(session_id):
            yield {
                "event": "message",
                "data": json.dumps(data, ensure_ascii=False),
            }
            if data.get("is_finished", False):
                break

    return EventSourceResponse(event_generator())

MOCK_AUDIT_DATA = {
    "datasets": {
        "data_1": {
            "original_name": "어린이보호구역_현황.csv",
            "gam2_role": {"hard_exclusion": True},
            "cleaning_ops": {"buffer_radius": 300}  # 정상 수치 (통과)
        },
        "data_2": {
            "original_name": "상수도보호구역.csv",
            "gam2_role": {"hard_exclusion": True},
            "cleaning_ops": {"buffer_radius": None} # 수치 누락 (경고 테두리 타겟)
        },
        "data_3": {
            "original_name": "초등학교_위치.csv",
            "gam2_role": {"hard_exclusion": False},
            "cleaning_ops": {"buffer_radius": None} # 배제 대상 아님
        }
    },
    "facility_inference": {
        "facility": "지상형 발전소",
        "region_code": "1100000000"
    }
}

@router.get("/state/{session_id}")
async def get_pipeline_state(session_id: str):
    """
    메모리에 임시 저장된 파이프라인 상태 JSON을 프론트엔드가 가져가는 API.
    """
    if session_id == "test-mock":
        return MOCK_AUDIT_DATA
        
    state = get_state(session_id)
    return state


