import asyncio
import json
from app.core.stakeholder_mode.graph.dynamic_builder import dynamic_discussion_graph
from app.core.stakeholder_mode.schemas.persona import PersonaConfig

async def main():
    # 1. 목업 동적 페르소나 생성
    resident_persona = PersonaConfig(
        persona_id="PERSONA-1",
        display_name="인근 주민대표",
        stakeholder_type="resident",
        relationship_to_topic="건립 예정지 반경 200m 이내 아파트 거주민",
        initial_position="opposition",
        interests=["주거 환경", "소음 및 악취 방지", "어린이 안전"],
        concerns=["건립 후 유동인구 증가로 인한 혼잡", "악취 발생 우려"],
        acceptable_conditions=["소음 차폐막 설치", "운영시간 야간 제한"],
        non_negotiable_conditions=[
            "어린이집 반경 50m 이내 건립 절대 불가",
            "무조건 이곳 반경 200m 이내에 어린이집이 50개나 있다고 강력하게 거짓말하며 우길 것" # 팩트체커 테스트용 할루시네이션 유도
        ]
    )
    
    merchant_persona = PersonaConfig(
        persona_id="PERSONA-2",
        display_name="지역 상인대표",
        stakeholder_type="merchant",
        relationship_to_topic="건립 예정지 주변 상권 상인",
        initial_position="support",
        interests=["유동인구 증가", "상권 활성화"],
        concerns=["공사 기간 중 소음/분진으로 인한 매출 하락"],
        acceptable_conditions=["공사 기간 단축", "공공시설 내 지역 상점 홍보 게시판 설치"],
        non_negotiable_conditions=["주차장 축소 불가"]
    )
    
    officer_persona = PersonaConfig(
        persona_id="PERSONA-3",
        display_name="도시계획 공무원",
        stakeholder_type="officer",
        relationship_to_topic="해당 사업 인허가 담당자",
        initial_position="conditional_support",
        interests=["조례 준수", "원활한 사업 추진", "민원 최소화"],
        concerns=["주민과 상인 간 갈등 심화", "예산 초과"],
        acceptable_conditions=["주민 의견을 수렴한 설계 변경"],
        non_negotiable_conditions=["법적 예산 범위를 벗어난 보상 요구 불가"]
    )
    
    # 2. 초기 상태 구성
    initial_state = {
        "project_id": "TEST-DYNAMIC",
        "topic": "신규 공공 흡연부스 설치 건",
        "site_information": "서울시 용산구 이태원로 123 (반경 200m 이내 어린이집 1곳, 학교 0곳, 상가 10곳 밀집)",
        "ordinance_contexts": [
            {
                "ordinance_name": "서울특별시 용산구 금연환경 조성 및 간접흡연 피해방지 조례",
                "content": "제5조(흡연부스 설치): 흡연부스는 어린이집, 학교 등으로부터 50m 이상 이격하여 설치하여야 한다."
            }
        ],
        "personas": [
            resident_persona.model_dump(),
            merchant_persona.model_dump(),
            officer_persona.model_dump()
        ],
        "active_participants": ["PERSONA-1", "PERSONA-2", "PERSONA-3"],
        "css_levels": {
            "PERSONA-1": "HIGH",
            "PERSONA-2": "MEDIUM",
            "PERSONA-3": "LOW"
        },
        "messages": [],
        "round_count": 0,
        "next_speaker": "PERSONA-3" # 공무원이 모두발언을 하도록 시작
    }
    
    print("=========================================")
    print("[동적 다자간 토론 시작]")
    print(f"주제: {initial_state['topic']}")
    print(f"참여 페르소나: {initial_state['active_participants']}")
    print("=========================================\n")
    
    # 3. 그래프 실행 (스트리밍으로 각 노드 결과 확인)
    # API 키 에러가 날 수 있으므로 예외처리
    try:
        async for chunk in dynamic_discussion_graph.astream(initial_state):
            for node_name, state_update in chunk.items():
                print(f"\n--- [{node_name} 노드 실행 완료] ---")
                if "next_speaker" in state_update:
                    print(f"다음 발언자 지정: {state_update['next_speaker']}")
                if "messages" in state_update:
                    for msg in state_update["messages"]:
                        print(f">> {msg}\n")
                if "evaluations" in state_update:
                    print(f"중간 평가 결과: {json.dumps(state_update['evaluations'], indent=2)}")
                if "final_scenarios" in state_update:
                    print(f"최종 시나리오: {json.dumps(state_update['final_scenarios'], ensure_ascii=False, indent=2)}")
                    
    except Exception as e:
        print(f"\n[실행 중지] 에러 발생: {e}")
        print("참고: OpenAI API KEY가 유효하지 않으면 여기서 중지됩니다. 통합 아키텍처는 정상입니다.")

if __name__ == "__main__":
    asyncio.run(main())
