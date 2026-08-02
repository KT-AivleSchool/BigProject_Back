import sys
import os
import time
import pandas as pd
import numpy as np
import geopandas as gpd

# 경로 설정
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.gam4_site_select import (
    check_consistency, load_exclusions,
    build_demand_grid, select_mclp, analyze_coverage,
    _read_parcels, DISPLAY_CRS, STEP3_OUTPUT_DIR, REGION_DATA_DIR, STEP1_OUTPUT_DIR,
    make_loader, load_weight_set, domain_prefix, score_candidates
)
from app.services import gam4_spatial_ops as S
from app.services import gam2_weight_model as W
from app.services import gam4_facility_params as FP
import json

def run_experiment(domain="흡연", topn=20, spacing=5, demand_spacing=10, 
                  r_cover=200, d_min=150, curve_n=2000, 
                  cpath=None, bonus_list=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0]):
    prefix = domain_prefix(domain)
    loader, report = make_loader(domain)
    reviewed = json.load(open(os.path.join(
        STEP1_OUTPUT_DIR, f"{prefix}_audit_result_reviewed.json"), encoding="utf-8"))
    ws = load_weight_set(domain)
    
    facility = (reviewed.get("facility_inference") or {}).get("facility", domain)
    fp = {k: v[2] for k, v in FP.PARAM_SPEC.items()}
    try:
        rec = FP.judge(facility)
        fp.update(rec.get("params", {}))
    except Exception:
        pass
        
    min_width = fp["설치_소요_폭_m"]
    
    parcels = _read_parcels(cpath, None)
    pts = S.points_in_parcels(parcels, spacing=spacing, max_per_parcel=0)
    
    for c in ("PNU", "JIBUN", "지목", "면적", "내접폭", "법정동코드",
              "국유_건수", "국유_지분면적", "국유_지분율", "국유_지번일치"):
        if c in parcels.columns:
            pts[c] = parcels[c].to_numpy()[pts["parcel_idx"].to_numpy()]
            
    inds = W.define_indicators(reviewed, report)
    W.attach_layers(inds, loader)
    check_consistency(ws, inds)
    
    from app.config import ADM_DONG_SHP
    admin_gdf = None
    if any(i["kind"] == "admin" for i in inds):
        if ADM_DONG_SHP and os.path.exists(ADM_DONG_SHP):
            admin_gdf = gpd.read_file(ADM_DONG_SHP).to_crs(W.WORK_CRS)
            
    score_base, norm, mat = score_candidates(pts, inds, ws, admin_gdf=admin_gdf)
    union, excl_layers, excl_rows = load_exclusions(reviewed, loader)
    keep_base = S.filter_outside(pts, union)
    
    if min_width is not None and "내접폭" in pts.columns:
        keep_base = keep_base & (pts["내접폭"].to_numpy() >= min_width)
        
    dgrid = build_demand_grid(cpath, parcels, demand_spacing)
    dscore_base, _, _ = score_candidates(dgrid, inds, ws, admin_gdf=admin_gdf, diag=False)
    
    # 국공유지 마스크 (국유_건수가 1 이상인 필지)
    if "국유_건수" in pts.columns:
        is_public = (pts["국유_건수"] > 0).to_numpy()
    else:
        is_public = np.zeros(len(pts), dtype=bool)
        
    public_survived = (is_public & keep_base).sum()
    print(f"\n[진단] 배제 필터 통과한 유효 국공유지 수: {public_survived}개")
    
    
    results = []
    for b in bonus_list:
        print(f"\n=== 실험: 국공유지 가산점 +{b:.1f} ===")
        score = score_base.copy()
        
        # 가산점 적용
        if b > 0:
            score[is_public] += b
            
        dscore = dscore_base.copy()
        
        t0 = time.time()
        sel = select_mclp(pts, score, keep_base, dgrid, dscore, n=topn,
                          r_cover=r_cover, d_min=d_min, curve_n=curve_n, pool=2000)
        print(f"  MCLP 연산 완료 ({time.time()-t0:.1f}초)")
                          
        n_public = 0
        if "국유_건수" in sel.columns:
            n_public = int((sel.head(topn)["국유_건수"] > 0).sum())
        
        scores = sel.head(topn)["점수"].to_numpy()
        min_s, max_s = scores.min(), scores.max()
        
        results.append({
            "가산점": f"+{b:.1f}",
            "Top20_내_국공유지_수": f"{n_public}개",
            "최소점수": f"{min_s:.4f}",
            "최대점수": f"{max_s:.4f}"
        })
        print(f"  -> Top-20 중 국공유지: {n_public}개 (Score: {min_s:.4f} ~ {max_s:.4f})")
        
    res_df = pd.DataFrame(results)
    print("\n[최종 실험 결과 요약표]")
    print(res_df.to_markdown(index=False))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", default="흡연")
    args = parser.parse_args()
    
    # 기본 용산구 테스트용 후보 파일 경로 추정
    cpath = os.path.join(STEP3_OUTPUT_DIR, f"{args.domain}_후보_지적도필지.gpkg")
    if not os.path.exists(cpath):
        cpath = os.path.join(REGION_DATA_DIR, "LSMD_CONT_LDREG_11170_202607.shp")
        
    run_experiment(
        domain=args.domain,
        cpath=cpath,
        bonus_list=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    )
