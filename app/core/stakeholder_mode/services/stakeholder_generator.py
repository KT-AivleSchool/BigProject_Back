"""
동적 이해관계자 생성 오케스트레이터 (Stakeholder Generator Service)
- Discovery -> Refinement -> Evaluation 으로 이어지는 3단계 LLM 프롬프트 체인을 순차적으로 실행하는 서비스 로직입니다.
- 프론트엔드 또는 API 라우터로부터 데이터를 전달받아 LangChain 기반 비동기 호출(`ainvoke`)을 수행하고, 마크다운이 포함될 수 있는 응답을 안전하게 JSON으로 전처리하여 Pydantic 모델 리스트로 반환합니다.
"""
from typing import List, Dict, Any
import json

from langchain_core.messages import HumanMessage

from app.core.stakeholder_mode.schemas.dynamic_stakeholder import StakeholderCandidate
from app.core.stakeholder_mode.prompts.discovery_prompt import DISCOVERY_PROMPT
from app.core.stakeholder_mode.prompts.refinement_prompt import REFINEMENT_PROMPT
from app.core.stakeholder_mode.prompts.evaluation_prompt import EVALUATION_PROMPT

class StakeholderGenerator:
    """
    [이해관계자 동적 생성 오케스트레이터]
    주제, GIS, RAG 데이터를 입력받아 발굴 -> 정제 -> 평가 3단계 체인을 실행하여
    최종 추천 이해관계자 후보군을 생성합니다.
    """
    
    def __init__(self, llm_client):
        # 실제 구현에서는 LangChain이나 OpenAI client 등을 주입받아 사용
        self.llm_client = llm_client

    async def generate_candidates(
        self, 
        topic: str, 
        purpose: str,
        gis_data: Dict[str, Any], 
        ordinance_data: Dict[str, Any],
        extra_data: Dict[str, Any] = None
    ) -> List[StakeholderCandidate]:
        """전체 파이프라인 실행"""
        
        # 1. Discovery (발굴)
        discovery_result = await self._run_discovery(
            topic, purpose, gis_data, ordinance_data, extra_data or {}
        )
        
        # 2. Refinement (정제 및 중복 제거)
        refined_result = await self._run_refinement(discovery_result)
        
        # 3. Evaluation (중요도 및 신뢰도 평가)
        final_candidates_json = await self._run_evaluation(refined_result)
        
        # JSON 파싱 및 Pydantic 객체로 변환
        candidates = []
        try:
            # Markdown JSON 블록 래핑을 대비한 텍스트 전처리
            cleaned_json = final_candidates_json.strip()
            if cleaned_json.startswith("```json"):
                cleaned_json = cleaned_json.replace("```json", "", 1)
            elif cleaned_json.startswith("```"):
                cleaned_json = cleaned_json.replace("```", "", 1)
            
            if cleaned_json.endswith("```"):
                cleaned_json = cleaned_json[:-3]
            
            cleaned_json = cleaned_json.strip()

            parsed_data = json.loads(cleaned_json)
            for item in parsed_data:
                candidates.append(StakeholderCandidate(**item))
        except Exception as e:
            # 파싱 에러 처리 (실제 환경에서는 재시도 로직 등 추가)
            print(f"Error parsing final candidates: {e}\nRaw JSON: {final_candidates_json}")
            
        return candidates

    async def _run_discovery(self, topic: str, purpose: str, gis_data: dict, ordinance_data: dict, extra_data: dict) -> str:
        prompt = DISCOVERY_PROMPT.format(
            topic=topic,
            purpose=purpose,
            gis_data=json.dumps(gis_data, ensure_ascii=False),
            ordinance_data=json.dumps(ordinance_data, ensure_ascii=False),
            extra_data=json.dumps(extra_data, ensure_ascii=False)
        )
        response = await self.llm_client.ainvoke([HumanMessage(content=prompt)])
        return response.content

    async def _run_refinement(self, candidates_json: str) -> str:
        prompt = REFINEMENT_PROMPT.format(candidates=candidates_json)
        response = await self.llm_client.ainvoke([HumanMessage(content=prompt)])
        return response.content

    async def _run_evaluation(self, refined_candidates_json: str) -> str:
        prompt = EVALUATION_PROMPT.format(refined_candidates=refined_candidates_json)
        response = await self.llm_client.ainvoke([HumanMessage(content=prompt)])
        return response.content
