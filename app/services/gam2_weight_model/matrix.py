# -*- coding: utf-8 -*-
"""[B] 후보 N × 지표 K — 레이어 부착 · 행렬 · 정규화 · 희소 판정"""
from __future__ import annotations

import os
import time as _time
import numpy as np
import pandas as pd
import geopandas as gpd

from .state import ADMIN_CROSSWALK_PATH, SPARSE_THRESHOLD, WORK_CRS, _ADM_CODE_COL
from .util import _NORMALIZERS, _norm_none, _pick_value_cols
from .admin import _detect_admin_key_col, admin_names_to_codes, load_admin_crosswalk


# =========================================================
# [A2] 레이어 부착 — 각 지표에 실제 점/값 결합, kind 확정
# =========================================================
def as_geodataframe(df, did: str = "", verbose: bool = True):
    """좌표 컬럼이 있으면 GeoDataFrame(WORK_CRS) 으로. **좌표계는 값으로 판정한다.**

    왜 이름으로 단정하면 안 되는가 —
      'X좌표/Y좌표' 는 한국 공공데이터에서 대개 투영좌표(TM, EPSG:5174/5186/2097)다.
      값이 (198000, 451000) 형태인데 이를 EPSG:4326 으로 읽으면 좌표가 지구 밖으로
      나가고, to_crs 후 공간조인이 0건이 되어 **지표가 전부 0 이 된다.**
      예외도 경고도 없다 — 마포구 사건과 같은 실패 모드다.

    판정: 경도 124~132 · 위도 33~39 (한국 범위) 이면 4326.
          그 밖이면서 값이 크면 투영좌표로 보고 **중단**한다(추측하지 않는다).
    """
    from app.services.gam2_clean_data import _pick_lnglat  # 중복 정의 대신 재사용

    if hasattr(df, "geometry") and "geometry" in getattr(df, "columns", []):
        return df
    lng, lat = _pick_lnglat(df.columns)
    if not (lng and lat):
        return df

    x = pd.to_numeric(df[lng], errors="coerce")
    y = pd.to_numeric(df[lat], errors="coerce")
    ok = x.notna() & y.notna()
    if not ok.any():
        if verbose:
            print(f"  ⚠ [{did}] '{lng}/{lat}' 이 있으나 유효 좌표 0건 — 통계표로 처리")
        return df

    xs, ys = x[ok], y[ok]
    if xs.between(124, 132).mean() > 0.9 and ys.between(33, 39).mean() > 0.9:
        crs = 4326
    elif (xs.abs() > 1e4).mean() > 0.9:
        raise ValueError(
            f"[{did}] '{lng}/{lat}' 이 투영좌표로 보이나 CRS 를 알 수 없습니다 "
            f"(중앙값 {xs.median():,.0f}, {ys.median():,.0f}).\n"
            f"  EPSG:5174/5186/2097 등 원본 좌표계를 확인해 명시하세요.\n"
            f"  추측해서 4326 으로 읽으면 공간조인이 조용히 0건이 됩니다."
        )
    else:
        raise ValueError(
            f"[{did}] 좌표가 한국 범위를 벗어납니다 "
            f"(경도 중앙 {xs.median():.3f}, 위도 중앙 {ys.median():.3f}).\n"
            f"  컬럼 짝({lng}/{lat})이 뒤바뀌었는지 확인하세요."
        )

    g = gpd.GeoDataFrame(df[ok].copy(), geometry=gpd.points_from_xy(xs, ys), crs=crs)
    dropped = int((~ok).sum())
    if verbose:
        note = f", 좌표결측 {dropped:,}행 제외" if dropped else ""
        print(f"  ⓘ [{did}] parquet -> geometry 복원 ({lng}/{lat}, EPSG:{crs}{note})")
    return g.to_crs(WORK_CRS)


