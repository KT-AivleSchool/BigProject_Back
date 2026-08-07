import os
import glob
import asyncio
from app.core.stakeholder_mode.services.spatial_context import extract_spatial_context
from app.core.stakeholder_mode.schemas.stakeholder import CandidateSite, OrdinanceContext
from app.core.stakeholder_mode.graph.builder import stakeholder_graph
import json

async def main():
    # 1. 파일 경로 탐색
    base_dir = os.path.dirname(os.path.abspath(__file__))
    shared_data_dir = os.path.join(base_dir, "shared_data")
    
    topN_files = glob.glob(os.path.join(shared_data_dir, "*", "*", "*topN.geojson"))
    if not topN_files:
        print("topN.geojson 파일을 찾을 수 없습니다.")
        return
    topN_path = topN_files[0]
    
    gpkg_files = glob.glob(os.path.join(shared_data_dir, "*", "*", "*clean_*.gpkg"))
    if not gpkg_files:
        print("clean_*.gpkg 파일을 찾을 수 없습니다.")
        return
    clean_gpkg_dir = os.path.dirname(gpkg_files[0])
    
    print(f"TopN 경로: {topN_path}")
    print(f"GPKG 디렉터리: {clean_gpkg_dir}")
    
    # 2. 공간 컨텍스트 추출
    print("공간 컨텍스트 추출 중...")
    try:
        spatial_contexts = extract_spatial_context(topN_path, clean_gpkg_dir, radius_m=200.0)
        print(json.dumps(spatial_contexts, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"공간 연산 오류: {e}")
        return

    # 3. 테스트 데이터 구성
    # 실제로는 감리 AI 파이프라인에서 추출된 데이터를 사용합니다.
    # 여기서는 추출된 컨텍스트를 테스트하기 위해 목업을 만듭니다.
    candidate_sites = []
    for cid, s_ctx in spatial_contexts.items():
        candidate_sites.append(CandidateSite(
            candidate_id=cid,
            name=f"후보지 {cid}",
            attributes={"접근성": "양호", "면적": "500sqm"},
            spatial_context=s_ctx
        ))
        
    if not candidate_sites:
        print("추출된 후보지가 없습니다. 테스트 데이터를 임의로 생성합니다.")
        candidate_sites.append(CandidateSite(
            candidate_id="SITE-1",
            name="테스트 후보지",
            attributes={"접근성": "우수"},
            spatial_context="반경 200m 이내 시설물: 어린이집 3곳"
        ))
        
    ordinance_contexts = [
        OrdinanceContext(
            chunk_id="ORD-001",
            ordinance_name="서울특별시 금연환경 조성 및 간접흡연 피해방지 조례",
            content="어린이집 주변 10m 이내에는 흡연 시설을 설치할 수 없다."
        )
    ]
    
    initial_state = {
        "project_id": "TEST-SPATIAL",
        "topic": "신규 흡연부스 설치",
        "candidate_sites": [s.model_dump() for s in candidate_sites],
        "ordinance_contexts": [o.model_dump() for o in ordinance_contexts]
    }
    
    # 4. 페르소나 그래프 실행 (시간이 걸릴 수 있으므로 추천 페르소나까지만 부분 테스트 또는 전체 실행)
    print("\n페르소나 워크플로우 실행 시작...")
    # 전체 실행 시 LLM 호출이 많이 발생하므로 주의.
    # 여기서는 결과만 확인.
    try:
        final_state = await stakeholder_graph.ainvoke(initial_state)
        print("\n최종 집계 결과:")
        print(json.dumps(final_state.get("final_result"), ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"워크플로우 실행 중 오류: {e}")

if __name__ == "__main__":
    asyncio.run(main())
