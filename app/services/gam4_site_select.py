# -*- coding: utf-8 -*-
"""
OmniSite 위치선정 (STEP 4) — 최소 동작 버전
============================================
  python gam4_site_select.py 흡연
  python gam4_site_select.py 흡연 --spacing 10 --topn 20 --dmin 100

흐름
  A 후보 필지(gpkg)  ->  B 필지내부 격자점
  C 지표 부착(STEP3 재사용)  ->  D 점수행렬(감쇠)  ->  E 정규화·가중합
  [점수 동결]  ->  G 배제 필터  ->  H greedy + 최소이격  ->  Top-N

미포함(온전판에서 추가): 국유지분 태그 · MCLP 정식 · 표출 산출물 · 갭 리포트

핵심 원칙
  · D 는 STEP3 의 build_matrix 를 **그대로** 호출한다. 별도 구현하면
    가중치를 뽑은 행렬과 점수를 매기는 행렬이 달라진다.
  · 배제는 점수화 **뒤**에 건다. 그래야 프런트에서 필터를 만져도 점수
    재계산이 없고, 배제구역도 점수를 가져 "수요 최고인데 막힘"을 보여준다.
"""

from __future__ import annotations

import sys
import io

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import os
import sys
import time

_T_START = time.perf_counter()  # 프로세스 기동 직후(무거운 임포트 전)

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_T_IMPORT = time.perf_counter()
import numpy as np
import pandas as pd
import geopandas as gpd

try:
    from app.config import (
        STEP1_OUTPUT_DIR,
        STEP2_OUTPUT_DIR,
        STEP3_OUTPUT_DIR,
        STEP4_OUTPUT_DIR,
        ADM_DONG_SHP,
        REGION_DATA_DIR,
        DISPLAY_CRS,
        CANDIDATE_GPKG_NAME,
        domain_prefix,
    )
except Exception:
    STEP1_OUTPUT_DIR = STEP2_OUTPUT_DIR = STEP3_OUTPUT_DIR = "."
    STEP4_OUTPUT_DIR = "."
    DISPLAY_CRS = 4326
    REGION_DATA_DIR = "."
    ADM_DONG_SHP = None
    CANDIDATE_GPKG_NAME = "후보_지적도필지.gpkg"

    def domain_prefix(d):
        return d


from app.services import gam2_weight_model as W
from app.services import gam4_spatial_ops as S
from app.services import gam4_facility_params as FP
from app.services import gam4_export as EX
from app.services import gam4_jimok as J
from app.services import gam4_exclusion_shape as X

IMPORT_SEC = time.perf_counter() - _T_IMPORT  # geopandas 계열 + 프로젝트 모듈


# =========================================================
# 로드
# =========================================================
def make_loader(domain: str):
    """dataset_id -> GeoDataFrame(5186)|DataFrame. (run_weight_model 과 동일)"""
    prefix = domain_prefix(domain)
    rpt = os.path.join(STEP2_OUTPUT_DIR, f"{prefix}_clean_report.json")
    doc = json.load(open(rpt, encoding="utf-8"))
    files = {}
    for r in doc.get("results", []):
        out = r.get("output")
        if not out:
            continue
        if os.path.isfile(out):
            files[r["dataset_id"]] = out
        else:
            alt = os.path.join(STEP2_OUTPUT_DIR, os.path.basename(out))
            if os.path.isfile(alt):
                files[r["dataset_id"]] = alt

    def loader(did):
        f = files.get(did)
        if not f:
            print(f"  ⚠ 레이어 '{did}' 정제 산출물 없음 — 빈 레이어로 대체합니다.")
            return gpd.GeoDataFrame(columns=["geometry"], crs=W.WORK_CRS)
        if f.endswith(".gpkg"):
            return gpd.read_file(f).to_crs(W.WORK_CRS)
        # parquet 에 좌표가 남아 있으면 geometry 복원(좌표계는 값으로 판정).
        return W.as_geodataframe(pd.read_parquet(f), did)

    return loader, doc


def load_weight_set(domain: str, path: str | None = None) -> dict:
    p = path or os.path.join(
        STEP3_OUTPUT_DIR, f"{domain_prefix(domain)}_weight_set.json"
    )
    if not os.path.isfile(p):
        raise FileNotFoundError(f"weight_set 없음: {p}\n  STEP3 를 먼저 실행하세요.")
    ws = json.load(open(p, encoding="utf-8"))
    print(
        f"[weight_set] {os.path.basename(p)}  "
        f"후보표본 {ws.get('n_candidates', '?')}  alpha={ws.get('alpha')}"
    )
    d = ws.get("decay") or {}
    print(
        f"             감쇠 {d.get('func') or '없음'}"
        + (f" σ=R×{d.get('sigma_ratio'):.3f}" if d.get("func") else "")
        + f"  정규화 {ws.get('scale') or 'minmax'}"
    )
    return ws


def check_consistency(ws: dict, inds: list) -> None:
    """weight_set 과 재구성한 지표가 같은지 검사. 다르면 **중단**.

    데이터셋 ID 는 파일명 가나다순으로 매번 재부여된다. 폴더에 파일 하나만
    추가돼도 전체 ID 가 밀리는데, 그래도 계산은 정상 진행되고 점수도 나온다.
    (마포구 생활인구가 용산구인 줄 알고 통과했던 것과 같은 실패 방식)
    """
    have = {i["id"]: i for i in inds}
    want = {x["id"]: x for x in ws.get("indicators", [])}
    errs = []
    if set(have) != set(want):
        errs.append(
            f"지표 ID 불일치\n"
            f"    weight_set: {sorted(want)}\n"
            f"    현재 감리  : {sorted(have)}"
        )
    for iid in set(have) & set(want):
        a, b = have[iid], want[iid]
        if a.get("kind") != b.get("kind"):
            errs.append(f"[{iid}] kind 다름: {b.get('kind')} != {a.get('kind')}")
        ca = {"geo": a.get("geo_dataset"), "val": a.get("val_dataset")}
        cb = b.get("components") or {}
        if ca != cb:
            errs.append(f"[{iid}] 구성 데이터셋 다름: {cb} != {ca}")
    if errs:
        raise SystemExit(
            "[중단] weight_set 과 현재 감리 결과가 어긋납니다.\n  "
            + "\n  ".join(errs)
            + "\n\n  데이터셋 폴더가 바뀌면 ID 가 전부 밀립니다."
            "\n  STEP3 를 다시 실행해 weight_set 을 갱신하세요."
        )
    print(f"[정합성] 지표 {len(have)}개 일치 ✅")


