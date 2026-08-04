import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db.session import AsyncSessionLocal
from app.services.gis_service import GisService
from app.core.sim_ai.graph import build_discussion_graph
from sqlalchemy import select
from app.db.models.simulation import Parcel

async def main():
    print("=" * 60)
    print("🚀 실시간 POI 기반 토론 테스트 시작")
    print("=" * 60)

    async with AsyncSessionLocal() as db:
        # 1. Parcel DB에서 유효한 parcel_id 가져오기
        result = await db.execute(select(Parcel).limit(1))
        parcel = result.scalar()
        if not parcel:
            print("❌ DB에 Parcel(필지) 데이터가 없습니다. 먼저 기초 데이터를 적재해야 합니다.")
            # DB가 비어있을 경우 테스트를 위한 가상 데이터
            class MockParcel:
                id = 1
                jibun = "서울특별시 용산구 이태원동 123-45"
                lat = 37.534
                lng = 126.994
            parcel = MockParcel()
            print("⚠️ 가상의 Parcel 데이터로 테스트를 진행합니다.")
        
        parcel_id = parcel.id
        print(f"✅ 테스트 대상 Parcel ID: {parcel_id} (지번: {parcel.jibun})")

        # 2. POI 문맥 조회 (gis_service)
        print("\n🔍 DB에서 POI(공간 정보) 조회 중...")
        poi_context = await GisService.get_poi_context_from_db(db, parcel_id)
        if not poi_context or "필지 정보를 찾을 수 없습니다." in poi_context:
            print("⚠️ DB에 연관된 POI가 없어, 테스트용 POI를 강제 주입합니다.")
            poi_context = "🔴 단점: 가장 가까운 새싹어린이집까지 약 150m 거리\n🟢 장점: 반경 300m 이내 공용 쓰레기통 3개 존재\n🔴 단점: 주변 상권(식당) 50m 이내 밀집"
            
        print(f"📍 도출된 POI 문맥:\n{poi_context}")

        audit_context = f"\n\n## 📍 주변 인프라 요인 (DB 연산)\n{poi_context}"

        # 3. 테스트용 AI 토론 상태 구성
        initial_state = {
            "messages": [],
            "css_pro": "HIGH",
            "css_con": "HIGH",
            "round_count": 0,
            "current_phase": "debate",
            "eval_score": 0.0,
            "spoken_this_round": [],
            "candidate_jibun": parcel.jibun,
            "candidate_lat": float(parcel.lat),
            "candidate_lng": float(parcel.lng),
            "facility_type": "흡연부스",
            "intensity_level": "보통",
            "ahp_weights": {"보행혼잡도": 0.4, "소음민감도": 0.3},
            "timestamp": "2026-08-03T10:00:00",
            "common_rag": "테스트 조례: 주거지역 인근 10m 이내 금연구역 지정",
            "rag_docs": [],
            "audit_context": audit_context,
            "evaluations": {},
            "final_scenarios": {},
            "is_finished": False,
            "next_speaker": "pro",
        }

        # 4. 토론 실행
        print("\n" + "=" * 60)
        print("🤖 [AI 토론 엔진 구동]")
        print("=" * 60)
        graph = build_discussion_graph()
        
        async for output in graph.astream(initial_state):
            for node_name, node_state in output.items():
                if node_state.get("messages"):
                    msg = node_state["messages"][-1]
                    print(msg)
                    print("-" * 50)
                
                # 1라운드(찬/반) 발화 후 종료 (결과만 확인)
                if node_name == "con":
                    print("✅ 찬반 1턴(Turn) 확인 완료! POI 정보가 AI 발언에 반영되었는지 확인하세요.")
                    return

if __name__ == "__main__":
    asyncio.run(main())
