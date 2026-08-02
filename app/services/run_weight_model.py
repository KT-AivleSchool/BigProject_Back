# -*- coding: utf-8 -*-
"""
가중치 모델 실행 (흡연 도메인)
==============================
  python run_weight_model.py 흡연 --candidates 국유부동산_위경도_v2.csv

흐름: 로드 -> [A]지표정의 -> [A2]레이어부착 -> [R]반경제안(mini+HITL) ->
      [B]행렬 -> 희소판정 -> 정규화 -> [D]CRITIC+부트스트랩 -> [C]human -> [E]합성 -> [F]저장

거리 감쇠
  --decay gaussian     반경 안이면 1(기존) 대신 exp(-d²/2σ²) 로 거리 가중
  --sigma-ratio 0.333  σ = R * ratio (기본 1/3)

반경 고정
  --radius "07+02=150,06+03=300,08=50,09=50,10=250"
    mini 제안이 실행마다 흔들리므로(temperature=0 인데도), 비교 실험에서는
    R 을 고정해야 감쇠 효과만 분리된다. 지정한 지표는 HITL 을 건너뛴다.
"""
import os, sys, json, re, argparse, time
_T_START = time.perf_counter()
_T_IMPORT = time.perf_counter()
import numpy as np, pandas as pd, geopandas as gpd

# 프로젝트 루트를 sys.path 에 추가 → `app.xxx` 절대 임포트가 되게.
#   이 파일: BigProject_Back/app/services/run_weight_model.py
#   루트   : parent.parent.parent
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.services import gam2_weight_model as W
IMPORT_SEC = time.perf_counter() - _T_IMPORT
from app.config import (STEP1_OUTPUT_DIR, STEP2_OUTPUT_DIR, STEP3_OUTPUT_DIR,
                        ADM_DONG_SHP, REGION_DATA_DIR, domain_prefix)


def _resolve_candidates(path: str, domain: str = "") -> str:
    """후보 파일 경로 해석.

    탐색 순서: 직접 경로 → STEP3_OUTPUT_DIR(프리픽스 붙인 이름) →
               STEP3_OUTPUT_DIR(원래 이름) → REGION_DATA_DIR(구버전 호환)

    후보 gpkg 는 make_parcel_candidates.py 산출물이며 **도메인 프리픽스**가 붙는다.
    후보 집합이 지목 판정(시설별)에 의존하므로 도메인마다 달라지기 때문이다.
    """
    base = os.path.basename(path)
    pfx = domain_prefix(domain) if domain else ""
    cands = [path]
    if pfx and not base.startswith(f"{pfx}_"):
        cands.append(os.path.join(STEP3_OUTPUT_DIR, f"{pfx}_{base}"))
    cands += [os.path.join(STEP3_OUTPUT_DIR, base),
              os.path.join(REGION_DATA_DIR, base)]   # 구버전 위치
    for p in cands:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(
        "후보 파일 없음. 다음 경로를 찾았습니다:\n  "
        + "\n  ".join(os.path.abspath(p) for p in cands)
        + f"\n\n  먼저 생성하세요: python app\\services\\make_parcel_candidates.py {domain or '<도메인>'}")


