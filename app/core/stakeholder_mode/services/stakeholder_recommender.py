# [이해관계자 페르소나 모드] 이해관계자 LLM 자동 추천 서비스
from typing import List
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import settings
from app.core.stakeholder_mode.schemas.stakeholder import (
    CandidateSite,
    OrdinanceContext,
    StakeholderCandidate
)


class StakeholderRecommendationResponse(BaseModel):
    """
    [LLM 구조화 응답 래퍼 모델]
    LLM의 with_structured_output 호출 시 추천 이해관계자 리스트를 정형화하여 수신하기 위한 래퍼 스키마입니다.
    """
    recommendations: List[StakeholderCandidate] = Field(
        ..., description="안건과의 연관성/영향도가 높은 순서대로 내림차순 정렬된 추천 이해관계자 3~5개 목록"
    )


async def recommend_stakeholders(
    topic: str,
    candidate_sites: List[CandidateSite],
    ordinance_contexts: List[OrdinanceContext],
    model_name: str = "gpt-4o-mini"
) -> List[StakeholderCandidate]:
    """
    [이해관계자 LLM 자동 추천 서비스]
    입력 안건 주제, 후보지 정량 데이터, 관련 조례 RAG 결과를 분석하여
    안건과의 연관성 및 영향도가 높은 이해관계자를 자동 도출합니다.
    """
    from app.core.stakeholder_mode.prompts.renderer import jinja_env

    # 1. LLM 클라이언트 인스턴스 생성
    llm = ChatOpenAI(
        api_key=settings.OPENAI_API_KEY,
        model=model_name,
        temperature=0.7
    )
    
    # 2. Pydantic 구조화 출력 바인딩 설정
    structured_llm = llm.with_structured_output(StakeholderRecommendationResponse)

    # 3. 입력 데이터 준비
    sites_data = [site.model_dump() for site in candidate_sites]
    ords_data = [ord_item.model_dump() for ord_item in ordinance_contexts]

    # 4. 프롬프트 렌더링
    template = jinja_env.get_template("stakeholder/recommend.j2")
    prompt_text = template.render(
        topic=topic,
        candidate_contexts=sites_data,
        ordinance_contexts=ords_data
    )

    # 5. LLM API 비동기 호출
    response = await structured_llm.ainvoke(prompt_text)

    # 6. 중요도(importance_score) 내림차순 정렬
    sorted_candidates = sorted(
        response.recommendations, 
        key=lambda x: x.importance_score, 
        reverse=True
    )
    
    return sorted_candidates