def attach_layers(
    indicators: list,
    loader,
    admin_value_col: str = "총생활인구수",
    admin_code_hint: str = "행정동코드",
    region: str = "",
    verbose: bool = True,
) -> None:
    """loader(dataset_id) -> GeoDataFrame|DataFrame (EPSG:5186 재투영은 loader 책임).
    각 지표에 _points/_valcol(point) 또는 _admin_agg(admin) 를 심고 kind 확정.
    """
    for i in indicators:
        g = loader(i["geo_dataset"])
        is_geo = hasattr(g, "geometry") and "geometry" in getattr(g, "columns", [])

        if not is_geo:  # 좌표 없는 통계표 -> admin
            i["kind"] = "admin"
            code_col = next((c for c in g.columns if admin_code_hint in str(c)), None)
            code_src, key_kind = "hint", "code"
            if code_col is None:
                # 이름 힌트 실패 -> 값으로 판정(코드/이름). 못 찾거나 애매하면 raise.
                code_col, key_kind, rate, _ = _detect_admin_key_col(g, i["id"])
                code_src = f"crosswalk_{key_kind}"
                if verbose:
                    print(
                        f"  [{i['id']}] 행정동 조인키 자동판정: '{code_col}' "
                        f"({key_kind}, 크로스워크 매칭 {rate:.0%})"
                    )
            i["_admin_code_source"] = code_src

            vcols = _pick_value_cols(g)
            if admin_value_col in g.columns:
                vcol = admin_value_col
            elif len(vcols) == 1:
                vcol = vcols[0]
            elif vcols:
                vcol = vcols[0]
                if verbose:
                    print(
                        f"  ⚠ [{i['id']}] 값 컬럼 후보 {vcols} 중 '{vcol}' 사용 — 확인 필요"
                    )
            else:
                raise ValueError(
                    f"[{i['id']}] 집계할 수치 컬럼이 없습니다.\n"
                    f"  컬럼: {[c for c in g.columns if c != 'geometry']}"
                )
            i["_admin_valcol_candidates"] = vcols

            g = g.copy()
            if key_kind == "name":
                # 이름 그대로 두면 build_matrix 의 코드 조인에서 0건이 된다 -> 코드로 변환
                g["_admcd"] = admin_names_to_codes(g[code_col], region, i["id"])
                g = g[g["_admcd"].notna()]
                code_col = "_admcd"
            g[vcol] = pd.to_numeric(g[vcol], errors="coerce")
            agg = g.groupby(code_col)[vcol].mean().reset_index()  # 시간대·일 평균
            i["_admin_agg"] = agg
            i["_admin_code_col"] = code_col
            i["_admin_valcol"] = vcol
            if verbose:
                print(
                    f"  [{i['id']}] admin  code={code_col} val={vcol} dongs={len(agg)}"
                )
            continue

        g = g[g.geometry.notna() & ~g.geometry.is_empty].copy()
        if i["val_dataset"]:  # 병합 point_sum
            val = loader(i["val_dataset"])
            nf = _NORMALIZERS.get(i["join"]["normalize"], _norm_none)
            vcols = _pick_value_cols(val)
            days = val["사용일자"].nunique() if "사용일자" in val.columns else 1
            val = val.copy()
            val["_k"] = val[i["join"]["val_key"]].map(nf)
            agg = val.groupby("_k")[vcols].sum().sum(axis=1) / max(days, 1)  # 일평균
            g["_k"] = g[i["join"]["geo_key"]].map(nf)
            g["_val"] = g["_k"].map(agg).fillna(0.0)
            i["kind"] = "point_sum"
            i["_valcol"] = "_val"
            i["_points"] = g[["_val", "geometry"]].copy()
            i["_days"] = int(days)
            if verbose:
                print(
                    f"  [{i['id']}] point_sum  pts={len(g)} days={days} vcols={vcols}"
                )
        else:  # 단독 점 -> 개수
            i["kind"] = "point_count"
            i["_points"] = g[["geometry"]].copy()
            if verbose:
                print(f"  [{i['id']}] point_count  pts={len(g)}")


