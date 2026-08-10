from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from langchain_openai import ChatOpenAI
import json
from fastapi.responses import StreamingResponse

from app.config import settings
from app.core.stakeholder_mode.schemas.dynamic_stakeholder import StakeholderCandidate
from app.core.stakeholder_mode.services.stakeholder_generator import StakeholderGenerator
from app.core.stakeholder_mode.graph.dynamic_builder import dynamic_discussion_graph
from app.core.stakeholder_mode.graph.dynamic_state import DynamicDiscussionState

router = APIRouter()

class StakeholderGenerationRequest(BaseModel):
    topic: str
    purpose: str
    gis_data: Dict[str, Any]
    ordinance_data: Dict[str, Any]
    extra_data: Optional[Dict[str, Any]] = None

@router.post("/generate", response_model=List[StakeholderCandidate], status_code=status.HTTP_200_OK)
async def generate_dynamic_stakeholders(request: StakeholderGenerationRequest):
    try:
        llm = ChatOpenAI(api_key=settings.OPENAI_API_KEY, model="gpt-4o-mini", temperature=0.7)
        generator = StakeholderGenerator(llm_client=llm)
        
        candidates = await generator.generate_candidates(
            topic=request.topic,
            purpose=request.purpose,
            gis_data=request.gis_data,
            ordinance_data=request.ordinance_data,
            extra_data=request.extra_data
        )
        return candidates
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"이해관계자 파이프라인 처리 중 오류가 발생했습니다: {str(e)}"
        )

class DynamicDiscussionRequest(BaseModel):
    personas: List[Dict[str, Any]]
    topic: str
    gis_data: Dict[str, Any]
    ordinance_contexts: List[str]

@router.post("/dynamic/discuss/stream")
async def stream_dynamic_discussion(request: DynamicDiscussionRequest):
    """
    다자간 페르소나 실시간 토론 스트리밍 엔드포인트
    LangGraph의 astream을 이용하여 노드 업데이트 발생 시 마다 Chunk를 전송합니다.
    """
    async def event_generator():
        try:
            # 페르소나 리스트를 기반으로 active_participants 구성 (프론트가 주는 데이터에 role, name 등이 있음)
            # 백엔드 스키마 PersonaConfig에 맞게 매핑
            mapped_personas = []
            active_ids = []
            for idx, p in enumerate(request.personas):
                pid = f"persona_{idx}"
                active_ids.append(pid)
                mapped_personas.append({
                    "persona_id": pid,
                    "display_name": p.get("name", f"페르소나 {idx}"),
                    "stakeholder_type": p.get("role", "unknown"),
                    "relationship_to_topic": p.get("description", "관계 없음"),
                    "importance_grade": p.get("importance_grade", "C"),
                    "initial_position": "conditional_support",
                    "interests": p.get("keywords", [])
                })
                
            initial_state = DynamicDiscussionState(
                project_id="stream_project",
                topic=request.topic,
                site_information=json.dumps(request.gis_data, ensure_ascii=False),
                personas=mapped_personas,
                active_participants=active_ids,
                css_levels={},
                ordinance_contexts=[{"content": ctx} for ctx in request.ordinance_contexts],
                messages=[],
                next_speaker="supervisor",
                round_count=0,
                rebuttal_target="",
                rebuttal_count=0,
                evaluations={},
                final_scenarios={},
                is_finished=False
            )
            
            async for chunk in dynamic_discussion_graph.astream(initial_state, stream_mode="updates"):
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                
            yield "data: [DONE]\n\n"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")