def _list_layers(path: str) -> list:
    """gpkg 레이어 목록. GeoPandas 1.x 기본 엔진은 pyogrio, 구버전은 fiona."""
    try:
        import pyogrio

        return [str(x) for x in pyogrio.list_layers(path)[:, 0]]
    except Exception:
        pass
    try:
        import fiona

        return list(fiona.listlayers(path))
    except Exception:
        return []


def _read_parcels(path: str, layer: str | None) -> gpd.GeoDataFrame:
    """후보 필지(폴리곤) 로드.

    ⚠ 점 데이터를 받으면 필지 내부 격자가 0개가 된다 — 점 안에는 격자점이
      들어갈 수 없다. 그래도 계산은 정상 진행되고 Top-N 도 나오므로
      (필지당 1점이 되어 STEP3 표본과 동일해질 뿐) 조용히 틀린다. 여기서 막는다.
    """
    layers = _list_layers(path)
    pick = layer or ("parcels" if "parcels" in layers else None)
    g = gpd.read_file(path, layer=pick) if pick else gpd.read_file(path)
    g = g.to_crs(W.WORK_CRS)

    kinds = set(g.geom_type.dropna().unique())
    if kinds & {"Point", "MultiPoint"}:
        raise SystemExit(
            f"[중단] 후보가 점 데이터입니다 (layer='{pick or '기본'}', {kinds}).\n"
            f"  필지 폴리곤이 있어야 내부 격자를 찍을 수 있습니다.\n"
            f"  이 파일의 레이어: {layers}\n"
            f"  make_parcel_candidates.py 를 다시 실행해 'parcels' 레이어를 만드세요."
        )
    return g


# =========================================================
# 점수화
# =========================================================
def score_candidates(
    pts: gpd.GeoDataFrame,
    inds: list,
    ws: dict,
    admin_gdf=None,
    diag: bool = True,
    label: str = "후보",
):
    """STEP3 와 동일한 경로로 점수를 낸다. 반환 (점수, 정규화행렬, 원행렬)."""
    radius_m = {x["id"]: x.get("radius_m") for x in ws["indicators"]}
    decay = (ws.get("decay") or {}).get("func")
    sratio = (ws.get("decay") or {}).get("sigma_ratio") or 1 / 3

    print(
        f"\n[D] 점수 행렬  {label} {len(pts):,}점"
        + (f"  감쇠={decay} σ=R×{sratio:.3f}" if decay else "  감쇠 없음")
    )
    mat = W.build_matrix(
        pts, inds, radius_m, admin_gdf=admin_gdf, decay=decay, sigma_ratio=sratio
    )

    if diag:
        diagnose_distribution(mat)

    # 정규화는 weight_set 에 기록된 방식을 그대로 쓴다.
    #   가중치를 뽑을 때와 다른 스케일로 점수를 매기면 앞뒤가 안 맞는다.
    scale = ws.get("scale") or "minmax"
    norm = W.normalize_matrix(mat, inds, scale=scale)
    wf = {x["id"]: float(x["w_final"]) for x in ws["indicators"]}
    miss = set(norm.columns) - set(wf)
    if miss:
        raise SystemExit(f"[중단] w_final 없는 지표: {sorted(miss)}")
    score = sum(norm[c] * wf[c] for c in norm.columns).to_numpy()
    print(
        f"[E] 점수  min {score.min():.4f}  중앙 {np.median(score):.4f}  "
        f"max {score.max():.4f}"
    )
    return score, norm, mat


def diagnose_distribution(mat: pd.DataFrame) -> None:
    """정규화 방식 판단용. min-max 는 이상치 하나가 전체 스케일을 지배한다.

    '상위1%가 전체 합의 몇 %를 차지하는가' 로 롱테일 정도를 본다.
    이 값이 크면 min-max 에서 나머지 후보가 전부 0 근처로 눌린다.
    """
    print("\n[진단] 지표 분포 (min-max 적합성)")
    print(
        f"  {'지표':<8}{'비영%':>7}{'중앙':>10}{'p99':>12}{'max':>12}"
        f"{'max/p99':>9}{'상위1%비중':>10}"
    )
    for c in mat.columns:
        v = mat[c].to_numpy(dtype=float)
        nz = v[v > 0]
        if len(nz) == 0:
            print(
                f"  {c:<8}      0        -           -           -        -         -"
            )
            continue
        p99 = np.percentile(v, 99)
        top1 = v[v >= p99].sum() / v.sum() if v.sum() else 0
        ratio = v.max() / p99 if p99 > 0 else float("inf")
        flag = "  <- 롱테일" if ratio > 3 or top1 > 0.3 else ""
        print(
            f"  {c:<8}{(v > 0).mean() * 100:>6.1f}%{np.median(nz):>10,.1f}"
            f"{p99:>12,.1f}{v.max():>12,.1f}{ratio:>9.1f}{top1 * 100:>9.1f}%{flag}"
        )


def build_demand_grid(
    cpath: str, parcels: gpd.GeoDataFrame, spacing: float, verbose: bool = True
) -> gpd.GeoDataFrame:
    """수요 격자 — 지역 전역을 균등하게 덮는다. 후보와 별개다.

    ⚠ 경계는 **지목 필터 전** 전체 지적도여야 한다.
      수요는 '지을 수 있는 곳'이 아니라 '사람이 있는 곳'이다. 하천·철도·학교
      필지를 빼면 용산구 면적의 30%(21.90 -> 15.21km²)가 날아가고, 학교 주변
      성인 흡연 수요나 역세권 수요가 통째로 사라진다.

    make_parcel_candidates 가 gpkg 에 'boundary' 레이어로 저장해 둔다.
    없으면 필터된 필지로 폴백하되 경고한다(구버전 gpkg 호환).
    """
    import time as _t

    t0 = _t.perf_counter()
    boundary = None
    if "boundary" in _list_layers(cpath):
        b = gpd.read_file(cpath, layer="boundary").to_crs(parcels.crs)
        if len(b):
            boundary = b.geometry.iloc[0]
    if boundary is None:
        boundary = parcels.geometry.union_all()
        if verbose:
            print(
                "  ⚠ 'boundary' 레이어 없음 — 필터된 필지로 폴백. "
                "수요 영역이 실제보다 좁습니다. "
                "make_parcel_candidates.py 를 다시 실행하세요."
            )
    grid = S.make_grid(boundary, spacing, crs=parcels.crs, verbose=False)
    if verbose:
        print(
            f"  [수요격자] {len(grid):,}점  spacing={spacing:g}m  "
            f"경계 {boundary.area / 1e6:.2f}km²  [{_t.perf_counter() - t0:.1f}s]"
        )
    return grid