# =========================================================
# [B] 지표 행렬 — 후보 N × 지표 K
# =========================================================
def build_matrix(
    candidates: gpd.GeoDataFrame,
    indicators: list,
    radius_m: dict,
    admin_gdf: gpd.GeoDataFrame = None,
    default_radius: float = 150.0,
    verbose: bool = True,
    decay: str | None = None,
    sigma_ratio: float = 1 / 3,
    chunk: int = 20000,
) -> pd.DataFrame:
    """후보 × 지표 행렬(원자료, 미정규화). 모두 EPSG:5186 가정.

    decay
      None       : 반경 안이면 1 (기존 동작 — 하위호환)
      "gaussian" : exp(-d²/2σ²),  σ = R * sigma_ratio
      "linear"   : max(0, 1 - d/R)

    sigma_ratio
      σ = R/3 (기본). R 경계에서 가중치 0.011 로 매끄럽게 소멸한다.
      R/2 로 하면 경계에서 0.14 가 남아 불연속이 생긴다.
      ※ σ 를 R 에서 파생시키므로 R 의 HITL 확정 근거를 그대로 상속한다.
        도메인이 바뀌어 R 이 달라지면 σ 도 자동으로 따라간다(하드코딩 아님).

    chunk
      후보를 나눠 처리(메모리 상한). 감쇠 모드에서만 의미.
      후보 13만 × 상권 R=250m 면 쌍이 2천만 개가 되므로 한 번에 올리지 않는다.
    """
    cand = candidates.reset_index(drop=True).copy()
    cand["_cid"] = range(len(cand))
    mat = pd.DataFrame({"_cid": cand["_cid"]})

    # 후보 중심좌표 — _cid 가 0..N-1 이라 위치 인덱스로 바로 접근 가능
    _cgeom = cand.geometry
    if not (_cgeom.geom_type == "Point").all():  # 폴리곤이 오면 내부 대표점
        _cgeom = _cgeom.representative_point()
    CX = _cgeom.x.to_numpy()
    CY = _cgeom.y.to_numpy()

    cand_admcd = None
    if admin_gdf is not None and any(i["kind"] == "admin" for i in indicators):
        jn = gpd.sjoin(
            cand[["_cid", "geometry"]],
            admin_gdf[[_ADM_CODE_COL, "geometry"]],
            how="left",
            predicate="within",
        )
        cand_admcd = jn.groupby("_cid")[_ADM_CODE_COL].first()

    for i in indicators:
        iid = i["id"]
        _t0 = _time.perf_counter()

        # ---- admin 지표: 반경 개념이 없어 감쇠와 무관 (기존 로직 그대로) ----
        if i["kind"] == "admin":
            if cand_admcd is None:
                raise ValueError(
                    f"[{iid}] admin 지표엔 admin_gdf(행정동 경계)가 필요합니다."
                )
            agg = i["_admin_agg"]
            ccol = i["_admin_code_col"]
            vcol = i["_admin_valcol"]
            amap = dict(zip(agg[ccol].astype(str), agg[vcol]))

            codes = cand["_cid"].map(cand_admcd)
            uniq = [str(c) for c in pd.unique(codes.dropna())]
            xw = load_admin_crosswalk()
            x2a = dict(zip(xw["행정구역코드"], xw["행정동코드8"]))
            x2s = dict(zip(xw["행정구역코드"], xw["시군구명"]))
            x2d = dict(zip(xw["행정구역코드"], xw["행정동명"]))

            # 코드 종류는 보통 수십 개다 — 코드 단위로 풀고 후보엔 map 으로 붙인다.
            code_val, no_xwalk, no_value = {}, [], []
            for c in uniq:
                if c in amap:  # 같은 코드 체계
                    code_val[c] = amap[c]
                    continue
                t = x2a.get(c)  # 코드 체계 변환
                if t is None:
                    no_xwalk.append(c)
                elif t in amap:
                    code_val[c] = amap[t]
                else:
                    no_value.append(c)

            # 미매칭을 0 으로 덮으면 '데이터 없음'이 '값 0'이 된다 — 조용한 왜곡.
            #   ① 크로스워크에 없는 코드      -> 참조표가 낡음. 무조건 중단.
            #   ② 변환은 됐는데 집계에 값 없음 -> 분석 대상 시군구면 결손(중단),
            #      다른 시군구면 경계에 걸친 이웃 지역이라 정상(경고).
            #   대상 시군구는 매칭된 코드의 최빈값으로 정한다(지역명 하드코딩 없음).
            cnt = codes.astype(str).value_counts()
            main_sgg = None
            if code_val:
                s = pd.Series([x2s.get(c) for c in code_val]).dropna()
                main_sgg = s.mode().iloc[0] if len(s) else None
            fatal = list(no_xwalk) + [c for c in no_value if x2s.get(c) == main_sgg]
            warn = [c for c in no_value if x2s.get(c) != main_sgg]

            if fatal:
                n = int(sum(cnt.get(c, 0) for c in fatal))
                det = "\n".join(
                    f"    {c}  {x2s.get(c, '?')} {x2d.get(c, '?')}  후보 {cnt.get(c, 0):,}점"
                    f"  {'크로스워크 없음' if c in no_xwalk else '집계 테이블에 값 없음'}"
                    for c in sorted(fatal)
                )
                raise ValueError(
                    f"[{iid}] 행정동 매칭 실패 — 후보 {n:,}/{len(cand):,} "
                    f"({n / len(cand) * 100:.1f}%)\n{det}\n"
                    f"  대상 시군구: {main_sgg}   집계 테이블 코드 {len(amap)}종\n"
                    f"  0 으로 채우면 해당 동 후보가 이 지표에서 구조적으로 불리해집니다."
                )

            vals = codes.astype(str).map(code_val)
            mat[iid] = vals.fillna(0.0).values
            if verbose:
                hit = int((mat[iid] > 0).sum())
                mv = mat[iid][mat[iid] > 0]
                _el = _time.perf_counter() - _t0
                print(
                    f"  [{iid}] admin  hit={hit}/{len(cand)}  mean={mv.mean():,.0f}"
                    f"  [{_el:.1f}s]"
                    if len(mv)
                    else f"  [{iid}] admin hit=0  [{_el:.1f}s]"
                )
                print(
                    f"         행정동 {len(code_val)}종 매칭 "
                    f"({main_sgg}) · 크로스워크 {os.path.basename(ADMIN_CROSSWALK_PATH)}"
                )
                for c in warn:
                    print(
                        f"         ⓘ {c} {x2s.get(c, '?')} {x2d.get(c, '?')} "
                        f"후보 {cnt.get(c, 0):,}점 — 대상 시군구 밖이라 0 처리"
                    )
            continue

        R = float(radius_m.get(iid) or default_radius)
        pts = i["_points"]

        # ---- 감쇠 OFF: 기존 경로 100% 동일 ----
        if decay is None:
            buf = cand[["_cid"]].copy()
            buf["geometry"] = cand.geometry.buffer(R)
            buf = gpd.GeoDataFrame(buf, geometry="geometry", crs=cand.crs)
            j = gpd.sjoin(pts, buf, how="inner", predicate="within")
            if i["kind"] == "point_count":
                s = j.groupby("_cid").size()
            else:
                s = j.groupby("_cid")[i["_valcol"]].sum()
            mat[iid] = mat["_cid"].map(s).fillna(0.0).values

        # ---- 감쇠 ON: 반경 내 쌍을 구한 뒤 거리로 가중 ----
        else:
            acc = np.zeros(len(cand), dtype=float)
            _pts = pts.reset_index(drop=True)  # 위치 인덱스 보장
            if len(_pts):
                sigma = R * float(sigma_ratio)
                _pg = _pts.geometry
                PX = _pg.x.to_numpy()
                PY = _pg.y.to_numpy()
                PV = (
                    _pts[i["_valcol"]].to_numpy(dtype=float)
                    if i["kind"] == "point_sum"
                    else None
                )

                for st in range(0, len(cand), chunk):
                    sl = cand.iloc[st : st + chunk]
                    buf = sl[["_cid"]].copy()
                    buf["geometry"] = sl.geometry.buffer(R)
                    buf = gpd.GeoDataFrame(buf, geometry="geometry", crs=cand.crs)
                    j = gpd.sjoin(_pts, buf, how="inner", predicate="within")
                    if len(j) == 0:
                        continue
                    cid = j["_cid"].to_numpy()
                    pi = j.index.to_numpy()  # _pts 위치 인덱스
                    d = np.hypot(PX[pi] - CX[cid], PY[pi] - CY[cid])
                    if decay == "gaussian":
                        w = np.exp(-(d * d) / (2.0 * sigma * sigma))
                    elif decay == "linear":
                        w = np.maximum(0.0, 1.0 - d / R)
                    else:
                        raise ValueError(
                            f"decay 는 None/'gaussian'/'linear' 중 하나: {decay}"
                        )
                    if PV is not None:
                        w = w * PV[pi]
                    acc += np.bincount(cid, weights=w, minlength=len(cand))
            mat[iid] = acc

        if verbose:
            hit = int((mat[iid] > 0).sum())
            tag = i["kind"] if decay is None else f"{i['kind']}~{decay[:4]}"
            print(
                f"  [{iid}] {tag:<16} R={R:>4.0f}m  hit={hit}/{len(cand)} "
                f"({hit / len(cand) * 100:.0f}%)  max={mat[iid].max():,.1f}"
                f"  [{_time.perf_counter() - _t0:.1f}s]"
            )

    return mat.drop(columns="_cid")


