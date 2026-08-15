# -*- coding: utf-8 -*-
"""
정제 산출물 진단 — 좌표계 판정 · 행정동코드 컬럼 판정 (일회성)
================================================================
  python app/tools/check_loader_health.py 재활용
  python app/tools/check_loader_health.py 흡연

이슈 #193 의 두 버그가 재현되는지 확인한다. LLM 호출 0회, 수 초.

  ① parquet 에 좌표가 남아 있는가 → as_geodataframe 이 CRS 를 맞게 판정하는가
  ② 행정동코드 컬럼을 이름으로 못 찾을 때 → 값 대조로 찾는가

🔴 확인 포인트
  · 'X좌표/Y좌표 투영좌표' 로 중단되면 → 원본 좌표계를 확인해야 한다.
    추측해서 EPSG:4326 으로 읽으면 공간조인이 조용히 0건이 된다.
  · 행정동코드 후보가 0개/2개 이상이면 → 조인 키가 확정되지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore", "GeoSeries.notna", UserWarning)
os.environ.setdefault("SHAPE_RESTORE_SHX", "YES")

import geopandas as gpd
import pandas as pd

# 이 스크립트는 `app/tools/` 안에 있다 — 저장소 루트는 두 단계 위다.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.services import gam2_weight_model as W
from app.config import STEP2_OUTPUT_DIR, domain_prefix


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    args = ap.parse_args()

    pfx = domain_prefix(args.domain)
    rpt = os.path.join(STEP2_OUTPUT_DIR, f"{pfx}_clean_report.json")
    if not os.path.isfile(rpt):
        print(f"[중단] clean_report 없음: {rpt}")
        return
    doc = json.load(open(rpt, encoding="utf-8"))

    files = {}
    for r in doc.get("results", []):
        out = r.get("output")
        if not out:
            continue
        p = (
            out
            if os.path.isfile(out)
            else os.path.join(STEP2_OUTPUT_DIR, os.path.basename(out))
        )
        if os.path.isfile(p):
            files[r["dataset_id"]] = p

    print("=" * 88)
    print(f"[로더 진단] {args.domain}  —  정제 산출물 {len(files)}종")
    print("=" * 88)

    n_geo_restored = n_fail = n_admin_auto = n_admin_fail = 0

    for did in sorted(files):
        f = files[did]
        base = os.path.basename(f)

        # ── gpkg 는 그대로 통과
        if f.endswith(".gpkg"):
            g = gpd.read_file(f)
            gt = ",".join(sorted(set(g.geom_type.dropna())))[:20]
            print(f"\n[{did}] {base}")
            print(
                f"      gpkg  {len(g):>7,}행  geom={gt}  "
                f"CRS={g.crs.to_epsg() if g.crs else '없음'}"
            )
            continue

        # ── parquet: ① 좌표계 판정
        df = pd.read_parquet(f)
        cols = [c for c in df.columns if c != "geometry"]
        print(f"\n[{did}] {base}")
        print(f"      parquet {len(df):>6,}행  컬럼 {len(cols)}개")

        try:
            g = W.as_geodataframe(df, did, verbose=False)
        except ValueError as e:
            n_fail += 1
            print("      🔴 좌표계 판정 중단")
            for ln in str(e).splitlines():
                print(f"         {ln}")
            continue

        if hasattr(g, "geometry"):
            n_geo_restored += 1
            print(f"      ✅ geometry 복원 → {len(g):,}행  CRS={g.crs.to_epsg()}")
            continue

        # ── ② 행정동코드 컬럼 판정 (geometry 없는 통계표만)
        hint = next((c for c in df.columns if "행정동코드" in str(c)), None)
        if hint:
            print(f"      admin  이름 힌트 성공 → '{hint}'")
        else:
            try:
                col, kind, rate, hits = W._detect_admin_key_col(df, did)
                n_admin_auto += 1
                print(
                    f"      ✅ 행정동 조인키 자동판정 → '{col}' ({kind}, 크로스워크 매칭 {rate:.0%})"
                )
                if len(hits) > 1:
                    print(
                        f"         다른 코드형 컬럼: "
                        f"{ {c: f'{v[0]} {v[1]:.0%}' for c, v in hits.items() if c != col} }"
                    )
            except ValueError as e:
                n_admin_fail += 1
                print("      🔴 행정동 조인키 판정 실패")
                for ln in str(e).splitlines():
                    print(f"         {ln}")
                continue

        vcols = W._pick_value_cols(df)
        print(f"      값 컬럼 후보: {vcols if vcols else '없음 🔴'}")

    print("\n" + "=" * 88)
    print(
        f"  geometry 복원 {n_geo_restored}건 · 좌표계 중단 {n_fail}건 · "
        f"행정동코드 자동판정 {n_admin_auto}건 · 판정 실패 {n_admin_fail}건"
    )
    if n_fail:
        print(
            "  🔴 좌표계 중단 — 원본 좌표계를 확인하세요. 추측하면 조인이 조용히 0건이 됩니다."
        )
    if n_admin_fail:
        print("  🔴 행정동코드 판정 실패 — 조인 키가 확정되지 않습니다.")
    if not (n_fail or n_admin_fail):
        print("  ✅ 로더가 모든 산출물을 처리할 수 있습니다.")
    print("=" * 88)


if __name__ == "__main__":
    main()
