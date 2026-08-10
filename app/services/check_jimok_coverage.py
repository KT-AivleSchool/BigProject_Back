# -*- coding: utf-8 -*-
"""
지목별 필지가 기존 배제 union 에 이미 포함되는지 확인 (일회성 진단)
====================================================================
  python check_jimok_coverage.py 흡연 --jimok 학
  python check_jimok_coverage.py 흡연 --jimok 학 공 천        (여러 개)

배제 레이어·반경은 전부 reviewed.json 의 hard_exclusion 에서 읽는다(하드코딩 없음).
polygon 타입도 배제반경_m 만큼 버퍼 확장(턴9 Q-poly 확정).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

os.environ.setdefault("SHAPE_RESTORE_SHX", "YES")

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from app.config import (STEP1_OUTPUT_DIR, STEP2_OUTPUT_DIR,
                            REGION_DATA_DIR, domain_prefix)
    WORK_CRS = 5186
except ImportError:                      # 프로젝트 밖에서 단독 실행할 때
    STEP1_OUTPUT_DIR = STEP2_OUTPUT_DIR = REGION_DATA_DIR = "."
    WORK_CRS = 5186
    def domain_prefix(d): return d


def load_layer_paths(prefix: str) -> dict:
    """clean_report.json → {dataset_id: 정제파일 경로}"""
    rpt = os.path.join(STEP2_OUTPUT_DIR, f"{prefix}_clean_report.json")
    doc = json.load(open(rpt, encoding="utf-8"))
    files = {}
    for r in doc.get("results", []):
        out = r.get("output")
        if not out:
            continue
        if os.path.isfile(out):
            files[r["dataset_id"]] = out
        else:                                     # 경로가 바뀐 경우 파일명으로 재탐색
            alt = os.path.join(STEP2_OUTPUT_DIR, os.path.basename(out))
            if os.path.isfile(alt):
                files[r["dataset_id"]] = alt
    return files


def clean_geom(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """POINT EMPTY 는 isna() 로 안 잡힌다 — is_empty 로 같이 거른다."""
    return g[~g.geometry.is_empty & g.geometry.notna()].copy()


def build_exclusion(reviewed: dict, files: dict) -> tuple:
    """reviewed 의 hard_exclusion → 버퍼 union. 반환 (union, 사용내역)"""
    geoms, rows = [], []
    for r in reviewed.get("results", []):
        did = r.get("dataset_id")
        for role in (r.get("roles") or []):
            if role.get("role") != "hard_exclusion":
                continue
            radius = role.get("배제반경_m")
            etype = role.get("exclusion_type", "")
            path = files.get(did)
            if not path:
                rows.append((did, etype, radius, 0, "정제파일 없음"))
                continue
            if not path.endswith(".gpkg"):
                rows.append((did, etype, radius, 0, "geometry 없음(parquet)"))
                continue

            g = clean_geom(gpd.read_file(path).to_crs(WORK_CRS))
            if g.empty:
                rows.append((did, etype, radius, 0, "빈 레이어"))
                continue

            if radius is None:                    # 반경 미확정 → 폴리곤 자체만
                buf = g.geometry
                note = "반경없음(원형상 그대로)"
            else:
                buf = g.geometry.buffer(float(radius))
                note = ""
            geoms.extend(buf.values)
            rows.append((did, etype, radius, len(g), note))

    union = unary_union(geoms) if geoms else None
    return union, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--jimok", nargs="+", default=["학"], help="확인할 지목 (예: 학 공 천)")
    ap.add_argument("--cadastral", default=None, help="연속지적도 SHP 경로")
    ap.add_argument("--reviewed", default=None)
    args = ap.parse_args()

    prefix = domain_prefix(args.domain)

    # ── 1. 배제 union
    rv_path = args.reviewed or os.path.join(
        STEP1_OUTPUT_DIR, f"{prefix}_audit_result_reviewed.json")
    reviewed = json.load(open(rv_path, encoding="utf-8"))
    files = load_layer_paths(prefix)

    print("=" * 68)
    print("[배제 레이어] reviewed.json 의 hard_exclusion")
    print("-" * 68)
    union, rows = build_exclusion(reviewed, files)
    for did, etype, radius, n, note in rows:
        rad = f"{radius}m" if radius is not None else "  - "
        print(f"  {did:<4} {etype:<8} +{rad:<6} {n:>6,} features  {note}")
    if union is None:
        print("\n  배제 레이어 0건 — 확인 불가")
        return
    print("-" * 68)
    print(f"  union 면적 {union.area/1e6:.3f} km²  (용산구 21.87 km² 대비 "
          f"{union.area/1e6/21.87*100:.1f}%)")

    # ── 2. 연속지적도
    cad = args.cadastral
    if not cad:
        import glob
        hits = glob.glob(os.path.join(REGION_DATA_DIR, "LSMD_CONT_LDREG_*.shp"))
        cad = hits[0] if hits else None
    if not cad or not os.path.isfile(cad):
        print(f"\n[중단] 연속지적도 SHP 없음. --cadastral 로 지정하세요.")
        return

    g = gpd.read_file(cad, encoding="cp949")
    if g.crs is None:
        print("  ⚠ 지적도 CRS 없음 — 5186 로 가정")
        g = g.set_crs(WORK_CRS)
    g = clean_geom(g.to_crs(WORK_CRS))
    g["지목"] = g["JIBUN"].astype(str).str.extract(r"([가-힣]+)$")

    # ── 3. 지목별 중복 확인
    print("\n" + "=" * 68)
    print("[지목별 배제 중복 확인]")
    print("=" * 68)
    for jm in args.jimok:
        sub = g[g["지목"] == jm]
        if sub.empty:
            print(f"\n  '{jm}' 필지 0건 — 건너뜀")
            continue

        rep = sub.geometry.representative_point()      # centroid 아님(폴리곤 내부 보장)
        covered = gpd.GeoSeries(rep, crs=WORK_CRS).within(union)
        n_cov, n_all = int(covered.sum()), len(sub)

        print(f"\n  지목 '{jm}' — {n_all:,}개 필지")
        print(f"    이미 배제됨 : {n_cov:,} / {n_all:,}  ({n_cov/n_all*100:.1f}%)")

        miss = sub[~covered.values]
        if miss.empty:
            print(f"    ✅ 전부 기존 배제에 포함 — 별도 배제 레이어 불필요")
            continue

        # 미배제 필지가 배제구역에서 얼마나 떨어져 있나
        d = gpd.GeoSeries(miss.geometry.representative_point(),
                          crs=WORK_CRS).distance(union)
        print(f"    ⚠ 미배제 {len(miss):,}개 — 최근접 배제까지 거리:")
        print(f"        중앙값 {d.median():>7.0f}m   최소 {d.min():>6.0f}m   "
              f"최대 {d.max():>6.0f}m")
        show = miss.assign(_d=d.values).nsmallest(min(10, len(miss)), "_d")
        for _, r in show.iterrows():
            p = r.geometry.representative_point()
            print(f"        {str(r['JIBUN'])[:16]:<16} {r['_d']:>6.0f}m  "
                  f"면적 {r.geometry.area:>8,.0f}m²")

    print("\n" + "=" * 68)
    print("판단 기준: 100% → Q-school 종결 / 90%↑ → Top-N 결과 확인 후 판단")
    print("           그 미만 → 배제 레이어 추가 검토")
    print("=" * 68)


if __name__ == "__main__":
    main()
