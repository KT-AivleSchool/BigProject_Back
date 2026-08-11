# -*- coding: utf-8 -*-
"""
OmniSite 배제구역 점/면 판정 — 지목 배수(lift) 결정론화  (S9)
================================================================
배제 레이어가 **점 시설**(좌표+이격거리)인지 **면 구역**(부지 전체+이격)인지
LLM 이 아니라 **데이터가** 정하게 한다.

왜 필요한가 — 두 가지가 동시에 깨져 있었다
  ① 감리 오판   프롬프트에 `어린이집 30m` 가 radius 예시로 박혀 있는데도
                 어린이집을 `polygon` 으로 냈다. 프롬프트 강화로는 못 고친다.
  ② 실행 수단   면으로 판정해도 우리가 가진 건 점 좌표뿐이라
                 `buffer_union(g, None)` = **면적 0** = 배제 소멸. 경고도 예외도 없었다.
                 실측: 흡연 `11 어린이보호구역` 31점이 0.0000 km² 로 조용히 사라짐.

판정 원리
  배수 = (이 레이어의 점이 그 지목에 떨어진 비율) / (그 지목의 **면적** 비율)

  기저율이 왜 면적인가 — 묻는 것이 *"이 점이 우연히 이 지목에 떨어질 확률"* 이므로
  귀무가설은 "점을 공간에 무작위로 뿌린다" 다. 개수 비율은 *"필지 하나를 무작위로
  고른다"* 는 **다른 질문**이다. 용산구 `천`(하천)은 개수 1.3% / 면적 21.8% 라 갈린다.

  기저율을 업로드된 지적도에서 계산하므로 **지자체가 바뀌면 자동 재보정**된다.
  지목 이름·시설 이름·도메인·법령 값이 이 파일에 하나도 없다.

**레이어 전체가 아니라 지목별로 판정한다.**
  흡연 `01 금연구역` 한 레이어에 학교절대보호구역(면)·공원내(면)·광장(점)·통학로(점)이
  섞여 있다. 레이어 단위로 하나의 답을 내면 반드시 한쪽이 틀린다.

임계 3개 — 서로 다른 실패를 막는다
  배수 ≥ 10x    무작위 분포와 구분. 이것만으로는 부족하다.
  관측 ≥ 10%    소수 예외의 승격을 막는다. **이게 없으면 오탐이 난다** —
                 어린이집 173점 중 7점(4.0%)이 종교용지(교회 부설)에 떨어져
                 배수 10.6x 가 나온다. 면으로 처리하면 교회 부지 전체가 배제된다.
  표본 ≥ 3점    한 점이 희귀 지목에 떨어져 배수가 튀는 것을 막는다.

실측 (2026-08-03 · 용산구 44,452필지 · 흡연 배제 5레이어)

  레이어            지목  점수  관측%   배수    판정   데이터팀 수동판단
  01 금연구역        학    30  33.3   13.8x   면     학교 폴리곤50m  ✅
  01 금연구역        공    25  27.8   30.6x   면     공원 폴리곤0m   ✅
  01 금연구역        대    24  26.7    0.6x   점     광장 점0m       ✅
  05 어린이집        대   156  90.2    2.0x   점     어린이집 점50m  ✅
  05 어린이집        종     7   4.0   10.6x   점 ←관측하한이 막음
  06 지하철역        도    10  58.8    5.1x   점     지하철 점10m    ✅
  06 지하철역        철     7  41.2    7.5x   점
  07 버스정류소      도   175  55.7    4.8x   점     버스 점10m      ✅
  11 어린이보호구역  학    12  38.7   16.1x   면
  11 어린이보호구역  종     2   6.5   16.8x   점 ←표본하한이 막음

  면 최소 13.8x / 점 최대 7.5x — 임계 10x 는 양쪽에서 여유가 있다.
  데이터팀 수동 판단(`_v4/01.정제데이터/08.용산구_전체_흡연구역_폴리곤.csv`)과 **5/5 일치**.
  서로 다른 경로(통계 vs 도메인 지식)로 같은 답에 도달했다.

⚠️ 임계값은 용산구 1개 도메인 관찰값이다. 재활용·EV 로 검증되지 않았다.
   특히 EV 는 `차`(주차장) 기저율이 0.09% 로 낮아 배수가 과대평가될 수 있다.
   `--lift`·`--share`·`--min-pts` 로 조정 가능하게 두고, 판정 근거를 전부 산출물에 남긴다.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

# 임계 — 도메인 값이 아니라 **통계 판정 파라미터**다(하드코딩 금지 대상이 아님).
# 근거는 위 실측표. 호출부에서 덮어쓸 수 있다.
LIFT_MIN = 10.0  # 배수
SHARE_MIN = 0.10  # 관측 비율
COUNT_MIN = 3  # 최소 표본 점 수
GAP_M = 1.0  # 인접 판정 여유 — 지적도 슬리버(경계 미세 틈) 보정
MAX_ITER = 50  # 인접 확장 반복 상한 (폭주 방어)


# =========================================================
# 기저율 · 배수
# =========================================================
def area_base_rate(parcels: gpd.GeoDataFrame) -> dict:
    """지목 -> 면적 비율. 지목 None(표준 부호 아님)은 제외한다.

    parcels 는 **전체 연속지적도**여야 한다. 후보 필지(설치 가능 지목만 남긴 것)를
    넣으면 `학`·`천` 이 통째로 빠져 기저율이 왜곡되고 배수가 무의미해진다.
    """
    g = parcels[parcels["지목"].notna()]
    if g.empty:
        raise ValueError("지목이 있는 필지가 없습니다 — 지적도를 확인하세요.")
    area = g.geometry.area if "면적" not in g.columns else g["면적"]
    s = area.groupby(g["지목"]).sum()
    return (s / s.sum()).to_dict()


def match_parcels(points: gpd.GeoDataFrame, parcels: gpd.GeoDataFrame):
    """점 -> 필지 index. 반환 Series(index=점 index, value=필지 index 또는 NA).

    경계 위 점이 두 필지에 매칭되면 첫 건만 남긴다 — 그대로 두면 배수 분모가
    점 수보다 커져 관측 비율이 1을 넘는다.
    """
    if points.crs != parcels.crs:
        points = points.to_crs(parcels.crs)
    j = gpd.sjoin(
        points[["geometry"]], parcels[["geometry"]], how="left", predicate="within"
    )
    return j[~j.index.duplicated()]["index_right"]


def lift_table(
    points: gpd.GeoDataFrame,
    parcels: gpd.GeoDataFrame,
    base: dict | None = None,
    matched=None,
) -> list[dict]:
    """지목별 [{지목, 점수, 관측, 기저, 배수}] + 미매칭 행(지목=None).

    배수는 관측/기저다. 기저가 0인 지목(지적도에 없는데 점이 떨어질 수는 없다)은
    나타나지 않는다.
    """
    base = base or area_base_rate(parcels)
    m = match_parcels(points, parcels) if matched is None else matched
    n = len(points)
    if n == 0:
        return []

    jm = pd.Series(
        parcels["지목"].reindex(m.dropna()).to_numpy(), index=m.dropna().index
    )
    rows = []
    for j, c in jm.value_counts().items():
        b = base.get(j, 0.0)
        rows.append(
            {
                "지목": j,
                "점수": int(c),
                "관측": c / n,
                "기저": b,
                "배수": (c / n) / b if b > 0 else float("inf"),
            }
        )
    n_un = int(m.isna().sum())
    if n_un:
        rows.append(
            {"지목": None, "점수": n_un, "관측": n_un / n, "기저": None, "배수": None}
        )
    return sorted(rows, key=lambda r: -r["점수"])


def classify(
    rows: list[dict],
    lift_min: float = LIFT_MIN,
    share_min: float = SHARE_MIN,
    count_min: int = COUNT_MIN,
) -> list[dict]:
    """lift_table 행에 판정(`면`/`점`)·사유·`확인요청` 을 붙인다.

    미매칭(지목=None)은 항상 점이다 — 필지 밖(도로 위 등)이라 복원할 부지가 없다.

    `확인요청` — 사람이 봐야 하는 행을 고른다. 임계값 자체가 용산구 1개 도메인
    관찰값이라 코드가 단독으로 확정할 근거가 없다(절대원칙 3). 다만 **전 행을 다
    올리면 노이즈**라(버스정류소만 15행) 두 부류로 한정한다.

      ① 면 판정 전부 — 진폭이 크다. 점 하나가 필지 전체 + 인접확장까지 배제한다
                        (실측 01 금연구역 0.0245 → 0.6325 km², 26배).
      ② 점 판정 중 배수 >= lift_min — 집중 신호는 이미 넘겼는데 관측·표본에 막힌 행.
                        실측 05 어린이집 `종` 10.6x/4.0%(교회 부설)가 여기 걸린다.
                        관측 하한이 없었으면 교회 필지 전체를 배제할 뻔했다.

    🔴 **잡지 못하는 것**: 배수가 임계 미달인 행은 관측이 아무리 높아도 안 올라간다
    (06 지하철역 `철` 7.5x/41.2%). 배수 근처 밴드를 두려면 새 상수가 필요한데
    그것 역시 근거 없는 도메인 값이라(절대원칙 2) 두지 않았다. 이 방향의 오판
    (면인데 점으로 판정 = 과소배제)은 **현재 검출되지 않는다.**
    """
    for r in rows:
        r["확인요청"] = False
        if r["지목"] is None:
            r["판정"], r["사유"] = "점", "필지 밖"
            continue
        if r["점수"] < count_min:
            r["판정"], r["사유"] = "점", f"표본 {r['점수']}<{count_min}"
        elif r["관측"] < share_min:
            r["판정"], r["사유"] = "점", f"관측 {r['관측']:.1%}<{share_min:.0%}"
        elif r["배수"] < lift_min:
            r["판정"], r["사유"] = "점", f"배수 {r['배수']:.1f}<{lift_min:g}"
        else:
            r["판정"], r["사유"] = "면", f"배수 {r['배수']:.1f}x · 관측 {r['관측']:.0%}"

        if r["판정"] == "면":
            r["확인요청"] = True
        elif r["배수"] is not None and r["배수"] >= lift_min:
            r["확인요청"] = True
    return rows


def review_requests(
    rows: list[dict],
    lift_min: float = LIFT_MIN,
    share_min: float = SHARE_MIN,
    count_min: int = COUNT_MIN,
) -> list[dict]:
    """`확인요청` 행을 프런트가 그대로 렌더할 수 있는 형태로 뽑는다.

    임계값을 함께 실어 보낸다 — 값만 보면 왜 그 판정인지 사람이 되짚을 수 없다.
    """
    return [
        {
            "지목": r["지목"],
            "점수": r["점수"],
            "관측": round(r["관측"], 4),
            "기저": round(r["기저"], 6) if r["기저"] is not None else None,
            "배수": round(r["배수"], 2) if r["배수"] is not None else None,
            "판정": r["판정"],
            "사유": r["사유"],
            "임계": {"배수": lift_min, "관측": share_min, "표본": count_min},
        }
        for r in rows
        if r.get("확인요청")
    ]


# =========================================================
# 인접 동일지목 확장 — 부지 복원
# =========================================================
def expand_adjacent(
    parcels: gpd.GeoDataFrame, seed_idx, gap: float = GAP_M, max_iter: int = MAX_ITER
) -> tuple[list, int]:
    """시드 필지에서 **같은 지목이면서 gap 이내로 닿는** 필지를 반복 흡수.

    지목 하나가 여러 필지로 쪼개진 것을 원래 부지로 되돌린다
    (용산구 지목 `학` 114필지 = 실제 학교 부지 25덩어리, 중앙값 369 m²).

    같은 지목만 타므로 도로·대지로 새지 않는다 — 실측 과다배제 0%.
    도로가 끼면 끊기는데 그게 맞는 동작이다(길 건너는 다른 부지).

    반환 (확장 후 필지 index 목록, 반복 횟수)
    """
    seed = list(seed_idx)
    if not seed:
        return [], 0
    out, iters = set(seed), 0
    for jm, grp in parcels.loc[seed].groupby("지목"):
        pool = parcels[parcels["지목"] == jm]
        frontier = set(grp.index)
        for _ in range(max_iter):
            iters += 1
            f = pool.loc[sorted(frontier), ["geometry"]]
            j = gpd.sjoin(
                pool[["geometry"]], f, how="inner", predicate="dwithin", distance=gap
            )
            new = set(j.index) - out
            if not new:
                break
            out |= new
            frontier = new
    return sorted(out), iters


# =========================================================
# 통합
# =========================================================
def resolve(
    points: gpd.GeoDataFrame,
    parcels: gpd.GeoDataFrame,
    radius: float | None,
    base: dict | None = None,
    lift_min: float = LIFT_MIN,
    share_min: float = SHARE_MIN,
    count_min: int = COUNT_MIN,
    expand: bool = True,
) -> dict:
    """배제 레이어 하나 -> 배제 geometry + 판정 근거.

    면 지목에 떨어진 점은 **그 필지(+인접 확장)** 로, 나머지 점은 **점 그대로**
    다루고, 양쪽 모두에 법정 이격(radius)을 건다.

    반환 dict
      geom            배제 geometry (shapely) 또는 None
      exclusion_type  point | polygon | mixed
      rows            지목별 판정 근거 (classify 결과)
      n_면점/n_점점    면으로 처리된 점 수 / 점으로 처리된 점 수
      n_시드필지/n_확장필지
      area_km2
      warnings        기여 0 인 부분에 대한 경고. **비어 있지 않으면 산출물에 남긴다.**
      확인요청        사람이 판정을 뒤집을지 봐야 하는 행 (`classify` 참조).
                      임계 HITL 을 실행 중단 없이 내보내는 경로다 — STEP4 는
                      무입력 실행 전제라 `input()` 을 넣지 않는다.
    """
    if points.crs != parcels.crs:
        points = points.to_crs(parcels.crs)
    base = base or area_base_rate(parcels)
    matched = match_parcels(points, parcels)
    rows = classify(
        lift_table(points, parcels, base, matched), lift_min, share_min, count_min
    )

    poly_jimok = {r["지목"] for r in rows if r["판정"] == "면" and r["지목"]}
    jm_of = parcels["지목"].reindex(matched.fillna(-1)).to_numpy()
    is_poly = (
        pd.Series([j in poly_jimok for j in jm_of], index=points.index)
        & matched.notna()
    )

    parts = []
    seed = sorted(set(matched[is_poly].dropna().astype(int)))
    grown, iters = expand_adjacent(parcels, seed) if (expand and seed) else (seed, 0)
    if grown:
        g = parcels.loc[grown].geometry
        parts.append(unary_union((g.buffer(float(radius)) if radius else g).values))

    warns = []
    pts = points[~is_poly].geometry
    if len(pts):
        if radius:
            parts.append(unary_union(pts.buffer(float(radius)).values))
        else:
            # 반경 없는 점은 면적 0 — 배제에 아무 기여를 못 한다.
            # 레이어 전체가 0 이면 호출부 가드가 잡지만, 여기처럼 **일부만** 0 이면
            # 총면적이 0 이 아니라서 가드를 통과한다. 조용히 넘기지 않는다(절대원칙 1·4).
            warns.append(
                f"점 처리 {len(pts)}건에 배제반경이 없어 기여 0 "
                f"— HITL 에서 반경을 입력하세요"
            )

    geom = unary_union(parts) if parts else None
    n_poly = int(is_poly.sum())
    etype = "polygon" if n_poly == len(points) else "point" if n_poly == 0 else "mixed"
    return {
        "geom": geom,
        "exclusion_type": etype,
        "rows": rows,
        "n_면점": n_poly,
        "n_점점": len(points) - n_poly,
        "n_시드필지": len(seed),
        "n_확장필지": len(grown),
        "확장반복": iters,
        "area_km2": (geom.area / 1e6) if geom is not None else 0.0,
        "warnings": warns,
        "확인요청": review_requests(rows, lift_min, share_min, count_min),
    }


def format_rows(rows: list[dict], indent: str = "      ") -> list[str]:
    """판정 근거를 사람이 읽는 줄로. 산출물·콘솔 공용."""
    out = []
    for r in rows:
        jm = r["지목"] or "(필지밖)"
        b = f"{r['기저'] * 100:>5.2f}%" if r["기저"] is not None else "    -"
        lf = f"{r['배수']:>7.1f}x" if r["배수"] is not None else "       -"
        ask = " ❓확인요청" if r.get("확인요청") else ""
        out.append(
            f"{indent}{jm:<6} {r['점수']:>4}점  관측 {r['관측'] * 100:>5.1f}%  "
            f"기저 {b}  배수 {lf}  → {r['판정']}  ({r['사유']}){ask}"
        )
    return out
