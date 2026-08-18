# -*- coding: utf-8 -*-
"""
OmniSite 공간연산 격리층 (STEP 4)
==================================
순수 기하 연산만 둔다. **LLM·도메인 판단·파일 규약은 여기 넣지 않는다.**

이 파일이 따로 있는 이유
  전국 확장 시 PostGIS 로 갈아끼울 지점을 한 곳에 모아두기 위함이다.
  아래 함수들은 전부 PostGIS 쿼리와 1:1 대응된다.
      buffer_union   -> ST_Union(ST_Buffer(...))
      filter_outside -> NOT ST_Intersects / ST_Difference
      neighbors_within -> ST_DWithin + ST_Distance  (GiST 인덱스)
      nearest        -> <-> 연산자 (KNN)
  호출부(gam4_site_select)는 이 함수들의 시그니처만 알면 되므로,
  내부를 DB 쿼리로 바꿔도 상위 로직은 그대로다.

  ※ 용산구 단위 실측(44,459필지)에서는 GeoPandas 로 충분하다.
    로드 1.6s / 메모리 15MB / union+차집합 0.8s. DB 는 확장 시점 과제.

의존성: geopandas, shapely, numpy, pandas 만. (scipy 안 씀 — 원본 파일 방침)
"""

from __future__ import annotations

import os
import math

os.environ.setdefault("SHAPE_RESTORE_SHX", "YES")  # .shx 없을 때 복구

import numpy as np
import pandas as pd
import geopandas as gpd
import shapely
from shapely.ops import unary_union

try:
    from app.config import SPATIAL_CRS
except Exception:  # 단독 실행 폴백
    SPATIAL_CRS = 5186

WORK_CRS = SPATIAL_CRS  # 미터 단위 작업 좌표계 (거리·버퍼)

# 지적법 표준 지목 부호 28종. 여기 없는 코드는 unknown 으로 격리한다.
#   실측: 용산구 지적도에 '가' 7건이 섞여 있었다(JIBUN 이 "0-2 가" 형태).
#   정규식 매칭에 성공했다고 올바른 지목인 것은 아니므로 별도 검증이 필요하다.
STD_JIMOK = set("전답과목임광염대장학차주창도철제천구유양수공체원종사묘잡")


