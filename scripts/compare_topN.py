import os
import argparse
import pandas as pd
import math

def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
    R = 6371000  # radius of Earth in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def find_csv(path):
    if os.path.isfile(path):
        return path
    elif os.path.isdir(path):
        for f in os.listdir(path):
            if f.endswith("_topN_min.csv"):
                return os.path.join(path, f)
    raise FileNotFoundError(f"Cannot find *_topN_min.csv in {path}")

def main():
    parser = argparse.ArgumentParser(description="Compare Top-N site selection results before and after adding private property penalty.")
    parser.add_argument("--before", required=True, help="Path to 'before' CSV or directory")
    parser.add_argument("--after", required=True, help="Path to 'after' CSV or directory")
    parser.add_argument("--top", type=int, default=20, help="Number of Top-N sites to consider (default: 20)")
    parser.add_argument("--candidates", default="data_임시/step3_output/재활용_후보_지적도필지.gpkg", help="Path to candidates GPKG to fetch ownership info (국유_지분율)")
    args = parser.parse_args()

    before_csv = find_csv(args.before)
    after_csv = find_csv(args.after)

    df_b = pd.read_csv(before_csv)
    df_a = pd.read_csv(after_csv)
    df_b["점수"] = pd.to_numeric(df_b["점수"], errors="coerce")
    df_a["점수"] = pd.to_numeric(df_a["점수"], errors="coerce")

    # Make sure we sort by 순위 just in case
    if "순위" in df_b.columns:
        df_b = df_b.sort_values("순위").reset_index(drop=True)
        df_a = df_a.sort_values("순위").reset_index(drop=True)

    # Take Top-N
    top_n = args.top
    df_b_top = df_b.head(top_n)
    df_a_top = df_a.head(top_n)

    # 1. 공통 후보지 유지율
    pnu_b = set(df_b_top["PNU"].astype(str))
    pnu_a = set(df_a_top["PNU"].astype(str))
    common_pnus = pnu_b.intersection(pnu_a)
    retention_rate = len(common_pnus) / top_n * 100

    # 2. 평균 점수 하락 폭 (공통 후보지 기준)
    b_common = df_b_top[df_b_top["PNU"].astype(str).isin(common_pnus)].set_index("PNU")
    a_common = df_a_top[df_a_top["PNU"].astype(str).isin(common_pnus)].set_index("PNU")
    
    if len(common_pnus) > 0:
        score_diffs = pd.to_numeric(a_common["점수"], errors='coerce') - pd.to_numeric(b_common["점수"], errors='coerce')
        avg_score_diff = score_diffs.dropna().mean()
    else:
        avg_score_diff = 0.0

    # 3. 신규 진입 후보지
    new_pnus = pnu_a - pnu_b
    new_sites = df_a_top[df_a_top["PNU"].astype(str).isin(new_pnus)]

    # 4. 공간적 이동 (1순위 기준)
    rank1_b = df_b_top.iloc[0]
    rank1_a = df_a_top.iloc[0]
    
    dist_shift = haversine(rank1_b["위도"], rank1_b["경도"], rank1_a["위도"], rank1_a["경도"])

    # 국공유지 비율 계산
    try:
        import geopandas as gpd
        import warnings
        warnings.filterwarnings("ignore")
        if os.path.exists(args.candidates):
            gdf_cand = gpd.read_file(args.candidates, layer="parcels")
            if "국유_지분율" in gdf_cand.columns:
                pnu_to_public = dict(zip(gdf_cand["PNU"].astype(str), gdf_cand["국유_지분율"].fillna(0) > 0))
            else:
                pnu_to_public = {}
        else:
            pnu_to_public = {}
    except Exception as e:
        print(f"DEBUG Error: {e}")
        pnu_to_public = {}

    def get_public_stats(df):
        if not pnu_to_public: return 0, 0.0
        cnt = sum(1 for p in df["PNU"].astype(str) if pnu_to_public.get(p, False))
        return cnt, (cnt / len(df) * 100) if len(df) > 0 else 0.0

    b_pub_cnt, b_pub_ratio = get_public_stats(df_b_top)
    a_pub_cnt, a_pub_ratio = get_public_stats(df_a_top)

    # === 리포트 출력 ===
    print("="*60)
    print(" 📊 사유지 데이터 반영 전후 Top-N 분석 리포트")
    print("="*60)
    print(f"✅ 분석 대상: Top {top_n} 후보지")
    print(f"   - Before: {os.path.basename(before_csv)}")
    print(f"   - After : {os.path.basename(after_csv)}")
    if pnu_to_public:
        print(f"   - 국공유지 비율: {b_pub_ratio:.1f}% ({b_pub_cnt}/{top_n}) ➡️  {a_pub_ratio:.1f}% ({a_pub_cnt}/{top_n})")
    else:
        print("   - 국공유지 비율: 계산 불가 (후보지 데이터에 '국유_지분율' 컬럼 없음)")
    print("-" * 60)
    
    print(f"📌 1. 공통 후보지 유지율: {len(common_pnus)}/{top_n} ({retention_rate:.1f}%)")
    print(f"📌 2. 기존 후보지 평균 점수 증감: {avg_score_diff:+.4f}")
    
    print(f"📌 3. 1순위 후보지 공간적 이동 거리: {dist_shift:,.1f} 미터(m)")
    if dist_shift == 0:
        print("   -> 1순위 후보지가 그대로 유지되었습니다.")
    else:
        print(f"   -> 기존 1위: {rank1_b['JIBUN']} (점수: {rank1_b['점수']:.4f})")
        print(f"   -> 신규 1위: {rank1_a['JIBUN']} (점수: {rank1_a['점수']:.4f})")

    print("-" * 60)
    print(f"🌟 4. 신규 진입 명당 후보지 (총 {len(new_sites)}곳)")
    if len(new_sites) > 0:
        for idx, row in new_sites.iterrows():
            print(f"   - [순위 {row['순위']}위] {row['JIBUN']} (점수: {row['점수']:.4f})")
    else:
        print("   - 신규 진입 후보지가 없습니다.")
    print("="*60)

if __name__ == "__main__":
    main()
