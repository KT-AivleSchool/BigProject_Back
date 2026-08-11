import os
import glob
from typing import Dict, Any, List


def extract_spatial_context(
    top_n_path: str,
    clean_gpkg_dir: str,
    radius_m: float = 200.0,
    facility_labels: Dict[str, str] | None = None,
) -> Dict[str, str]:
    """
    지어진 topN.geojson 파일과 clean_*.gpkg 파일들을 바탕으로
    각 후보지 반경(radius_m) 이내의 인프라/시설물 개수 요약 정보를 생성합니다.

    🔴 2026-08-11. `FACILITY_MAP` 상수를 지웠다. `{"_clean_01": "공공기관",
       "_clean_06": "노인복지시설", "_clean_07": "학교", "_clean_11": "대형마트", …}`
       였는데 **흡연 도메인 실측과 8개 중 4개가 다르다**(01 금연구역 · 06 지하철역 ·
       07 버스정류소 · 11 어린이보호구역. 맞은 건 05 어린이집 하나뿐이다).

       고쳐 적는 것으로는 안 끝난다 — `dataset_id`(`_clean_NN`) 번호는 **도메인마다
       다르게 매겨진다**(업로드 API 가 파일명 가나다순으로 부여). 즉 어떤 고정 사전도
       다음 도메인에서 틀린다(원칙 2). 그래서 이름표는 **주입**받고, 안 주면 파일명을
       그대로 쓴다 — 「모르는 것을 그럴듯한 이름으로 바꾸지 않는다」(원칙 4·5).
       도메인별 이름표의 출처는 그 실행의 `audit_result_reviewed.json` 이다.

    ⚠ 이 모듈은 아직 API 경로에 안 물려 있다(호출자: `tests/test_spatial_persona.py`).
    """
    facility_labels = facility_labels or {}
    try:
        import geopandas as gpd
    except ImportError:
        return {} # geopandas가 없으면 빈 컨텍스트 반환

    if not os.path.exists(top_n_path):
        return {}

    # 후보지 로드 및 좌표계 변환 (미터 기반 거리 연산을 위해 EPSG:5186 사용)
    top_n = gpd.read_file(top_n_path)
    if top_n.crs and top_n.crs.to_epsg() != 5186:
        top_n = top_n.to_crs(epsg=5186)
    
    # 200m 반경 버퍼 생성
    top_n['buffer'] = top_n.geometry.buffer(radius_m)

    # gpkg 파일 목록 조회
    gpkg_files = glob.glob(os.path.join(clean_gpkg_dir, "*clean_*.gpkg"))
    
    # 각 후보지별 시설물 카운트 딕셔너리 초기화
    results: Dict[str, Dict[str, int]] = {}
    for idx, row in top_n.iterrows():
        # 후보지 ID가 있다면 사용, 없으면 인덱스
        candidate_id = str(row.get('id', row.get('candidate_id', f"SITE-{idx+1}")))
        results[candidate_id] = {}

    for gpkg_path in gpkg_files:
        filename = os.path.basename(gpkg_path)
        base_name = filename.split('.')[0]
        facility_type = base_name
        for k, v in facility_labels.items():
            if k in base_name:
                facility_type = v
                break
        
        try:
            facility_df = gpd.read_file(gpkg_path)
            if facility_df.empty:
                continue
            
            if facility_df.crs and facility_df.crs.to_epsg() != 5186:
                facility_df = facility_df.to_crs(epsg=5186)

            # 각 후보지별로 sjoin (공간 조인)
            for idx, row in top_n.iterrows():
                candidate_id = str(row.get('id', row.get('candidate_id', f"SITE-{idx+1}")))
                # 현재 후보지의 버퍼와 겹치는 시설물 개수
                buffer_gdf = gpd.GeoDataFrame(geometry=[row['buffer']], crs=top_n.crs)
                intersecting = gpd.sjoin(facility_df, buffer_gdf, how='inner', predicate='intersects')
                count = len(intersecting)
                if count > 0:
                    results[candidate_id][facility_type] = count
        except Exception as e:
            print(f"Error processing {gpkg_path}: {e}")

    # 문자열로 요약
    final_context: Dict[str, str] = {}
    for cid, counts in results.items():
        if not counts:
            final_context[cid] = f"반경 {radius_m}m 이내 확인된 특별한 관련 시설물 없음."
        else:
            summary = ", ".join([f"{ftype} {cnt}곳" for ftype, cnt in counts.items()])
            final_context[cid] = f"반경 {radius_m}m 이내 시설물: {summary}"
            
    return final_context
