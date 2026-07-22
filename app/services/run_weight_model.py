# -*- coding: utf-8 -*-
"""
가중치 모델 실행 (흡연 도메인)
==============================
  python run_weight_model.py 흡연 --candidates 국유부동산_위경도_v2.csv

흐름: 로드 -> [A]지표정의 -> [A2]레이어부착 -> [R]반경제안(mini+HITL) ->
      [B]행렬 -> 희소판정 -> 정규화 -> [D]CRITIC+부트스트랩 -> [C]human -> [E]합성 -> [F]저장
"""
import os, sys, json, re, argparse
import numpy as np, pandas as pd, geopandas as gpd

# 프로젝트 루트를 sys.path 에 추가 → `app.xxx` 절대 임포트가 되게.
#   이 파일: BigProject_Back/app/services/run_weight_model.py
#   루트   : parent.parent.parent
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.services import gam2_weight_model as W
from app.config import (STEP1_OUTPUT_DIR, STEP2_OUTPUT_DIR, ADM_DONG_SHP,
                        REGION_DATA_DIR, domain_prefix)


def _resolve_candidates(path: str) -> str:
    """후보 CSV 경로 해석: 직접 경로 → 실행위치 → REGION_DATA_DIR 순."""
    if os.path.isfile(path):
        return path
    alt = os.path.join(REGION_DATA_DIR, os.path.basename(path))
    if os.path.isfile(alt):
        return alt
    raise FileNotFoundError(
        f"후보 CSV 없음: {path}\n"
        f"  다음에서 찾음: {os.path.abspath(path)} / {alt}\n"
        f"  전체 경로로 지정하거나 {REGION_DATA_DIR} 에 두세요.")


