"""
평가(Evaluation) 프롬프트 템플릿
- 파이프라인의 3단계 프롬프트입니다.
- 정제된 후보군에 대해 '중요도(A, B, C)'와 '근거 신뢰도(high, medium, low)'를 각각 객관적으로 평가하고, 최종적으로 모의 심의에 참여시킬 핵심 이해관계자 5~8개를 선정해 JSON 형태로 반환하도록 지시합니다.
"""

EVALUATION_PROMPT = """
당신은 정제된 이해관계자 후보군의 중요도와 근거 신뢰도를 객관적으로 평가(Evaluation)하고 최종 추천 목록을 구성하는 전문가입니다.

[정제된 이해관계자 후보군]
{refined_candidates}

[평가 기준]
1. 중요도 (importance_grade): 직접 영향 정도, 피해/이익 규모, 취약성, 법적 관련성, 운영 책임, 의사결정 영향력 등을 종합하여 A(매우 높음), B(높음), C(보통/관찰)로 평가하세요.
2. 근거 신뢰도 (evidence_confidence): 추천된 사유가 제공된 실제 데이터(GIS, 조례 등)에 얼마나 강하게 뒷받침되는지 평가하여 high, medium, low로 구분하세요. 
  - (주의: 중요도와 신뢰도는 분리해서 판단. 예: 청소년 이용자(중요도 A)이지만 현재 구체적 데이터가 부족하면 신뢰도는 medium/low가 될 수 있음)

[임무]
평가를 바탕으로 최종적으로 논의에 참여해야 할 핵심 추천 이해관계자 후보를 5~8개 선정하여 반환하세요.

[출력 형식]
반드시 아래 JSON 배열의 스키마를 준수하되, 템플릿의 설명문을 그대로 복사하지 말고 **평가 결과로 도출된 실제 데이터 값**을 채워넣어 출력하세요. 다른 텍스트는 출력하지 마세요.
[
    {{
        "display_name": "실제 이해관계자 그룹 이름",
        "stakeholder_type": "분류 태그 (resident, merchant, admin, expert 등)",
        "relationship_to_topic": "주제와의 연관성 및 구체적인 영향",
        "recommendation_reason": "이 그룹이 논의에 반드시 참여해야 하는 이유",
        "importance_grade": "A, B, C 중 하나",
        "evidence_confidence": "high, medium, low 중 하나",
        "keywords": ["핵심", "키워드", "3개"]
    }}
]
"""