# =========================================================
# 배제
# =========================================================
def load_exclusions(
    reviewed: dict,
    loader,
    all_parcels=None,
    shape_opts: dict | None = None,
    verbose: bool = True,
):
    """reviewed.json 의 hard_exclusion -> 버퍼 union.

    all_parcels 가 있으면 **지목 배수로 점/면을 판정**하고(S9, gam4_exclusion_shape),
    면으로 판정된 점은 그 필지(+인접 동일지목 확장)를 복원해 배제한다.
    LLM 이 낸 `exclusion_type` 은 참고로만 쓰고 코드 값이 확정한다 —
    `exclusion_type_source` 에 어느 쪽이 쓰였는지 남긴다.

    all_parcels 가 없으면 종전대로 `buffer_union` 만 한다(구 경로 보존).

    ※ all_parcels 는 **지목 필터 전 전체 지적도**여야 한다.
      후보 필지를 넣으면 `학`·`천` 이 빠져 기저율이 왜곡된다.
    """
    opts = shape_opts or {}
    base = X.area_base_rate(all_parcels) if all_parcels is not None else None
    geoms, rows, layers = [], [], {}  # layers: 표출에서 '어느 규칙인지' 보여주기 위함
    for r in reviewed.get("results", []):
        did = r.get("dataset_id")
        for role in r.get("roles") or []:
            if role.get("role") != "hard_exclusion":
                continue
            rad = role.get("배제반경_m")
            etype_llm = role.get("exclusion_type", "")
            try:
                g = loader(did)
            except Exception as e:
                rows.append(
                    {
                        "id": did,
                        "etype": etype_llm,
                        "src": "-",
                        "radius": rad,
                        "n": 0,
                        "area": 0.0,
                        "note": f"로드 실패({e})",
                    }
                )
                continue
            if not isinstance(g, gpd.GeoDataFrame):
                rows.append(
                    {
                        "id": did,
                        "etype": etype_llm,
                        "src": "-",
                        "radius": rad,
                        "n": 0,
                        "area": 0.0,
                        "note": "geometry 없음",
                    }
                )
                continue
            g = S.clean_geometry(g)
            if g.empty:
                rows.append(
                    {
                        "id": did,
                        "etype": etype_llm,
                        "src": "-",
                        "radius": rad,
                        "n": 0,
                        "area": 0.0,
                        "note": "빈 레이어",
                    }
                )
                continue

            det = None
            if base is not None and (g.geom_type == "Point").all():
                # 점 레이어만 배수 판정 대상이다. 이미 폴리곤이면 복원할 것이 없다.
                det = X.resolve(g, all_parcels, rad, base=base, **opts)
                u, etype, src = det["geom"], det["exclusion_type"], "jimok_lift"
            else:
                u, etype, src = S.buffer_union(g, rad), etype_llm, "llm_audit"

            if u is None:
                rows.append(
                    {
                        "id": did,
                        "etype": etype,
                        "src": src,
                        "radius": rad,
                        "n": len(g),
                        "area": 0.0,
                        "note": "빈 레이어",
                    }
                )
                continue
            geoms.append(u)
            layers[did] = {
                "geom": u,
                "type": etype,
                "radius": rad,
                "exclusion_type_source": src,
                "exclusion_type_llm": etype_llm,
                "detail": det,
            }
            # rows 는 갭 리포트로 흘러간다 — shapely geom 을 넣으면 직렬화에서 터진다.
            rows.append(
                {
                    "id": did,
                    "etype": etype,
                    "src": src,
                    "radius": rad,
                    "n": len(g),
                    "area": u.area / 1e6,
                    "note": "",
                    "detail": {k: v for k, v in det.items() if k != "geom"}
                    if det
                    else None,
                }
            )

    union = S.union_all(geoms)
    if verbose:
        print("\n[G] 배제 레이어")
        for r in rows:
            print(
                f"  {r['id']:<4} {r['etype']:<8} {r['src']:<10} "
                f"+{(str(r['radius']) + 'm') if r['radius'] else '  -   ':<6} "
                f"{r['n']:>6,} features  {r['area']:>8.4f} km²  {r['note']}"
            )
            d = r.get("detail")
            if d:
                if d["n_시드필지"]:
                    print(
                        f"       면 {d['n_면점']}점 → 시드 {d['n_시드필지']}필지 "
                        f"→ 인접확장 {d['n_확장필지']}필지 · 점 {d['n_점점']}점"
                    )
                for w in d.get("warnings", []):
                    print(f"       ⚠ {w}")
        if union is not None:
            print(f"    union {union.area / 1e6:.4f} km²")
    _guard_zero_area(rows)
    return union, layers, rows


def _guard_zero_area(rows: list) -> None:
    """면적 0 레이어는 배제에 아무 기여를 못 한다 — 조용한 실패의 전형이라 중단한다.

    실제로 겪은 형태: `11 어린이보호구역` 이 polygon 판정 + 반경 없음이라
    `buffer_union(points, None)` = 점들의 union = **면적 0**. 경고도 예외도 없었고
    31개 시설이 배제에서 통째로 빠진 채 Top-N 이 나왔다.

    **features 가 있는데** 면적이 0 인 경우만 잡는다. 로드 실패·빈 레이어는
    이미 note 가 붙어 갭 리포트에 드러나므로 중단시키지 않는다 — 조용하지 않다.
    """
    dead = [r for r in rows if r["n"] > 0 and r["area"] == 0]
    if not dead:
        return
    lines = "\n".join(
        f"    {r['id']}  {r['etype'] or '-':<8} 반경 {r['radius'] or '없음'}  "
        f"{r['n']:,} features  {r['note'] or '기여 0'}"
        for r in dead
    )
    raise SystemExit(
        f"[중단] 배제 기여가 0 인 레이어 {len(dead)}건 — 배제가 조용히 사라집니다.\n"
        f"{lines}\n\n"
        f"  점 레이어인데 반경이 없으면 면적이 0 이 됩니다.\n"
        f"  HITL 에서 배제반경을 입력하거나, 그 레이어를 hard_exclusion 에서 빼세요:\n"
        f"    python app\\services\\gam2_audit_judgment_test.py hitl <도메인>"
    )