def make_loader(domain: str):
    """dataset_id -> GeoDataFrame(5186)|DataFrame. clean_report 로 파일 경로 해석."""
    prefix = domain_prefix(domain)
    rpt = os.path.join(STEP2_OUTPUT_DIR, f"{prefix}_clean_report.json")
    doc = json.load(open(rpt, encoding="utf-8"))
    files = {}
    for r in doc.get("results", []):
        out = r.get("output")
        if out and os.path.isfile(out):
            files[r["dataset_id"]] = out
        elif out:
            alt = os.path.join(STEP2_OUTPUT_DIR, os.path.basename(out))
            if os.path.isfile(alt):
                files[r["dataset_id"]] = alt

    def loader(did):
        f = files[did]
        if f.endswith(".gpkg"):
            return gpd.read_file(f).to_crs(W.WORK_CRS)
        return pd.read_parquet(f)
    return loader, doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--candidates", required=True, help="후보지 CSV(경도·위도 포함)")
    ap.add_argument("--reviewed", help="reviewed.json (기본: STEP2 옆)")
    ap.add_argument("--alpha", type=float, default=0.3)
    ap.add_argument("--auto-radius", action="store_true",
                    help="mini 제안값 자동 사용(HITL 생략, 테스트용)")
    args = ap.parse_args()

    loader, report = make_loader(args.domain)
    reviewed_path = args.reviewed or os.path.join(
        STEP1_OUTPUT_DIR, f"{domain_prefix(args.domain)}_audit_result_reviewed.json")
    if not os.path.isfile(reviewed_path):
        raise FileNotFoundError(
            f"감리 결과(reviewed) 없음: {reviewed_path}\n"
            f"  --reviewed <경로> 로 직접 지정하거나, STEP1_OUTPUT_DIR 를 확인하세요.")
    reviewed = json.load(open(reviewed_path, encoding="utf-8"))
    facility = reviewed.get("facility_inference", {}).get("facility", args.domain)
    region = report.get("region", "")

    # [A] 지표 정의
    print("="*70, "\n[A] 지표 정의")
    inds = W.define_indicators(reviewed, report)
    for i in inds:
        print(f"  {i['id']:<8} seed={i['seed_weight']} dir={i['direction']} "
              f"geo={i['geo_dataset']} val={i['val_dataset']}")

    # [A2] 레이어 부착 (kind 확정)
    print("\n[A2] 레이어 부착")
    W.attach_layers(inds, loader)

    # [R] 반경 제안 (mini) -> HITL
    print("\n[R] 집계반경 제안 (mini)")
    radius_conf = W.suggest_radius(facility, inds)
    for i in inds:
        rc = radius_conf.get(i["id"], {})
        print(f"  {i['id']:<8} R={rc.get('radius_m')}  {rc.get('rationale','')}")
    if not args.auto_radius:
        print("\n  >> HITL: 위 반경을 확인/수정하세요. 엔터=승인, 숫자입력=수정")
        for i in inds:
            if i["kind"] == "admin":
                continue
            cur = radius_conf[i["id"]]["radius_m"]
            v = input(f"     [{i['id']}] R({cur}m)= ").strip()
            if v:
                radius_conf[i["id"]]["radius_m"] = int(v)
        radius_conf["_confirmed"] = True
    radius_m = {k: v.get("radius_m") for k, v in radius_conf.items() if not k.startswith("_")}

    # 후보 로드
    cand_path = _resolve_candidates(args.candidates)
    c = pd.read_csv(cand_path, encoding="utf-8")
    lon = next(col for col in c.columns if "경도" in col or col.lower() == "lon")
    lat = next(col for col in c.columns if "위도" in col or col.lower() == "lat")
    cand = gpd.GeoDataFrame(c, geometry=gpd.points_from_xy(c[lon], c[lat]),
                            crs=4326).to_crs(W.WORK_CRS)
    print(f"\n[후보] {len(cand)}개 (EPSG:{W.WORK_CRS})")

    # 행정동 경계 (admin 지표용)
    admin_gdf = None
    if any(i["kind"] == "admin" for i in inds):
        if not ADM_DONG_SHP or not os.path.exists(ADM_DONG_SHP):
            print("  ⚠ ADM_DONG_SHP 없음 — admin 지표 계산 불가. config 확인 필요.")
        else:
            admin_gdf = gpd.read_file(ADM_DONG_SHP).to_crs(W.WORK_CRS)

    # [B] 행렬
    print("\n[B] 지표 행렬")
    mat = W.build_matrix(cand, inds, radius_m, admin_gdf=admin_gdf)

    # 희소 판정
    sparse = W.detect_sparse(mat)
    print("\n[희소성] 비영 비율")
    for c_ in mat.columns:
        ratio = (mat[c_] > 0).mean()
        print(f"  {c_:<8} {ratio*100:5.1f}%" + ("  <- 희소(CRITIC 제외)" if c_ in sparse else ""))

    # 정규화 -> CRITIC -> human -> 합성
    norm = W.normalize_matrix(mat, inds)
    w_h = W.human_weights(inds)
    w_c = W.critic_weights(norm, sparse_ids=sparse)
    boot = W.critic_bootstrap(norm, sparse_ids=sparse, B=1000)
    w_f = W.synthesize(w_h, w_c, alpha=args.alpha, sparse_ids=sparse)

    print("\n" + "="*70)
    print(f"[가중치] alpha={args.alpha}  (사람 {1-args.alpha:.0%} / 데이터 {args.alpha:.0%})")
    print("-"*70)
    print(f"{'지표':<10}{'w_human':>9}{'w_critic':>9}{'w_final':>9}   95% CI")
    for i in inds:
        iid = i["id"]; ci = boot.get(iid)
        cis = f"[{ci['ci_low']:.3f},{ci['ci_high']:.3f}]" if ci else "(희소)"
        wc = f"{w_c.get(iid,0):.3f}" if iid not in sparse else "  -  "
        print(f"{iid:<10}{w_h[iid]:>9.3f}{wc:>9}{w_f[iid]:>9.3f}   {cis}")
    print("-"*70)

    # [F] 저장
    ws = W.build_weight_set(args.domain, facility, region, inds, radius_conf,
                            args.alpha, w_h, w_c, w_f, boot, sparse, len(cand))
    path = W.save_weight_set(ws, args.domain)
    print(f"\n[F] weight_set 저장: {path}")


if __name__ == "__main__":
    main()
