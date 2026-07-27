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
        ..., description="추천된 이해관계자 3~5개 목록"
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
    본 심의에 직접적인 영향을 받는 대표 이해관계자를 3~5개 자동 도출합니다.
    """
    # 1. LLM 클라이언트 인스턴스 생성
    llm = ChatOpenAI(
        api_key=settings.OPENAI_API_KEY,
        model=model_name,
        temperature=0.7
    )
    
    # 2. Pydantic 구조화 출력 바인딩 설정
    structured_llm = llm.with_structured_output(StakeholderRecommendationResponse)

    # 3. 입력 데이터 요약 텍스트 구성
    sites_summary = "\n".join(
        [f"- {site.name} ({site.candidate_id}): {site.attributes}" for site in candidate_sites]
    )
    ordinance_summary = "\n".join(
        [f"- [{ord_item.chunk_id}] {ord_item.ordinance_name}: {ord_item.content}" for ord_item in ordinance_contexts]
    )

    # 4. 프롬프트 구성
    system_prompt = """당신은 입지 선정 및 도시계획 심의 전문 AI 컨설턴트입니다.
주어진 토론 주제, 후보지 정보, 관련 조례 내용을 분석하여, 본 안건 평가에 직접적으로 영향받거나 주요 의견을 제시해야 하는 대표 이해관계자를 3~5개 추천해 주세요.

이해관계자는 지역 주민, 시설 이용자, 사업 주관 부서, 교통 담당 부서, 지역 상인 등 다양한 관점을 반영할 수 있도록 구성해 주세요."""

    user_prompt = f"""[토론 주제]
{topic}

[후보지 데이터]
{sites_summary}

[관련 조례 Context]
{ordinance_summary}

위 안건에 대해 3~5개의 이해관계자를 추천해 주세요."""

    # 5. LLM 비동기 호출 및 결과 반환
    response = await structured_llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt)
    ])

    return response.recommendations