# =========================================================
# [B2] 정규화
# =========================================================
def normalize_matrix(
    mat: pd.DataFrame, indicators: list, scale: str = "minmax"
) -> pd.DataFrame:
    """지표 행렬 정규화. 방향(benefit/cost)에 따라 부호를 뒤집는다.

    scale
      "minmax" : (x-lo)/(hi-lo). 수요가 지표값에 **선형** 비례한다는 가정.
      "log"    : log1p 를 취한 뒤 min-max. **체감** 비례 가정.

    정규화는 '문제 해결'이 아니라 '가정 선택'이다. 어느 쪽이 맞는지는
    데이터가 아니라 도메인 판단이 정한다.

    log 가 필요해지는 상황 — 실측(용산 흡연, 후보 66,915점):
        지표      max/p99   상위1%비중
        06+03       6.3       40.1%     <- 서울·용산역이 지배
        07+02       3.6       20.8%
      min-max 에서는 서울역(승하차 25만)이 1.00 을 가져가고 중형역(2만)이
      0.08 로 눌린다. 흡연 수요가 승하차에 12배 비례한다고 보긴 어렵다.
      log 면 0.80 대 0.62 로 완만해진다.

    ※ 이 값을 바꾸면 CRITIC 도 바뀐다(지표 분산이 달라지므로).
      STEP3 를 다시 실행해 weight_set 을 갱신해야 한다. 감쇠 때와 같은 구조.
    """
    if scale not in ("minmax", "log"):
        raise ValueError(f"scale 은 'minmax'/'log': {scale}")
    dir_by = {i["id"]: i["direction"] for i in indicators}
    out = pd.DataFrame(index=mat.index)
    for c in mat.columns:
        x = mat[c].astype(float)
        if scale == "log":
            # 음수는 log1p 가 정의되지 않는다. 지표값은 개수·합이라 음수가
            # 나올 수 없지만, cost 지표를 원자료로 넘기는 실수를 대비해 막는다.
            if (x < 0).any():
                raise ValueError(f"[{c}] 음수 값에는 log 스케일을 쓸 수 없습니다.")
            x = np.log1p(x)
        lo, hi = x.min(), x.max()
        if hi - lo < 1e-12:
            out[c] = 0.0
        elif dir_by.get(c) == "cost":
            out[c] = (hi - x) / (hi - lo)
        else:
            out[c] = (x - lo) / (hi - lo)
    return out


