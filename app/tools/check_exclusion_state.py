# -*- coding: utf-8 -*-
"""
배제 레이어가 실제로 반영되고 있는지 확인 (LLM 호출 0회)
=========================================================
  python app/tools/check_exclusion_state.py 흡연
  python app/tools/check_exclusion_state.py 흡연 --lift 5 --share 0.05

reviewed.json 의 hard_exclusion 전건에 대해 **기존 계산과 S9 지목배수 계산을 나란히**
보여준다. STEP4 `load_exclusions` 와 같은 모듈(`gam4_exclusion_shape`)을 쓴다 —
진단과 본 계산이 갈리면 진단이 거짓말을 한다.

🔴 확인 포인트
  · 기존 면적 0.0000  → 그 레이어는 배제에 아무 기여를 못 한다(조용한 실패).
    STEP4 는 이제 여기서 중단한다.
  · 판정 열   면/점을 지목 배수로 어떻게 갈랐는지. `--detail` 로 지목별 근거를 본다.
  · ⚠ 경고    면적이 0 이 아니어도 **일부** 건이 기여 0 일 수 있다(반경 없는 점).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings

os.environ.setdefault("SHAPE_RESTORE_SHX", "YES")
warnings.filterwarnings("ignore", "GeoSeries.notna", UserWarning)

import geopandas as gpd
from shapely.ops import unary_union

# 이 스크립트는 `app/tools/` 안에 있다 — 저장소 루트는 두 단계 위다.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from app.config import (
        STEP1_OUTPUT_DIR,
        STEP2_OUTPUT_DIR,
        REGION_DATA_DIR,
        DOMAIN_ROOT,
        domain_prefix,
    )
except Exception:
    DOMAIN_ROOT = "data_임시"
    STEP1_OUTPUT_DIR = os.path.join("data_임시", "step1_output")
    STEP2_OUTPUT_DIR = os.path.join("data_임시", "step2_output")
    REGION_DATA_DIR = os.path.join("data_임시", "region_data")

    def domain_prefix(d):
        return d


from app.services import gam4_spatial_ops as S
from app.services import gam4_exclusion_shape as X
from app.services.gam2_weight_model import find_region_file

WORK_CRS = 5186


def _fixture_union(domain: str):
    """픽스처의 배제 union 기준값 → (S9, no_shape_lift, 고정일). 없으면 None.

    이 값은 산출물에 없다 — STEP4 로그에만 찍혀서 make_fixture.py 가 수기로 받는다.
    그래서 여기서도 재계산이 아니라 **기록된 기준**과 대조하는 것이다.
    """
    p = os.path.join(str(DOMAIN_ROOT), f"{domain}_FIX", "기준값.json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        b = json.load(f)
    s4 = b.get("STEP4") or {}
    ref, nsl = s4.get("배제_union_km2"), s4.get("배제_union_km2_no_shape_lift")
    if ref is None or nsl is None:
        return None
    return ref, nsl, b.get("고정일", "?")


def _resolve(path: str) -> str | None:
    if not path:
        return None
    if os.path.isfile(path):
        return path
    alt = os.path.join(STEP2_OUTPUT_DIR, os.path.basename(path))
    return alt if os.path.isfile(alt) else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--reviewed", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument(
        "--region",
        default=None,
        help="지적도 선택용 지역명. 미지정 시 reviewed.json 에서 읽는다",
    )
    ap.add_argument("--lift", type=float, default=X.LIFT_MIN)
    ap.add_argument("--share", type=float, default=X.SHARE_MIN)
    ap.add_argument("--min-pts", type=int, default=X.COUNT_MIN)
    ap.add_argument("--no-expand", action="store_true")
    ap.add_argument("--detail", action="store_true", help="지목별 판정 근거 출력")
    args = ap.parse_args()

    pfx = domain_prefix(args.domain)
    rv_path = args.reviewed or os.path.join(
        STEP1_OUTPUT_DIR, f"{pfx}_audit_result_reviewed.json"
    )
    cr_path = args.report or os.path.join(STEP2_OUTPUT_DIR, f"{pfx}_clean_report.json")

    reviewed = json.load(open(rv_path, encoding="utf-8"))
    report = json.load(open(cr_path, encoding="utf-8"))
    files = {r["dataset_id"]: r.get("output") for r in report.get("results", [])}

    region = (
        args.region
        or reviewed.get("region")
        or reviewed.get("facility_inference", {}).get("region")
        or ""
    )
    shp = find_region_file("LSMD_CONT_LDREG_*.shp", region, root=REGION_DATA_DIR)
    parcels = S.load_parcels(shp, verbose=False)
    base = X.area_base_rate(parcels)

    print("=" * 104)
    print(f"[배제 레이어 진단] {args.domain}  ·  {region}")
    print(f"  reviewed : {os.path.basename(rv_path)}")
    print(
        f"  지적도   : {os.path.basename(shp)}  {len(parcels):,}필지 "
        f"{parcels['면적'].sum() / 1e6:.2f} km²"
    )
    print(
        f"  임계     : 배수 {args.lift:g}x · 관측 {args.share:.0%} · "
        f"표본 {args.min_pts}점 · 인접확장 {'끔' if args.no_expand else '켬'}"
    )
    print("=" * 104)
    print(
        f"{'ID':<4} {'시설유형':<14} {'감리':<9} {'S9판정':<8} {'반경':<7} "
        f"{'건수':>6} {'기존 km²':>10} {'S9 km²':>10}  비고"
    )
    print("-" * 104)

    old_geoms, new_geoms, n_zero, details = [], [], 0, []

    for r in reviewed.get("results", []):
        did = r.get("dataset_id")
        for role in r.get("roles") or []:
            if role.get("role") != "hard_exclusion":
                continue
            rad = role.get("배제반경_m")
            etype = role.get("exclusion_type") or "-"
            ftype = (role.get("facility_type") or "")[:13]

            path = _resolve(files.get(did))
            if not path or not path.endswith(".gpkg"):
                print(
                    f"{did:<4} {ftype:<14} {etype:<9} {'-':<8} {str(rad):<7} "
                    f"{0:>6} {0:>10} {0:>10}  🔴 정제파일 없음/비gpkg"
                )
                continue

            g = S.clean_geometry(gpd.read_file(path).to_crs(WORK_CRS))
            if g.empty:
                print(
                    f"{did:<4} {ftype:<14} {etype:<9} {'-':<8} {str(rad):<7} "
                    f"{0:>6} {0:>10} {0:>10}  🔴 빈 레이어"
                )
                n_zero += 1
                continue

            # 기존 계산 (S9 이전 경로)
            old = unary_union(
                (g.geometry.buffer(float(rad)) if rad else g.geometry).values
            )
            old_geoms.append(old)

            note = []
            if all(t in ("Point", "MultiPoint") for t in set(g.geom_type)):
                det = X.resolve(
                    g,
                    parcels,
                    rad,
                    base=base,
                    lift_min=args.lift,
                    share_min=args.share,
                    count_min=args.min_pts,
                    expand=not args.no_expand,
                )
                new_geoms.append(det["geom"])
                verdict, new_area = det["exclusion_type"], det["area_km2"]
                if det["n_시드필지"]:
                    note.append(f"필지 {det['n_시드필지']}→{det['n_확장필지']}")
                note += ["⚠ " + w for w in det["warnings"]]
                details.append((did, ftype, det))
            else:
                new_geoms.append(old)
                verdict, new_area = etype, old.area / 1e6
                note.append("폴리곤 원본 — 배수 판정 대상 아님")

            if old.area == 0:
                note.insert(0, "🔴 기존 기여 0")
                n_zero += 1

            print(
                f"{did:<4} {ftype:<14} {etype:<9} {verdict:<8} "
                f"{(str(rad) + 'm') if rad else '없음':<7} {len(g):>6,} "
                f"{old.area / 1e6:>10.4f} {new_area:>10.4f}  {' · '.join(note)}"
            )

    print("-" * 104)
    if old_geoms:
        uo = unary_union(old_geoms).area / 1e6
        un = unary_union([x for x in new_geoms if x is not None]).area / 1e6
        print(
            f"{'':<4} {'union 합계':<14} {'':<9} {'':<8} {'':<7} {'':>6} "
            f"{uo:>10.4f} {un:>10.4f}   ×{un / uo if uo else 0:.2f}"
        )
        # 기준선은 **픽스처에서 읽는다.** 여기 숫자를 박아두면 기준선을 옮긴 뒤에도
        #   진단 도구가 옛 값을 '현행 기준'이라 광고한다 — 산출물이 거짓말한다(원칙 4).
        #   실제로 2026-08-03 까지 폐기된 0.7552(재현 불가) 를 계속 찍고 있었다.
        _base = _fixture_union(args.domain)
        if _base:
            ref, ref_nsl, when = _base
            print(f"\n  픽스처 기준({when}): S9 {ref} km² · 기존 {ref_nsl} km²")
            print(
                f"    → 기존 {uo - ref_nsl:+.4f} · S9 {un - ref:+.4f}"
                + (
                    "   ✅ 일치"
                    if abs(un - ref) < 5e-5 and abs(uo - ref_nsl) < 5e-5
                    else "   ⚠ 차이 — 기준선을 옮겼다면 make_fixture.py 로 함께 갱신할 것"
                )
            )
        else:
            print(
                f"\n  픽스처 없음 — 비교 기준이 없다. "
                f"고정하려면: python app/tools/make_fixture.py {args.domain} "
                f"--union {un:.4f} --union-nsl {uo:.4f} ... --write"
            )
    else:
        print("  배제 레이어 0건")

    if args.detail:
        for did, ftype, det in details:
            print(f"\n[{did}] {ftype}  {det['n_면점']}면/{det['n_점점']}점")
            print(*X.format_rows(det["rows"]), sep="\n")

    if n_zero:
        print(
            f"\n  🔴 기존 계산에서 기여 0 인 레이어 {n_zero}건 — "
            f"STEP4 는 이제 여기서 중단합니다."
        )
    if not args.detail:
        print("\n  판정 근거(지목별 배수)를 보려면 --detail")
    print("=" * 104)


if __name__ == "__main__":
    main()
