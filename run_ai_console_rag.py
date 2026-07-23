# -*- coding: utf-8 -*-
"""실제 RAG 파이프라인으로 AI 페르소나 토론 테스트 — OmniSite 데이터팀

기존 run_ai_console.py / test_multi_docs_persona.py 는 RagVectorStorage 를 MagicMock 으로
죽이고 common_rag 를 수동(csv·파일 앞 15청크)으로 채워서, 실제 벡터 검색·필터·임계치를
전혀 태우지 않았음 → 조례가 토론에 제대로 반영되지 않았음.

이 스크립트는 simulations.py 의 서비스 경로를 그대로 재현한다:
  1. seeds/ 를 적재한 statutes_collection 에서 facility_type 필터로 실제 검색
  2. 검색 결과(조번호 포함 조문)를 common_rag 로 주입
  3. build_discussion_graph() 로 토론 실행

사전 조건:
  · .env 의 DATABASE_URL 이 pgvector DB 를 가리킬 것
  · python ingest_statutes.py 로 조례가 적재돼 있을 것 (없으면 검색 0건)
사용:
  python run_ai_console_rag.py                    # 기본: 흡연부스
  python run_ai_console_rag.py 전기차충전소        # facility_type 지정
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.sim_ai.vector_db import RagVectorStorage  # noqa: E402
from app.core.sim_ai.graph import build_discussion_graph  # noqa: E402

# 후보지·AHP 는 실서비스에서 DB(Parcel)로 오지만, 여기선 테스트 고정값
FACILITY_TYPE = sys.argv[1] if len(sys.argv) > 1 else "흡연부스"
CANDIDATE = {
    "jibun": "서울특별시 용산구 이태원동 123-45",
    "lat": 37.534,
    "lng": 126.994,
    "intensity_level": "높음",
    "ahp_weights": {"보행혼잡도": 0.4, "소음민감도": 0.3, "상권활성화": 0.3},
}


async def main():
    print(
        f"\n{'=' * 60}\n[실제 RAG 파이프라인 AI 페르소나 토론] facility_type={FACILITY_TYPE}\n{'=' * 60}\n"
    )

    # ── 1. 실제 벡터 검색 (simulations.py 와 동일 쿼리·필터) ──
    storage = RagVectorStorage()
    query = "설치 기준 허가 규제 갈등 중재 혜택"
    retrieved = await storage.retrieve_similar_statutes(
        query, top_k=5, facility_type=FACILITY_TYPE
    )
    if retrieved:
        common_rag = "\n".join(retrieved)
        print(
            f"✅ 조례 검색 {len(retrieved)}건 (filter={FACILITY_TYPE}, 임계치 통과분):\n"
        )
        for d in retrieved:
            print("   •", d.split("\n")[0])
    else:
        common_rag = "관련 조례 없음"
        print(
            "⚠️ 검색 0건 — 적재 여부(python ingest_statutes.py)와 facility_type 값을 확인하세요."
        )
    print(f"\n{'-' * 60}\n")

    # ── 2. 토론 상태 구성 ──
    import random

    initial_state = {
        "messages": [],
        "css_pro": random.choice(["LOW", "MEDIUM", "HIGH"]),
        "css_con": random.choice(["LOW", "MEDIUM", "HIGH"]),
        "round_count": 0,
        "current_phase": "debate",
        "eval_score": 0.0,
        "spoken_this_round": [],
        "candidate_jibun": CANDIDATE["jibun"],
        "candidate_lat": CANDIDATE["lat"],
        "candidate_lng": CANDIDATE["lng"],
        "facility_type": FACILITY_TYPE,
        "intensity_level": CANDIDATE["intensity_level"],
        "ahp_weights": CANDIDATE["ahp_weights"],
        "timestamp": "2026-07-23T10:00:00",
        "common_rag": common_rag,
        "audit_context": "프론트엔드 감리 데이터 없음",
        "evaluations": {},
        "final_scenarios": {},
        "is_finished": False,
        "next_speaker": "pro",
    }

    # ── 3. 토론 실행 ──
    graph = build_discussion_graph()
    async for output in graph.astream(initial_state):
        for _, node_state in output.items():
            if node_state.get("messages"):
                print(node_state["messages"][-1])
                print("-" * 50)


if __name__ == "__main__":
    asyncio.run(main())