# =========================================================
# 로드 / 정리
# =========================================================
def clean_geometry(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """빈 geometry 제거. POINT EMPTY 는 isna() 로 안 잡히므로 is_empty 를 같이 본다."""
    return g[~g.geometry.is_empty & g.geometry.notna()].copy()


def load_parcels(
    path: str, encoding: str = "cp949", jibun_col: str = "JIBUN", verbose: bool = True
) -> gpd.GeoDataFrame:
    """연속지적도 SHP -> GeoDataFrame(WORK_CRS).

    추가 컬럼
      지목       : JIBUN 끝 한글 (표준 부호 아니면 None)
      지목_raw   : 추출된 원문 (검증 전)
      면적       : m² (WORK_CRS 기준)

    ※ 지목 '판정'(설치 가능/불가)은 여기서 하지 않는다 — LLM 소관(gam4_jimok).
      여기서는 추출과 표준 부호 검증까지만 한다.
    """
    if not os.path.exists(path):
        if verbose:
            print(f"  ⚠ 연속지적도 파일 없음({path}) — 스텁 필지 생성")
        from shapely.geometry import Polygon
        poly1 = Polygon([(198000, 450000), (198500, 450000), (198500, 450500), (198000, 450500)])
        poly2 = Polygon([(198600, 450600), (199000, 450600), (199000, 451000), (198600, 451000)])
        g = gpd.GeoDataFrame(
            {"PNU": ["1120010100100010001", "1120010100100020002"], "JIBUN": ["100-1대", "102-3잡"], "geometry": [poly1, poly2]},
            crs=WORK_CRS,
        )
    else:
        g = gpd.read_file(path, encoding=encoding)
    if g.crs is None:
        if verbose:
            print(f"  ⚠ CRS 없음 — EPSG:{WORK_CRS} 로 가정")
        g = g.set_crs(WORK_CRS)
    g = g.to_crs(WORK_CRS)

    n0 = len(g)
    g = clean_geometry(g).reset_index(drop=True)

    if jibun_col in g.columns:
        raw = g[jibun_col].astype(str).str.extract(r"([가-힣]+)$")[0]
        g["지목_raw"] = raw
        g["지목"] = raw.where(raw.isin(STD_JIMOK))
    else:
        g["지목_raw"] = None
        g["지목"] = None
        if verbose:
            print(f"  ⚠ '{jibun_col}' 컬럼 없음 — 지목 추출 생략")

    g["면적"] = g.geometry.area

    if verbose:
        bad = int(g["지목"].isna().sum())
        print(
            f"  [지적도] {len(g):,}필지 (빈 geom {n0 - len(g)}건 제거) "
            f"CRS=EPSG:{WORK_CRS}  면적합 {g['면적'].sum() / 1e6:.2f}km²"
        )
        if bad:
            odd = sorted(set(g.loc[g["지목"].isna(), "지목_raw"].dropna()))
            print(f"  ⚠ 표준 지목 부호 아님 {bad}건 → 지목=None 격리: {odd}")
    return g


# =========================================================
# 필지 형상 — 부스가 물리적으로 들어가는가
# =========================================================
def inscribed_width(gdf: gpd.GeoDataFrame, tol: float = 0.05) -> pd.Series:
    """필지별 **최대 내접원 지름**(m). 부스가 들어갈 수 있는 최대 폭.

    면적이 아니라 폭으로 봐야 하는 이유: 200m² 필지라도 폭 1m 띠 모양이면
    부스가 못 들어간다. 면적 필터로는 절대 걸러지지 않는다.

    shapely>=2.1 의 maximum_inscribed_circle 을 쓰고, 없으면 음수 버퍼 이분탐색.
    """
    if hasattr(shapely, "maximum_inscribed_circle"):
        try:
            lines = shapely.maximum_inscribed_circle(gdf.geometry.values, tolerance=tol)
            return pd.Series(shapely.length(lines) * 2.0, index=gdf.index)
        except TypeError:  # tolerance 인자 없는 버전
            lines = shapely.maximum_inscribed_circle(gdf.geometry.values)
            return pd.Series(shapely.length(lines) * 2.0, index=gdf.index)
        except Exception:
            pass  # 폴백으로 진행
    return _inscribed_by_buffer(gdf, tol=tol)


def _inscribed_by_buffer(
    gdf: gpd.GeoDataFrame, tol: float = 0.05, iters: int = 24
) -> pd.Series:
    """폴백: 음수 버퍼 이분탐색. buffer(-r) 이 비지 않으면 반지름 r 원이 들어간다."""
    geom = gdf.geometry
    hi = np.sqrt(np.maximum(geom.area.to_numpy(), 0.0) / math.pi)  # 상한(같은 면적 원)
    lo = np.zeros(len(gdf))
    for _ in range(iters):
        if np.all(hi - lo < tol):
            break
        mid = (lo + hi) / 2.0
        ok = ~gpd.GeoSeries(shapely.buffer(geom.values, -mid), crs=geom.crs).is_empty
        lo = np.where(ok, mid, lo)
        hi = np.where(ok, hi, mid)
    return pd.Series(lo * 2.0, index=gdf.index)


# =========================================================
# 후보점 생성
# =========================================================
def points_in_parcels(
    parcels: gpd.GeoDataFrame,
    spacing: float,
    max_per_parcel: int | None = 400,
    verbose: bool = True,
) -> gpd.GeoDataFrame:
    """필지 내부에 spacing 간격 격자점을 찍는다. 점이 하나도 없는 필지는 대표점 1개.

    전역 격자를 만들어 필지와 공간조인하는 방식 — 필지마다 루프를 돌지 않는다.
    격자가 전역 격자에 정렬되므로 인접 필지 간 점 간격이 들쭉날쭉하지 않다.

    반환: GeoDataFrame(geometry=Point, parcel_idx=원본 필지 index, from_rep=대표점여부)

    ※ 실측: 용산 필지 중앙값 면적이 대 97m²·도 42m² 라, 대부분 필지에는
      10m 격자가 하나도 안 들어간다 → 대표점 1개가 기본이 된다.
      큰 필지만 여러 점을 갖는 '면적 비례 해상도'가 자동으로 나온다.

    max_per_parcel: 초대형 필지의 점 수 상한(계산 폭주 방어). None 이면 무제한.
      상한을 넘으면 그 필지만 균등 솎아낸다. Top-N 은 최댓값 기준이라 영향 미미.
    """
    if parcels.empty:
        return gpd.GeoDataFrame(
            {"parcel_idx": [], "from_rep": []}, geometry=[], crs=parcels.crs
        )

    minx, miny, maxx, maxy = parcels.total_bounds
    xs = np.arange(minx + spacing / 2, maxx, spacing)
    ys = np.arange(miny + spacing / 2, maxy, spacing)
    if len(xs) == 0 or len(ys) == 0:
        lat = gpd.GeoDataFrame(geometry=[], crs=parcels.crs)
    else:
        gx, gy = np.meshgrid(xs, ys)
        lat = gpd.GeoDataFrame(
            geometry=gpd.points_from_xy(gx.ravel(), gy.ravel()), crs=parcels.crs
        )

    hit = gpd.GeoDataFrame(
        {"parcel_idx": [], "from_rep": []}, geometry=[], crs=parcels.crs
    )
    if len(lat):
        j = gpd.sjoin(lat, parcels[["geometry"]], how="inner", predicate="within")
        if len(j):
            hit = gpd.GeoDataFrame(
                {"parcel_idx": j["index_right"].to_numpy(), "from_rep": False},
                geometry=j.geometry.values,
                crs=parcels.crs,
            )

    # 상한 초과 필지 솎기
    if max_per_parcel and len(hit):
        cnt = hit.groupby("parcel_idx").size()
        over = cnt[cnt > max_per_parcel].index
        if len(over):
            keep = []
            for pid, grp in hit[hit["parcel_idx"].isin(over)].groupby("parcel_idx"):
                step = math.ceil(len(grp) / max_per_parcel)
                keep.append(grp.iloc[::step])
            hit = pd.concat([hit[~hit["parcel_idx"].isin(over)]] + keep)
            hit = gpd.GeoDataFrame(hit, geometry="geometry", crs=parcels.crs)

    # 점이 없는 필지 -> 대표점(centroid 아님. 오목한 필지에서 centroid 는 밖으로 나간다)
    covered = set(hit["parcel_idx"].tolist()) if len(hit) else set()
    missing = parcels.index.difference(pd.Index(sorted(covered)))
    if len(missing):
        rep = parcels.loc[missing].geometry.representative_point()
        rep_gdf = gpd.GeoDataFrame(
            {"parcel_idx": missing.to_numpy(), "from_rep": True},
            geometry=rep.values,
            crs=parcels.crs,
        )
        hit = pd.concat([hit, rep_gdf]) if len(hit) else rep_gdf
        hit = gpd.GeoDataFrame(hit, geometry="geometry", crs=parcels.crs)

    hit = hit.reset_index(drop=True)
    if verbose:
        nrep = int(hit["from_rep"].sum())
        print(
            f"  [후보점] {len(hit):,}점 / {len(parcels):,}필지 "
            f"(격자 {len(hit) - nrep:,} + 대표점 {nrep:,}, spacing={spacing:g}m)"
        )
    return hit


def make_grid(
    boundary, spacing: float, crs=None, verbose: bool = True
) -> gpd.GeoDataFrame:
    """표출용 전역 격자(셀 중심점). 히트맵 배경용 — 후보점과 별개다.

    boundary: GeoDataFrame | GeoSeries | shapely geometry
    반환: geometry(Point) + row/col (프런트에서 원점+간격으로 사각형 복원용)
    """
    if isinstance(boundary, (gpd.GeoDataFrame, gpd.GeoSeries)):
        crs = crs or boundary.crs
        geom = unary_union(boundary.geometry.values)
    else:
        geom = boundary
    minx, miny, maxx, maxy = geom.bounds
    xs = np.arange(minx + spacing / 2, maxx, spacing)
    ys = np.arange(miny + spacing / 2, maxy, spacing)
    gx, gy = np.meshgrid(xs, ys)
    ci, ri = np.meshgrid(np.arange(len(xs)), np.arange(len(ys)))

    pts = gpd.GeoDataFrame(
        {"row": ri.ravel(), "col": ci.ravel()},
        geometry=gpd.points_from_xy(gx.ravel(), gy.ravel()),
        crs=crs,
    )
    inside = pts.within(geom)
    out = pts[inside].reset_index(drop=True)
    out.attrs["origin"] = (float(minx), float(miny))
    out.attrs["spacing"] = float(spacing)
    out.attrs["cols"] = int(len(xs))
    out.attrs["rows"] = int(len(ys))
    if verbose:
        print(
            f"  [표출격자] {len(out):,}셀 ({len(xs)}×{len(ys)} 중 경계 내부, "
            f"spacing={spacing:g}m)"
        )
    return out


# =========================================================
# 배제
# =========================================================
def buffer_union(gdf: gpd.GeoDataFrame, radius: float | None):
    """레이어를 radius 만큼 버퍼해 union. radius=None 이면 원형상 그대로.

    ※ polygon 타입도 배제반경만큼 확장한다(설계 확정 Q-poly).
      4326 에서 buffer(30) 하면 30'도' 짜리 버퍼가 된다 — 반드시 미터 CRS 에서.
    """
    g = clean_geometry(gdf)
    if g.empty:
        return None
    geoms = g.geometry if not radius else g.geometry.buffer(float(radius))
    return unary_union(geoms.values)


def union_all(geoms: list):
    """여러 배제 union 을 하나로. None 은 무시."""
    valid = [x for x in geoms if x is not None]
    return unary_union(valid) if valid else None


def filter_outside(points: gpd.GeoDataFrame, exclusion) -> np.ndarray:
    """배제 영역 '밖' 인 점의 boolean mask. exclusion=None 이면 전부 True."""
    if exclusion is None:
        return np.ones(len(points), dtype=bool)
    return (~points.geometry.within(exclusion)).to_numpy()


# =========================================================
# 근접 질의
# =========================================================
def neighbors_within(
    cand: gpd.GeoDataFrame, targets: gpd.GeoDataFrame, radius: float, chunk: int = 20000
):
    """반경 내 (후보 위치인덱스, 대상 위치인덱스, 거리) 배열 3개.

    거리 감쇠 점수화의 입력. sjoin(predicate="dwithin") 으로 쌍을 구한 뒤
    거리를 벡터 계산한다. PostGIS 의 `ST_DWithin` 과 판정 기준이 같다(d <= radius).
    chunk 로 나누는 이유: 후보 13만 × 상권 R=250m 면 쌍이 2천만 개다.

    ※ 반환 인덱스는 **위치 기반(0..N-1)** 이다. 라벨 인덱스가 아니다.
    """
    c = cand.reset_index(drop=True)
    t = targets.reset_index(drop=True)
    if len(c) == 0 or len(t) == 0:
        e = np.array([], dtype=np.int64)
        return e, e, np.array([], dtype=float)

    cg = c.geometry
    if not (cg.geom_type == "Point").all():
        cg = cg.representative_point()
    CX, CY = cg.x.to_numpy(), cg.y.to_numpy()
    TX, TY = t.geometry.x.to_numpy(), t.geometry.y.to_numpy()

    # 결측·빈 기하는 여기서 잡는다. 그냥 두면 그 후보는 쌍을 하나도 못 만들면서
    # 오류도 안 나 **조용히 커버에서 빠진다** — 실제로 그 형태의 버그를 겪었다.
    n_bad = int(cg.isna().sum() + cg.is_empty.sum())
    if n_bad:
        raise RuntimeError(
            f"neighbors_within: 후보 기하 결측/빈 값 {n_bad}/{len(cg)} "
            f"— clean_geometry 를 먼저 적용하세요"
        )

    CI, TI, D = [], [], []
    tg = t[["geometry"]]  # _ci 컬럼 충돌 방지 + sjoin 부하 감소
    for st in range(0, len(c), chunk):
        seg = cg.iloc[st : st + chunk]  # CX/CY 와 동일한 기하(폴리곤이면 대표점)
        # ⚠ geometry 에 GeoSeries 를 넘기면 프레임 인덱스에 **정렬**된다.
        #   dict 로 만든 프레임의 인덱스는 항상 0..len-1 인데 seg 의 인덱스는
        #   st..st+len-1 이라, 두 번째 청크부터 geometry 가 전부 NaN 이 되고
        #   sjoin 이 경고 없이 0행을 반환한다(실측: 후보 57,530 중 앞 20,000만
        #   쌍을 가져 커버율이 64.3% 에서 정체). .values 로 정렬 자체를 끊는다.
        buf = gpd.GeoDataFrame(
            {"_ci": np.arange(st, st + len(seg))}, geometry=seg.values, crs=c.crs
        )
        # predicate="dwithin" 은 버퍼를 만들지 않고 거리로 직접 판정한다.
        #   구 방식(buffer(R) + "within")은 원을 64각형으로 **내접** 근사해
        #   0.9988R~R 구간 쌍을 놓쳤다(실측 0.13%). PostGIS 의 ST_DWithin 은
        #   정확하므로, 근사를 두면 DB 로 갈아끼울 때 답이 달라진다 —
        #   격리층의 전제(같은 답을 내는 교체 지점)가 깨진다.
        #   ※ geopandas>=0.14 + shapely>=2.0 필요.
        j = gpd.sjoin(tg, buf, how="inner", predicate="dwithin", distance=radius)
        if len(j) == 0:
            continue
        ci = j["_ci"].to_numpy()
        ti = j.index.to_numpy()
        CI.append(ci)
        TI.append(ti)
        D.append(np.hypot(TX[ti] - CX[ci], TY[ti] - CY[ci]))

    if not CI:
        e = np.array([], dtype=np.int64)
        return e, e, np.array([], dtype=float)
    return np.concatenate(CI), np.concatenate(TI), np.concatenate(D)


def decay_weights(
    dist: np.ndarray, radius: float, func: str = "gaussian", sigma_ratio: float = 1 / 3
) -> np.ndarray:
    """거리 -> 가중치. STEP3 build_matrix 와 **같은 식**이어야 한다.

    gaussian: exp(-d²/2σ²), σ = R*sigma_ratio.  σ=R/3 이면 d=R 에서 0.011.
    linear  : max(0, 1-d/R)
    """
    if func == "gaussian":
        s = radius * float(sigma_ratio)
        return np.exp(-(dist * dist) / (2.0 * s * s))
    if func == "linear":
        return np.maximum(0.0, 1.0 - dist / radius)
    raise ValueError(f"func 는 'gaussian'/'linear': {func}")


def nearest(
    cand: gpd.GeoDataFrame, targets: gpd.GeoDataFrame, max_distance: float | None = None
):
    """후보별 최근접 대상까지 (거리, 대상 위치인덱스). 없으면 (inf, -1).

    국유지 근접도 같은 '실행축' 필드용. 점수와 섞지 않는다(설계 확정 Q-feas).
    """
    c = cand.reset_index(drop=True)
    t = targets.reset_index(drop=True)
    d = np.full(len(c), np.inf)
    idx = np.full(len(c), -1, dtype=np.int64)
    if len(c) == 0 or len(t) == 0:
        return d, idx

    j = gpd.sjoin_nearest(
        c[["geometry"]],
        t[["geometry"]],
        how="left",
        distance_col="_d",
        max_distance=max_distance,
    )
    j = j[~j["index_right"].isna()]
    if len(j):
        j = j.sort_values("_d").groupby(level=0).first()  # 동거리 다중매칭 방어
        pos = j.index.to_numpy()
        d[pos] = j["_d"].to_numpy()
        idx[pos] = j["index_right"].to_numpy().astype(np.int64)
    return d, idx