# =========================================================
# 선정
# =========================================================
def select_mclp(
    pts: gpd.GeoDataFrame,
    score: np.ndarray,
    keep: np.ndarray,
    demand_pts: gpd.GeoDataFrame,
    demand_val: np.ndarray,
    n: int = 20,
    r_cover: float = 150.0,
    d_min: float = 100.0,
    pool: int | None = None,
    curve_n: int = 0,
    verbose: bool = True,
):
    """MCLP(Maximal Covering Location Problem) greedy.

    점수 상위 나열(select_topn)과의 차이
      상위 나열 : 봉우리 위에 d_min 간격으로 촘촘히 박힌다. 같은 수요를 여러
                  부스가 중복 서비스해도 점수가 그대로라 계속 뽑힌다.
      MCLP     : 부스를 놓으면 반경 r_cover 안의 수요가 **소진**된다. 다음
                  선택은 아직 커버되지 않은 수요가 많은 곳으로 이동한다.

    수요점과 후보를 분리한다 — 표준 MCLP 의 구조다.
      수요점 : 전역 격자. 배제구역·필지 없는 곳에도 수요는 존재한다.
               (어린이집 옆엔 못 짓지만 그 수요는 근처 부스가 커버해야 한다)
      후보   : 배제·폭 필터를 통과한 지점.

    ※ 수요점을 후보 자신으로 두면(초기 구현) 고득점 지역에 몰린 5,000점을
      부스 20개가 99.6% 덮어버려 후반 선택이 부스러기 줍기가 된다.

    반환에 '커버기여' 를 담는다. MCLP 순위는 점수 순이 아니라 **그 시점에
    흡수한 미커버 수요량** 순이므로, 없으면 순위 역전이 오해를 부른다.
    """
    idx = np.where(keep)[0]
    if len(idx) == 0:
        return pd.DataFrame()
    # ⚠ pool 을 쓰면 안 되는 이유 — 선정 기준과 절단 기준이 다르다.
    #   MCLP 는 '커버 기여'로 고르는데 pool 은 '점수'로 자른다. 점수 상위는
    #   고수요 봉우리에 몰려 있어, MCLP 가 빈 곳을 메우려 찾는 외곽 후보가
    #   애초에 목록에서 빠진다.
    #   실측(후보 57,530): pool=5,000 이면 봉우리 700m 내 후보가 100%,
    #   전체를 쓰면 19.1%. 커버율이 56.4% -> 63.5% 로 7.1%p 손실이었다.
    #   전체를 써도 1.9초라 정확성을 성능과 바꿀 이유가 없다.
    #   (greedy 는 점수 순으로 뽑으므로 pool 절단이 타당하다 — select_topn 참조)
    if pool:
        idx = idx[np.argsort(-score[idx])][:pool]
        if verbose:
            print(
                f"    ⚠ pool={pool:,} 로 후보를 점수 상위만 남김 — "
                f"커버 최적성이 떨어질 수 있음"
            )
    sub = pts.iloc[idx].reset_index(drop=True)

    dem = np.asarray(demand_val, dtype=float)
    total = dem.sum()
    ci, tj, _ = S.neighbors_within(sub, demand_pts, r_cover)

    # 도달 상한 — 반경 내에 후보가 하나도 없는 수요점은 어떤 배치로도 못 덮는다.
    #   배제구역 한복판·수면 인접·초대형 필지 내부가 여기 해당한다.
    #   이 값을 안 내놓으면 "왜 100% 가 안 되나" 가 버그인지 사실인지 구분이 안 된다.
    reached = np.zeros(len(demand_pts), dtype=bool)
    if len(tj):
        reached[np.unique(tj)] = True
    ceiling = float(dem[reached].sum() / total) if total else 0.0

    if verbose:
        print(
            f"    후보 {len(idx):,}"
            + ("(pool 제한)" if pool else "(전체)")
            + f" × 수요점 {len(demand_pts):,}  "
            f"커버 쌍 {len(ci):,}  (R_cover={r_cover:g}m)"
        )
        print(
            f"    도달 상한 {ceiling * 100:.2f}%  "
            f"— 반경 내 후보 없는 수요점 {int((~reached).sum()):,}"
            f"/{len(demand_pts):,} (수요 비중 {(1 - ceiling) * 100:.2f}%)"
        )

    xy = np.c_[sub.geometry.x.to_numpy(), sub.geometry.y.to_numpy()]
    covered = np.zeros(len(demand_pts), dtype=bool)
    blocked = np.zeros(len(idx), dtype=bool)
    chosen, gains = [], []
    d2 = d_min * d_min
    demw = dem[tj]  # 루프마다 재인덱싱하지 않도록 1회만

    n_iter = max(n, curve_n)  # 곡선을 보려면 n 을 넘겨 더 돌린다
    for _ in range(n_iter):
        gain = np.bincount(ci, weights=demw * (~covered[tj]), minlength=len(idx))
        gain[blocked] = -1.0
        best = int(np.argmax(gain))
        if gain[best] <= 0:
            break
        chosen.append(best)
        gains.append(float(gain[best]))
        covered[tj[ci == best]] = True
        blocked |= ((xy[:, 0] - xy[best, 0]) ** 2 + (xy[:, 1] - xy[best, 1]) ** 2) < d2

    sel = pts.iloc[idx[chosen]].copy()
    sel["점수"] = score[idx[chosen]]
    sel["커버기여"] = gains
    sel["누적커버율"] = np.cumsum(gains) / total if total else 0.0
    sel["순위"] = np.arange(1, len(chosen) + 1)
    sel.attrs["gains"] = list(gains)  # 곡선 분석용(n 을 넘는 구간 포함)
    sel.attrs["total_demand"] = float(total)
    sel.attrs["ceiling"] = ceiling
    # 🔴 S5(PostGIS 전환) 회귀 대조용. **커버 쌍 개수가 공간 술어에 가장 예민하다** —
    #   `neighbors_within` 이 `ST_DWithin` 으로 바뀌었을 때 쌍 개수가 그대로면
    #   술어가 같게 동작한 것이고, 어긋나면 그 아래 모든 지표가 의미를 잃는다.
    #   지금까지 로그에만 찍히고 산출물에 없어 **대조할 방법이 없었다**.
    sel.attrs["cover_pairs"] = int(len(ci))
    sel.attrs["n_cand_mclp"] = int(len(idx))
    sel.attrs["n_demand"] = int(len(demand_pts))
    sel.attrs["unreached_n"] = int((~reached).sum())
    sel.attrs["unreached_val"] = float(dem[~reached].sum() / total) if total else 0.0
    if verbose and len(chosen):
        achieved = float(dem[covered].sum() / total) if total else 0.0
        rel = f"  · 도달 상한 대비 {achieved / ceiling * 100:.1f}%" if ceiling else ""
        print(
            f"    커버 수요 {achieved * 100:.1f}% "
            f"({int(covered.sum()):,}/{len(demand_pts):,}점){rel}"
        )
        if len(chosen) < n_iter:
            # gain=0 조기 종료가 '정상 포화'인지 '후보 누락'인지 여기서 갈린다.
            tag = (
                "도달 상한 소진 — 정상 종료"
                if achieved >= ceiling - 1e-9
                else "⚠ 상한에 못 미친 채 종료 — 후보/쌍 생성 확인 필요"
            )
            print(f"    ⓘ {len(chosen)}개에서 한계기여 0 → 중단. {tag}")
    return sel.reset_index(drop=True)


