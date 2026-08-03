"""
동적 이해관계자 API 라우터 (Stakeholders API Route)
- 클라이언트(프론트엔드)에서 접근 가능한 REST API 엔드포인트를 정의합니다.
- `/generate` POST 요청을 받아 입력 데이터(주제, GIS, 조례 등)를 `StakeholderGenerator` 서비스 클래스로 전달하고, 생성된 최종 추천 이해관계자 리스트를 반환합니다.
"""
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from langchain_openai import ChatOpenAI

from app.config import settings
from app.core.stakeholder_mode.schemas.dynamic_stakeholder import StakeholderCandidate
from app.core.stakeholder_mode.services.stakeholder_generator import StakeholderGenerator

router = APIRouter()

class StakeholderGenerationRequest(BaseModel):
    topic: str
    purpose: str
    gis_data: Dict[str, Any]
    ordinance_data: Dict[str, Any]
    extra_data: Optional[Dict[str, Any]] = None

@router.post("/generate", response_model=List[StakeholderCandidate], status_code=status.HTTP_200_OK)
async def generate_dynamic_stakeholders(request: StakeholderGenerationRequest):
    """
    [동적 이해관계자 생성 API]
    안건 주제, GIS 기반 주변 인프라, 조례 데이터를 입력받아
    직접/간접 이해관계자를 폭넓게 도출하고(Discovery),
    중복 집단을 묶어내며(Refinement),
    최종적으로 5~8개의 추천 목록을 중요도/신뢰도와 함께 평가(Evaluation)하여 반환합니다.
    """
    try:
        # LLM Client (기본 모델 설정)
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