# =========================================================
# [D] CRITIC (Spearman) + 희소 제외 + 부트스트랩
# =========================================================
def detect_sparse(
    mat: pd.DataFrame,
    indicators: list | None = None,
    threshold: float = SPARSE_THRESHOLD,
) -> set:
    """희소 = '후보를 갈라놓는 정보가 거의 없음' → CRITIC 에서 제외.

    benefit : 값>0 인 후보가 거의 없으면 신호 없음.
    cost    : 값=0 이 '좋음'이라 의미가 뒤집힌다.
              감점 대상이 거의 없거나(nz<θ) 거의 전부(nz>1-θ) 면 변별력이 없다.

    indicators 를 주지 않으면 기존 동작(방향 무시)으로 폴백한다.

    ※ 배경: 원래는 (mat[c]>0).mean() < θ 하나뿐이었다. benefit 에는 맞지만
      cost 지표에서는 '감점 대상이 적다'를 '정보가 없다'로 오판한다.
      용산 어린이집 규모로 모사하면 비영 비율이 7.2% 로 임계 5% 를 겨우
      넘긴다 — 밀도가 조금만 낮은 지역·도메인이면 sparse_excluded=true 라는
      정상적으로 보이는 라벨을 달고 조용히 빠진다.
    """
    if indicators is None:
        return {c for c in mat.columns if (mat[c] > 0).mean() < threshold}

    dir_by = {i["id"]: i.get("direction", "benefit") for i in indicators}
    out = set()
    for c in mat.columns:
        nz = (mat[c] > 0).mean()
        if dir_by.get(c) == "cost":
            if nz < threshold or nz > 1 - threshold:
                out.add(c)
        elif nz < threshold:
            out.add(c)
    return out