def analyze_coverage(
    gains: list,
    total: float,
    targets=(0.5, 0.7, 0.8, 0.9),
    ceiling: float | None = None,
) -> dict:
    """커버 곡선 분석 — 설치 개수 판단 근거.

    **개수를 단정하지 않는다.** 적정 개수는 예산·유지관리 인력·정책 목표에
    달렸고 그 정보가 시스템에 없다. 대신 판단 재료를 제공한다:
      · 목표 커버율별 필요 개수
      · 한계기여가 급감하는 지점(효율 변곡점)

    변곡점은 누적곡선의 양 끝을 이은 현(chord)에서 가장 멀리 떨어진 점으로
    잡는다. 임계값을 정하지 않아도 되고 설명이 단순하다.
    """
    g = np.asarray(gains, dtype=float)
    if len(g) == 0 or total <= 0:
        return {}
    cum = np.cumsum(g) / total
    n = np.arange(1, len(g) + 1)

    reach = {}
    for t in targets:
        hit = np.where(cum >= t)[0]
        reach[t] = int(hit[0] + 1) if len(hit) else None

    knee = None
    if len(g) >= 3:
        x = (n - n[0]) / (n[-1] - n[0]) if n[-1] > n[0] else n * 0.0
        y = (cum - cum[0]) / (cum[-1] - cum[0]) if cum[-1] > cum[0] else cum * 0.0
        knee = int(np.argmax(y - x) + 1)  # 현에서 가장 먼 지점

    return {
        "cum": cum,
        "gain": g,
        "reach": reach,
        "knee": knee,
        "n_max": int(len(g)),
        "ceiling": ceiling,
    }


