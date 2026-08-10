"""
이해관계자 파이프라인 단독 테스트 스크립트
- 작성된 동적 이해관계자 파이프라인 로직(발굴->정제->평가)이 LangChain과 연동되어 정상 작동하는지 더미 데이터를 주입하여 검증하는 터미널 실행용 파일입니다.
"""
import asyncio
from langchain_openai import ChatOpenAI
from app.core.stakeholder_mode.services.stakeholder_generator import StakeholderGenerator
from app.config import settings

async def main():
    # Initialize LLM
    llm = ChatOpenAI(api_key=settings.OPENAI_API_KEY, model="gpt-4o-mini", temperature=0.7)
    
    # Initialize Generator
    generator = StakeholderGenerator(llm_client=llm)
    
    # Dummy Data
    topic = "구도심 내 쓰레기 소각장 건립"
    purpose = "폐기물 처리 효율화 및 지역 난방 에너지 공급"
    
    gis_data = {
        "location": "A시 B구 C동 외곽 지역",
        "nearby_facilities": [
            {"type": "초등학교", "distance": "500m"},
            {"type": "대단지 아파트", "distance": "1km"},
            {"type": "하천", "distance": "300m"}
        ]
    }
    
    ordinance_data = {
        "relevant_laws": [
            "A시 폐기물 처리시설 설치 촉진 및 주변지역지원 등에 관한 조례",
            "환경영향평가법"
        ],
        "key_clauses": [
            "시설 경계로부터 300m 이내 지역 주민에 대한 지원금 지급",
            "대기오염물질 배출 기준 엄격 적용"
        ]
    }
    
    extra_data = {
        "current_sentiment": "주민 반발이 예상되나 시 예산 확보는 완료됨"
    }
    
    print("시작: 동적 이해관계자 생성 파이프라인 (Discovery -> Refinement -> Evaluation)...")
    candidates = await generator.generate_candidates(
        topic=topic,
        purpose=purpose,
        gis_data=gis_data,
        ordinance_data=ordinance_data,
        extra_data=extra_data
    )
    
    print(f"\n완료! 총 {len(candidates)}개의 핵심 이해관계자가 도출되었습니다.\n")
    for idx, c in enumerate(candidates, 1):
        print(f"[{idx}] {c.display_name} ({c.stakeholder_type})")
        print(f"  - 중요도: {c.importance_grade} | 신뢰도: {c.evidence_confidence}")
        print(f"  - 연관성: {c.relationship_to_topic}")
        print(f"  - 추천 사유: {c.recommendation_reason}")
        print("-" * 50)

if __name__ == "__main__":
    asyncio.run(main())