def _parse_radius_arg(s: str) -> dict:
    """'07+02=150,08=50' -> {'07+02':150,'08':50}. 형식 오류는 즉시 중단."""
    out = {}
    if not s:
        return out
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "=" not in tok:
            raise ValueError(f"--radius 형식 오류: '{tok}' (지표ID=미터)")
        k, v = tok.split("=", 1)
        k = k.strip()
        try:
            r = int(v.strip())
        except ValueError:
            raise ValueError(f"--radius 반경은 정수여야 합니다: '{tok}'")
        if not (1 <= r <= 5000):
            raise ValueError(f"--radius 반경은 1~5000m 범위: '{tok}'")
        out[k] = r
    return out


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
        df = pd.read_parquet(f)
        if "경도" in df.columns and "위도" in df.columns:
            df = df.dropna(subset=["경도", "위도"])
            df = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["경도"], df["위도"]), crs="EPSG:4326").to_crs(W.WORK_CRS)
        elif "X좌표" in df.columns and "Y좌표" in df.columns:
            df = df.dropna(subset=["X좌표", "Y좌표"])
            df = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["X좌표"], df["Y좌표"]), crs="EPSG:4326").to_crs(W.WORK_CRS)
        return df
    return loader, doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--candidates", required=True, help="후보지 CSV(경도·위도 포함)")
    ap.add_argument("--reviewed", help="reviewed.json (기본: STEP2 옆)")
    ap.add_argument("--alpha", type=float, default=0.3)
    ap.add_argument("--auto-radius", action="store_true",
                    help="mini 제안값 자동 사용(HITL 생략, 테스트용)")
    ap.add_argument("--bootstrap", type=int, default=200,
                    help="CRITIC 95%% CI 부트스트랩 반복수 (기본 200, 0이면 생략). "
                         "가중치 계산과 무관한 진단이다")
    ap.add_argument("--no-diag", action="store_true",
                    help="표본 대표성·alpha 민감도 진단 생략")
    # --- 거리 감쇠 ---
    ap.add_argument("--decay", choices=["gaussian", "linear"], default=None,
                    help="거리 감쇠. 미지정이면 기존 binary(반경 안=1)")
    ap.add_argument("--sigma-ratio", type=float, default=1/3,
                    help="가우시안 σ = R * ratio (기본 1/3)")
    ap.add_argument("--scale", choices=["minmax", "log"], default="minmax",
                    help="정규화. log 는 롱테일(서울역 등) 지배를 완화")
    # --- 반경 고정 ---
    ap.add_argument("--radius", default=None,
                    help='R 고정. 예: "07+02=150,06+03=300,08=50,09=50,10=250"')
    args = ap.parse_args()

    radius_fix = _parse_radius_arg(args.radius)

    T = W.Timer()
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
    T.lap("감리·정제 결과 로드")

    # [A] 지표 정의
    print("="*70, "\n[A] 지표 정의")
    inds = W.define_indicators(reviewed, report)
    for i in inds:
        print(f"  {i['id']:<8} seed={i['seed_weight']} dir={i['direction']} "
              f"geo={i['geo_dataset']} val={i['val_dataset']}")

    T.lap("[A] 지표 정의")

    # [A2] 레이어 부착 (kind 확정)
    print("\n[A2] 레이어 부착")
    W.attach_layers(inds, loader)
    T.lap("[A2] 레이어 부착")

    # [R] 반경 제안 (mini) -> HITL
    print("\n[R] 집계반경 제안 (mini)")
    radius_conf = W.suggest_radius(facility, inds)
    for i in inds:
        rc = radius_conf.get(i["id"], {})
        print(f"  {i['id']:<8} R={rc.get('radius_m')}  {rc.get('rationale','')}")

    # --radius 로 지정된 지표는 제안값을 덮어쓰고 HITL 에서 제외
    if radius_fix:
        unknown = set(radius_fix) - {i["id"] for i in inds}
        if unknown:
            raise ValueError(f"--radius 에 없는 지표ID: {sorted(unknown)}\n"
                             f"  사용 가능: {[i['id'] for i in inds]}")
        print("\n  [고정] --radius 로 지정된 반경 (HITL 생략)")
        for k, v in radius_fix.items():
            old = radius_conf.get(k, {}).get("radius_m")
            radius_conf.setdefault(k, {})["radius_m"] = v
            radius_conf[k]["source"] = "cli_fixed"
            print(f"     [{k}] {old} -> {v}m")

    if not args.auto_radius:
        todo = [i for i in inds if i["kind"] != "admin" and i["id"] not in radius_fix]
        if todo:
            print("\n  >> HITL: 위 반경을 확인/수정하세요. 엔터=승인, 숫자입력=수정")
            for i in todo:
                cur = radius_conf[i["id"]]["radius_m"]
                while True:                   # 잘못된 입력에 파이프라인이 죽지 않게 재입력
                    v = input(f"     [{i['id']}] R({cur}m)= ").strip()
                    if not v:                 # 엔터 = 제안값 승인
                        break
                    try:
                        r = int(v)
                    except ValueError:
                        print(f"        숫자만 입력하세요 (엔터=승인). 입력값: {v[:40]}")
                        continue
                    if not (1 <= r <= 5000):
                        print("        1~5000m 범위로 입력하세요.")
                        continue
                    radius_conf[i["id"]]["radius_m"] = r
                    break
        radius_conf["_confirmed"] = True
    radius_m = {k: v.get("radius_m") for k, v in radius_conf.items() if not k.startswith("_")}
    T.lap("[R] 반경 제안(LLM)+HITL")

    # 후보 로드 — .gpkg/.geojson 은 geometry 그대로, .csv 는 경위도에서 생성
    cand_path = _resolve_candidates(args.candidates, args.domain)
    if cand_path.lower().endswith((".gpkg", ".geojson", ".shp")):
        # gpkg 는 candidates(Point)/parcels(Polygon) 2개 레이어다.
        # STEP3 는 필지당 1점이어야 하므로 candidates 를 명시한다.
        _lyr = "candidates" if cand_path.lower().endswith(".gpkg") else None
        cand = (gpd.read_file(cand_path, layer=_lyr) if _lyr
                else gpd.read_file(cand_path)).to_crs(W.WORK_CRS)
        c = pd.DataFrame(cand.drop(columns="geometry"))
        src_kind = "geometry"
    else:
        c = pd.read_csv(cand_path, encoding="utf-8")
        lon = next(col for col in c.columns if "경도" in col or col.lower() == "lon")
        lat = next(col for col in c.columns if "위도" in col or col.lower() == "lat")
        cand = gpd.GeoDataFrame(c, geometry=gpd.points_from_xy(c[lon], c[lat]),
                                crs=4326).to_crs(W.WORK_CRS)
        src_kind = "경위도(4326->%d)" % W.WORK_CRS
    print(f"\n[후보] {len(cand):,}개 (EPSG:{W.WORK_CRS}, {src_kind})"
          f"  {os.path.basename(cand_path)}")
    T.lap("후보 로드")

    # 행정동 경계 (admin 지표용)
    admin_gdf = None
    if any(i["kind"] == "admin" for i in inds):
        if not ADM_DONG_SHP or not os.path.exists(ADM_DONG_SHP):
            print("  ⚠ ADM_DONG_SHP 없음 — admin 지표 계산 불가. config 확인 필요.")
        else:
            admin_gdf = gpd.read_file(ADM_DONG_SHP).to_crs(W.WORK_CRS)

    # [B] 행렬
    if args.decay:
        print(f"\n[B] 지표 행렬  (감쇠={args.decay}, σ=R×{args.sigma_ratio:.3f})")
    else:
        print("\n[B] 지표 행렬  (감쇠 없음 — 반경 안=1)")
    mat = W.build_matrix(cand, inds, radius_m, admin_gdf=admin_gdf,
                         decay=args.decay, sigma_ratio=args.sigma_ratio)

    T.lap("[B] 지표 행렬")

    # 희소 판정 (방향 인지 — cost 지표는 양끝 모두 희소)
    sparse = W.detect_sparse(mat, inds)
    print("\n[희소성] 비영 비율")
    for c_ in mat.columns:
        ratio = (mat[c_] > 0).mean()
        print(f"  {c_:<8} {ratio*100:5.1f}%" + ("  <- 희소(CRITIC 제외)" if c_ in sparse else ""))

    # 정규화 -> CRITIC -> human -> 합성
    norm = W.normalize_matrix(mat, inds, scale=args.scale)
    T.lap("정규화·희소판정")
    w_h = W.human_weights(inds)
    w_c = W.critic_weights(norm, sparse_ids=sparse)
    T.lap("[D] CRITIC")
    boot = ({} if args.bootstrap <= 0
            else W.critic_bootstrap(norm, sparse_ids=sparse, B=args.bootstrap))
    T.lap(f"[D] 부트스트랩 B={args.bootstrap}")
    w_f = W.synthesize(w_h, w_c, alpha=args.alpha, sparse_ids=sparse)

    # [진단] 표본 대표성 · alpha 민감도  (--no-diag 로 생략 가능)
    if not args.no_diag:
        # 후보의 계층(법정동) — 주소에서 추출. 없으면 층화 검사는 생략된다.
        strata = None
        if "법정동코드" in c.columns:              # 지적도 후보(gpkg)
            strata = c["법정동코드"].astype(str)
        else:                                       # 국유부동산 CSV — 주소에서 추출
            addr_col = next((col for col in c.columns if "소재지" in str(col)), None)
            if addr_col:
                strata = c[addr_col].astype(str).str.extract(r"구\s+(\S+?)\s")[0]
                if strata.isna().mean() > 0.5:
                    strata = None
        W.diagnose_sample_bias(mat, inds, sparse, strata=strata)
        W.diagnose_alpha(w_h, w_c, sparse)
        T.lap("[진단] 표본·alpha")

    print("\n" + "="*70)
    print(f"[가중치] alpha={args.alpha}  (사람 {1-args.alpha:.0%} / 데이터 {args.alpha:.0%})"
          + (f"  감쇠={args.decay}" if args.decay else "  감쇠=없음")
          + f"  정규화={args.scale}")
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
    # 재현성 메타 — 감쇠 설정을 산출물에 남긴다(같은 결과를 다시 못 만드는 일 방지)
    ws["decay"] = {"func": args.decay, "sigma_ratio": args.sigma_ratio if args.decay else None}
    ws["scale"] = args.scale        # 재현성 — 어떤 정규화로 뽑은 가중치인지
    path = W.save_weight_set(ws, args.domain)
    print(f"\n[F] weight_set 저장: {path}")
    T.lap("[F] 저장")
    T.report(import_sec=IMPORT_SEC, start=_T_START)


if __name__ == "__main__":
    main()