def print_coverage(an: dict, show: int = 20) -> None:
    """커버 곡선 표. show = 표시할 행 수(대략), 0 이면 전부.

    ⚠ 등간격 샘플만 하면 안 된다. --curve-n 100 · show 20 이면 5개마다 찍히는데,
      실측에서 변곡점 43·70% 도달 34·90% 도달 57 이 전부 표에서 빠졌다.
      담당자가 개수를 정하는 근거가 되는 행이 안 보이면 표를 낼 이유가 없다.
      → 등간격 + 반드시 보여야 할 지점의 합집합으로 출력한다.
    """
    if not an:
        return
    cum, g = an["cum"], an["gain"]
    gmax = g.max() if len(g) else 1.0
    gsum = g.sum() or 1.0
    n = an["n_max"]
    print("\n" + "=" * 70)
    print(f"[커버 곡선]  {n}개까지 계산")
    print("-" * 70)
    print(f"  {'개수':>4}{'누적커버':>10}{'한계기여':>10}   기여 크기")

    rows = set(range(0, n, max(1, n // show))) if (show and n > show) else set(range(n))
    rows |= {0, n - 1}
    marks: dict[int, str] = {}
    if an.get("knee"):
        rows.add(an["knee"] - 1)
        marks[an["knee"] - 1] = "◀ 변곡점"
    for t, v in (an.get("reach") or {}).items():
        if v:
            rows.add(v - 1)
            tag = f"◀ {int(t * 100)}% 도달"
            marks[v - 1] = (marks[v - 1] + " " + tag) if v - 1 in marks else tag

    for i in sorted(rows):
        bar = "█" * max(1, int(g[i] / gmax * 28))
        note = f"  {marks[i]}" if i in marks else ""
        print(
            f"  {i + 1:>4}{cum[i] * 100:>9.1f}%{g[i] / gsum * 100:>9.1f}%   {bar}{note}"
        )
    print("-" * 70)
    r = an["reach"]
    # '미도달'(개수를 더 늘리면 될 수도 있음)과 '도달불가'(상한을 넘음)는 다르다.
    ceil = an.get("ceiling")
    parts = []
    for t, v in r.items():
        if v:
            parts.append(f"{int(t * 100)}% → {v}개")
        elif ceil is not None and t > ceil:
            parts.append(f"{int(t * 100)}% → 도달불가(상한 {ceil * 100:.1f}%)")
        else:
            parts.append(f"{int(t * 100)}% → 미도달")
    print(
        "  목표 커버율:  "
        + "   ".join(parts)
        + (
            f"   (최대 {cum[-1] * 100:.1f}% @ {an['n_max']}개)"
            if any(v is None for v in r.values())
            else ""
        )
    )
    if an.get("knee"):
        k = an["knee"]
        print(
            f"  효율 변곡점:  {k}번째 (누적 {cum[k - 1] * 100:.1f}%). "
            f"이후 부스당 기여가 급감한다"
        )
    print("  ※ 적정 개수는 예산·유지관리 여건에 따라 정해진다. 위는 판단 근거다.")
    print("=" * 70)


# =========================================================
# (이하 기존)
# =========================================================


def select_topn(
    pts: gpd.GeoDataFrame,
    score: np.ndarray,
    keep: np.ndarray,
    n: int = 20,
    d_min: float = 100.0,
    pool: int = 5000,
):
    """점수 상위에서 최소이격 d_min 을 지키며 greedy 선택.

    필지 집계를 하지 않는다(설계 확정 턴20). 큰 필지는 이격만 지키면
    자연히 2개소 이상 가질 수 있다 — 면적 기준 개수 제한은 두지 않는다.

    pool: 상위 몇 개만 후보로 볼지. 하위는 선정될 일이 없어 계산에서 뺀다.
    """
    idx = np.where(keep)[0]
    if len(idx) == 0:
        return pd.DataFrame()
    idx = idx[np.argsort(-score[idx])][: max(pool, n * 50)]

    g = pts.geometry
    xy = np.c_[g.x.to_numpy()[idx], g.y.to_numpy()[idx]]

    chosen, cxy = [], []
    d2 = d_min * d_min
    for k in range(len(idx)):
        if len(chosen) >= n:
            break
        if cxy:
            arr = np.asarray(cxy)
            if ((arr[:, 0] - xy[k, 0]) ** 2 + (arr[:, 1] - xy[k, 1]) ** 2).min() < d2:
                continue
        chosen.append(idx[k])
        cxy.append(xy[k])

    sel = pts.iloc[chosen].copy()
    sel["점수"] = score[chosen]
    sel["순위"] = np.arange(1, len(chosen) + 1)
    return sel.reset_index(drop=True)


# =========================================================
# CLI
# =========================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument(
        "--candidates",
        default=CANDIDATE_GPKG_NAME,
        help="후보 gpkg. 기본은 STEP3_OUTPUT_DIR 의 <도메인>_후보_지적도필지.gpkg",
    )
    ap.add_argument(
        "--layer", default=None, help="gpkg 레이어명 (기본: parcels 있으면 그것)"
    )
    ap.add_argument(
        "--spacing", type=float, default=10.0, help="필지 내부 격자 간격(m)"
    )
    ap.add_argument("--topn", type=int, default=20)
    ap.add_argument(
        "--dmin",
        type=float,
        default=None,
        help="선정지 간 최소이격(m). 미지정이면 판정값",
    )
    ap.add_argument(
        "--min-width",
        type=float,
        default=None,
        help="설치 소요 폭(m). 미지정이면 시설 파라미터 판정값 사용",
    )
    ap.add_argument(
        "--r-cover",
        type=float,
        default=None,
        help="서비스 반경(m). MCLP 커버 범위. 미지정이면 판정값",
    )
    ap.add_argument(
        "--select",
        choices=["mclp", "greedy"],
        default="mclp",
        help="선정 방식 (기본 mclp)",
    )
    ap.add_argument(
        "--demand-spacing",
        type=float,
        default=50.0,
        help="수요 격자 간격(m). MCLP 전용 (기본 50)",
    )
    ap.add_argument(
        "--pool",
        type=int,
        default=None,
        help="MCLP 후보 상한(점수 상위). 기본은 전체. "
        "지정하면 커버 최적성이 떨어진다 — 후보가 수십만일 때만",
    )
    ap.add_argument("--no-export", action="store_true", help="표출 산출물(J) 생성 생략")
    ap.add_argument(
        "--curve-n",
        type=int,
        default=100,
        help="커버 곡선을 몇 개까지 계산할지 (기본 100). "
        "Top-N 보다 크면 그 구간까지 곡선만 본다. "
        "50 이면 70/80/90%% 도달점을 못 봐 개수 판단이 어렵다",
    )
    ap.add_argument(
        "--curve-show",
        type=int,
        default=20,
        help="커버 곡선 표에 찍을 행 수 (기본 20, 0=전부). "
        "변곡점·목표 도달 지점은 이 값과 무관하게 항상 표시된다",
    )
    ap.add_argument(
        "--no-facility-params",
        action="store_true",
        help="시설 파라미터 LLM 판정 생략 (CLI 값만 사용)",
    )
    ap.add_argument("--force-params", action="store_true", help="파라미터 재판정")
    ap.add_argument("--max-per-parcel", type=int, default=400)
    # 배제 점/면 판정(S9). 기본값 근거는 gam4_exclusion_shape 상단 실측표.
    # 용산구 1개 도메인 관찰값이라 재활용·EV 에서는 조정이 필요할 수 있어 노출한다.
    ap.add_argument(
        "--lift",
        type=float,
        default=X.LIFT_MIN,
        help=f"면 판정 지목 배수 임계 (기본 {X.LIFT_MIN:g})",
    )
    ap.add_argument(
        "--share",
        type=float,
        default=X.SHARE_MIN,
        help=f"면 판정 관측비율 하한 (기본 {X.SHARE_MIN:.2f}). "
        f"소수 예외의 면 승격을 막는다",
    )
    ap.add_argument(
        "--min-pts",
        type=int,
        default=X.COUNT_MIN,
        help=f"면 판정 최소 표본 점 수 (기본 {X.COUNT_MIN})",
    )
    ap.add_argument(
        "--no-expand",
        action="store_true",
        help="면 판정 시 인접 동일지목 확장 생략(시드 필지만)",
    )
    ap.add_argument(
        "--no-shape-lift",
        action="store_true",
        help="점/면 배수 판정 생략 — 감리 exclusion_type 을 그대로 쓴다",
    )
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    T = W.Timer()
    prefix = domain_prefix(args.domain)
    loader, report = make_loader(args.domain)
    reviewed = json.load(
        open(
            os.path.join(STEP1_OUTPUT_DIR, f"{prefix}_audit_result_reviewed.json"),
            encoding="utf-8",
        )
    )
    ws = load_weight_set(args.domain)

    # 시설 파라미터 — CLI 지정이 우선, 없으면 LLM 판정(캐시), 그것도 없으면 폴백.
    #   점수를 바꾸지 않는 값들이라 결과화면에서 자유롭게 조절할 수 있다.
    facility = (reviewed.get("facility_inference") or {}).get("facility", args.domain)
    fp = {k: v[2] for k, v in FP.PARAM_SPEC.items()}
    if not args.no_facility_params:
        try:
            rec = FP.judge(facility, force=args.force_params)
            FP.print_params(rec)
            fp.update(rec.get("params", {}))
        except Exception as e:
            print(f"  ⚠ 시설 파라미터 판정 실패({e}) — 폴백값 사용")
    min_width = args.min_width if args.min_width is not None else fp["설치_소요_폭_m"]
    r_cover = args.r_cover if args.r_cover is not None else fp["서비스_반경_m"]
    d_min = args.dmin if args.dmin is not None else fp["최소_이격_m"]
    T.lap("설정·weight_set·파라미터")

    # ── A 후보 필지 ──
    # 후보 gpkg 는 make_parcel_candidates.py 산출물이며 **도메인 프리픽스**가 붙는다.
    #   후보 집합이 지목 판정(시설별)에 의존하므로 도메인마다 다르다.
    #   REGION_DATA_DIR 은 구버전 위치 — 원본·참조 데이터 폴더라 생성물을 두지 않는다.
    _base = os.path.basename(args.candidates)
    _pfx = domain_prefix(args.domain)
    _cands = [args.candidates]
    if _pfx and not _base.startswith(f"{_pfx}_"):
        _cands.append(os.path.join(STEP3_OUTPUT_DIR, f"{_pfx}_{_base}"))
    _cands += [
        os.path.join(STEP3_OUTPUT_DIR, _base),
        os.path.join(REGION_DATA_DIR, _base),
    ]
    cpath = next((p for p in _cands if os.path.isfile(p)), None)
    if cpath is None:
        raise FileNotFoundError(
            "후보 파일 없음. 다음 경로를 찾았습니다:\n  "
            + "\n  ".join(os.path.abspath(p) for p in _cands)
            + f"\n\n  먼저 생성하세요: python app\\services\\make_parcel_candidates.py {args.domain}"
        )
    parcels = _read_parcels(cpath, args.layer)
    print(f"\n[A] 후보 필지 {len(parcels):,}  {os.path.basename(cpath)}")
    T.lap("A 후보 필지 로드")

    # ── B 후보점 ──
    print("[B] 후보점 생성")
    pts = S.points_in_parcels(
        parcels, spacing=args.spacing, max_per_parcel=args.max_per_parcel
    )
    for c in (
        "PNU",
        "JIBUN",
        "지목",
        "면적",
        "내접폭",
        "법정동코드",
        "국유_건수",
        "국유_지분면적",
        "국유_지분율",
        "국유_지번일치",
    ):
        if c in parcels.columns:
            pts[c] = parcels[c].to_numpy()[pts["parcel_idx"].to_numpy()]
    T.lap("B 후보점 생성")

    # ── C 지표 ──
    print("\n[C] 지표 정의·부착")
    inds = W.define_indicators(reviewed, report)
    # region 은 weight_set 에 기록돼 있다 — 행정동명 -> 코드 변환의 시군구 확정에 쓴다.
    W.attach_layers(inds, loader, region=ws.get("region", ""))
    check_consistency(ws, inds)

    admin_gdf = None
    if any(i["kind"] == "admin" for i in inds):
        if ADM_DONG_SHP and os.path.exists(ADM_DONG_SHP):
            admin_gdf = gpd.read_file(ADM_DONG_SHP).to_crs(W.WORK_CRS)
        else:
            print("  ⚠ ADM_DONG_SHP 없음 — admin 지표 계산 불가")
    T.lap("C 지표 부착·정합성")

    # ── D·E 점수 ──
    score, norm, mat = score_candidates(pts, inds, ws, admin_gdf=admin_gdf)
    T.lap("D·E 점수화")

    # ── G 배제 (점수화 뒤) ──
    # 점/면 판정(S9)의 기저율은 **지목 필터 전 전체 지적도**에서 계산해야 한다.
    # 후보 gpkg 의 parcels 레이어는 설치 가능 지목만 남긴 것이라(학·천이 없다)
    # 기저율이 왜곡된다 — 실측 42,216필지 / 원본 44,452필지, 대 66% vs 46%.
    all_parcels = None
    if not args.no_shape_lift:
        try:
            _shp = W.find_region_file(
                "LSMD_CONT_LDREG_*.shp", ws.get("region", ""), root=REGION_DATA_DIR
            )
            all_parcels = S.load_parcels(_shp, verbose=False)
        except Exception as e:
            print(
                f"  ⚠ 전체 지적도 로드 실패({e}) — 점/면 배수 판정 생략, "
                f"감리 exclusion_type 을 그대로 씁니다"
            )
    union, excl_layers, excl_rows = load_exclusions(
        reviewed,
        loader,
        all_parcels=all_parcels,
        shape_opts={
            "lift_min": args.lift,
            "share_min": args.share,
            "count_min": args.min_pts,
            "expand": not args.no_expand,
        },
    )
    keep = S.filter_outside(pts, union)
    n_exc = len(pts) - int(keep.sum())
    print(f"    배제 통과 {keep.sum():,} / {len(pts):,}  ({keep.mean() * 100:.1f}%)")

    # 내접폭 — 배제와 별개 축이다. 규제가 아니라 시설 규격이라 결과화면에서
    # 조절할 수 있어야 하므로 점수화 '뒤'에 건다(설계 확정 턴23).
    if min_width is not None:
        if "내접폭" not in pts.columns:
            print(
                "  ⚠ 내접폭 컬럼 없음 — 필터 생략. "
                "make_parcel_candidates.py 를 --no-width 없이 실행하세요."
            )
        else:
            wok = pts["내접폭"].to_numpy() >= min_width
            keep = keep & wok
            print(
                f"    폭 {min_width:g}m 이상 {int(wok.sum()):,} "
                f"({wok.mean() * 100:.1f}%)  → 최종 생존 {int(keep.sum()):,}"
            )
    T.lap("G 배제·폭 필터")

    # ── H Top-N ──
    print(f"\n[H] 선정 ({args.select})")
    if args.select == "mclp":
        dgrid = build_demand_grid(cpath, parcels, args.demand_spacing)
        T.lap("H1 수요격자 생성")
        dscore, _, _ = score_candidates(
            dgrid, inds, ws, admin_gdf=admin_gdf, diag=False, label="수요격자"
        )
        T.lap("H2 수요격자 점수화")
        sel = select_mclp(
            pts,
            score,
            keep,
            dgrid,
            dscore,
            n=args.topn,
            r_cover=r_cover,
            d_min=d_min,
            curve_n=args.curve_n,
            pool=args.pool,
        )
        T.lap("H3 MCLP 선정")
        # attrs 는 head()·copy() 에서 항상 전파되지는 않는다 — 자르기 전에 빼둔다.
        mclp_meta = {
            k: sel.attrs.get(k)
            for k in (
                "ceiling",
                "unreached_n",
                "unreached_val",
                "cover_pairs",
                "n_cand_mclp",
                "n_demand",
            )
        }
    else:
        sel = select_topn(pts, score, keep, n=args.topn, d_min=d_min)
        T.lap("H Top-N 선정")
        mclp_meta = {}
    cov_an = {}
    if args.select == "mclp" and sel.attrs.get("gains"):
        cov_an = analyze_coverage(
            sel.attrs["gains"],
            sel.attrs["total_demand"],
            ceiling=mclp_meta.get("ceiling"),
        )
        cov_an.update(mclp_meta)  # ceiling·unreached_* 를 산출물까지 전달
        sel = sel.head(args.topn).copy()  # 곡선은 전체, 표는 Top-N 만
    print(
        f"\n[H] Top-{len(sel)}  (이격 {d_min:g}m"
        + (f", 커버 {r_cover:g}m" if args.select == "mclp" else "")
        + ")"
    )
    print("-" * 78)
    wgs = sel.to_crs(DISPLAY_CRS)
    for i, r in sel.iterrows():
        p = wgs.geometry.iloc[i]
        wd = f"{r['내접폭']:.1f}m" if "내접폭" in sel.columns else "-"
        # 실행축 — 점수와 섞지 않고 나란히 보여준다(2축 분리)
        own = ""
        if "국유_지분율" in sel.columns and r.get("국유_건수", 0) > 0:
            own = f"  국유 {r['국유_지분율'] * 100:.0f}%"
        elif "국유_건수" in sel.columns:
            own = "  국유 -"
        cov = (
            f"  누적커버 {r['누적커버율'] * 100:>4.1f}%"
            if "누적커버율" in sel.columns
            else ""
        )
        print(
            f"  {int(r['순위']):>2}. {r.get('JIBUN', '?'):<12} "
            f"{r.get('지목', '?')} {r.get('면적', 0):>8,.0f}m²  폭 {wd:>6}  "
            f"점수 {r['점수']:.4f}{cov}{own:<10}  ({p.x:.5f}, {p.y:.5f})"
        )
    print("-" * 78)

    # 진단: 선정지 간 거리
    if len(sel) > 1:
        xy = np.c_[sel.geometry.x, sel.geometry.y]
        dm = np.sqrt(((xy[:, None] - xy[None]) ** 2).sum(-1))
        np.fill_diagonal(dm, np.inf)
        actual_min = float(dm.min())
        print(
            f"[진단] 선정지 최근접거리 중앙 {np.median(dm.min(1)):.0f}m  "
            f"최소 {actual_min:.0f}m"
        )
        # d_min 은 greedy 시절 '유일한 분산 수단'이었다. MCLP 에서는 r_cover 가
        # 커버 중복을 막아 이미 흩어놓기 때문에, d_min < r_cover 면 대개 무효다.
        #   실측: d_min 0/100/200/300 전부 결과 동일(최소 398m, 커버 88.6%).
        #   d_min > r_cover 가 되어야 걸리는데 그때는 커버가 오히려 떨어진다.
        # 무효인데 화면에 "이격 200m"만 뜨면 제약이 작동한 것으로 오해한다.
        if args.select == "mclp" and d_min > 0:
            if actual_min > d_min * 1.15:
                print(
                    f"       ⚠ d_min={d_min:g}m 이 실제로 걸리지 않음 "
                    f"(r_cover={r_cover:g}m 가 이미 분산시킴)"
                )
            else:
                print(
                    f"       d_min={d_min:g}m 제약이 작동 중 "
                    f"— 값을 낮추면 커버가 오를 수 있음"
                )
        if "법정동코드" in sel.columns:
            vc = sel["법정동코드"].value_counts()
            print(f"       법정동 {len(vc)}개 분산, 최다 {vc.iloc[0]}건")
        if "국유_건수" in sel.columns:
            n_own = int((sel["국유_건수"] > 0).sum())
            n_hi = int((sel.get("국유_지분율", 0) >= 0.9).sum())
            print(
                f"       국유지분 보유 {n_own}/{len(sel)}건 "
                f"(지분율 90%↑ {n_hi}건 = 사실상 국유지)"
            )

    print_coverage(cov_an, show=args.curve_show)

    # ── 갭 리포트 ──
    jrec = None
    try:
        import json as _j

        _p = J.cache_path()
        if os.path.isfile(_p):
            jrec = _j.load(open(_p, encoding="utf-8")).get(facility)
    except Exception:
        pass
    odd = (
        sorted(set(parcels["지목_raw"].dropna()) - set(parcels["지목"].dropna()))
        if "지목_raw" in parcels.columns
        else None
    )
    gaps = EX.build_gap_report(reviewed, excl_rows, jrec, odd)
    # 커버 상한이 100% 가 아닌 것은 버그가 아니라 사실이다 — 그 사실을 남긴다.
    #   남기지 않으면 산출물만 보고 "왜 다 못 덮었나" 를 설명할 근거가 없다.
    if mclp_meta.get("unreached_n"):
        gaps.append(
            {
                "kind": "수요_도달불가",
                "target": f"수요점 {mclp_meta['unreached_n']:,} / {len(dgrid):,} "
                f"(수요 비중 {mclp_meta['unreached_val'] * 100:.2f}%)",
                "detail": f"반경 {r_cover:g}m 안에 배제·폭 필터를 통과한 후보가 없다",
                "impact": f"커버 상한 {mclp_meta['ceiling'] * 100:.2f}% "
                f"— 이 수요는 어떤 배치로도 커버되지 않는다",
            }
        )
    EX.print_gap_report(gaps)

    # ── 공간 연산 회귀 대조값 (S5 PostGIS 전환 준비) ──
    #   왜 산출물에 넣나: 지금까지 배제 union 면적은 **로그에만** 있었다.
    #   기준선을 잡으려면 사람이 콘솔에서 눈으로 읽어 `make_fixture --union` 으로
    #   옮겨적어야 했다. 옮겨적는 값은 오타 한 번에 기준선이 조용히 바뀐다.
    #   내접폭은 분위수로 남긴다 — 평균만으로는 `ST_MaximumInscribedCircle` 로
    #   바꿨을 때 꼬리만 달라지는 변화를 못 잡는다. 폭 필터가 후보 수를
    #   직접 좌우하므로(실측 66,915 → 59,989) 꼬리가 곧 결과다.
    spatial = {
        "exclusion_union_km2": (
            round(union.area / 1e6, 4) if union is not None else 0.0
        ),
        "shape_lift": not args.no_shape_lift,
    }
    if "내접폭" in pts.columns:
        _w = pts["내접폭"].to_numpy(dtype=float)
        _q = np.quantile(_w, [0.0, 0.05, 0.5, 0.95, 1.0])
        spatial["width_m"] = {
            "n": int(_w.size),
            "min": round(float(_q[0]), 4),
            "p05": round(float(_q[1]), 4),
            "median": round(float(_q[2]), 4),
            "p95": round(float(_q[3]), 4),
            "max": round(float(_q[4]), 4),
            # 합계는 분위수가 못 잡는 개별 행 변화를 잡는 체크섬 역할이다.
            "sum": round(float(_w.sum()), 4),
            "min_width": min_width,
            "pass_min_width": (
                int((_w >= min_width).sum()) if min_width is not None else None
            ),
        }

    # ── J 표출 산출물 ──
    if not args.no_export and args.select == "mclp":
        # 배제구역 셀도 점수를 갖는다 — "수요 최고인데 막힘"을 보여주기 위함
        gex = ~S.filter_outside(dgrid, union)
        EX.export_all(
            STEP4_OUTPUT_DIR,
            prefix,
            grid=dgrid,
            gscore=dscore,
            gexcluded=gex,
            spacing=args.demand_spacing,
            sel=sel,
            excl_layers=excl_layers,
            crs=pts.crs,
            domain=args.domain,
            facility=facility,
            ws=ws,
            params={
                "설치_소요_폭_m": min_width,
                "서비스_반경_m": r_cover,
                "최소_이격_m": d_min,
            },
            counts={
                "parcels": len(parcels),
                "points": len(pts),
                "survive": int(keep.sum()),
            },
            cov=cov_an,
            gap=gaps,
            spatial=spatial,
        )
        T.lap("J 표출 산출물")

    dst = args.out or os.path.join(STEP4_OUTPUT_DIR, f"{prefix}_topN_min.csv")
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    out = sel.drop(columns="geometry").copy()
    out["경도"] = wgs.geometry.x.to_numpy()
    out["위도"] = wgs.geometry.y.to_numpy()
    out.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"\n[저장] {dst}")
    T.lap("저장")
    T.report(import_sec=IMPORT_SEC, start=_T_START)


if __name__ == "__main__":
    main()
