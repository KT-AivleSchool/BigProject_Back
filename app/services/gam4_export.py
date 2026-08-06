# -*- coding: utf-8 -*-
"""
OmniSite 표출 산출물 (STEP 4 - J)
==================================
계산된 결과를 프런트가 쓸 형식으로 내보낸다. **새 연산은 4326 변환뿐이다.**

  <도메인>_score_grid.json    점수면 (중심점+점수, 압축)
  <도메인>_exclusion.geojson  배제구역 (레이어별)
  <도메인>_topN.geojson       Top-N 마커
  <도메인>_report.json        파라미터·커버곡선·진단
  <도메인>_preview.html       단독 미리보기 (더블클릭으로 열림)

점수면을 폴리곤 GeoJSON 이 아니라 **중심점+점수**로 내보내는 이유
  격자는 규칙적이라 좌표를 다 보낼 필요가 없다. 원점·간격만 주면 프런트가
  L.rectangle 로 복원한다. 실측 3.09MB -> 0.26MB (12배).

배제구역 셀을 빼지 않고 붉게 남기는 이유
  구멍만 남으면 "왜 여기 후보가 없나"를 알 수 없다. 점수는 계산하되 후보에서만
  제외하고, 표출에는 남겨 이유를 클릭으로 보여준다(설계 확정 턴7·72).
"""

from __future__ import annotations

import json
import os

import numpy as np
import geopandas as gpd

try:
    from app.config import DISPLAY_CRS
except Exception:
    DISPLAY_CRS = 4326


# =========================================================
# 산출물
# =========================================================
def export_score_grid(
    path: str,
    grid: gpd.GeoDataFrame,
    score: np.ndarray,
    excluded: np.ndarray,
    spacing: float,
) -> dict:
    """점수면 — 중심점(4326) + 점수 + 배제여부. 좌표는 6자리로 반올림."""
    g = grid.to_crs(DISPLAY_CRS)
    lon = np.round(g.geometry.x.to_numpy(), 6)
    lat = np.round(g.geometry.y.to_numpy(), 6)
    s = np.round(np.asarray(score, dtype=float), 4)
    ex = np.asarray(excluded, dtype=bool)

    doc = {
        "spacing_m": float(spacing),
        "crs": f"EPSG:{DISPLAY_CRS}",
        "count": int(len(g)),
        "score_min": float(s.min()) if len(s) else 0.0,
        "score_max": float(s.max()) if len(s) else 0.0,
        # [경도, 위도, 점수, 배제여부] — 배열이라 키 반복이 없다
        "cells": [
            [float(a), float(b), float(c), int(d)]
            for a, b, c, d in zip(lon, lat, s, ex)
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    return doc


def export_exclusion(
    path: str, layers: dict, crs, simplify_m: float = 1.0
) -> dict | None:
    """배제구역 — 레이어별로 분리. 어느 규칙 때문에 막혔는지 보여주기 위함.

    simplify_m: 표출 전용 단순화(m). 어린이집 173개 버퍼가 합쳐지면 꼭짓점이
      수만 개가 되어 1.69MB 가 나온다. 1m 단순화로 시각 차이 없이 10분의 1 이하.
      **배제 판정에는 원본 union 을 쓴다** — 여기서 줄이는 건 표출본뿐이다.
    """
    rows = []
    for did, info in layers.items():
        geom = info.get("geom")
        if geom is None:
            continue
        if simplify_m and simplify_m > 0:
            try:
                geom = geom.simplify(simplify_m, preserve_topology=True)
            except Exception:
                pass
        rows.append(
            {
                "dataset_id": did,
                "type": info.get("type", ""),
                # 값마다 누가 정했는지 남긴다 — 지목 배수(코드)인가 감리(LLM)인가
                "type_source": info.get("exclusion_type_source", ""),
                "type_llm": info.get("exclusion_type_llm", ""),
                "radius_m": info.get("radius"),
                "label": f"{did} {info.get('type', '')} "
                f"{info.get('radius') or ''}m".strip(),
                "geometry": geom,
            }
        )
    if not rows:
        return None
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs).to_crs(DISPLAY_CRS)
    gdf.to_file(path, driver="GeoJSON")
    return json.loads(gdf.to_json())


def export_topn(path: str, sel: gpd.GeoDataFrame) -> dict:
    """Top-N 마커. geometry 외 컬럼은 그대로 속성으로 싣는다."""
    import pandas as pd

    g = sel.to_crs(DISPLAY_CRS).copy()
    # GeoJSON 은 bool·numpy 타입에 약하다. pandas 3.x 의 StringDtype 은
    # np.issubdtype 이 해석하지 못하므로 pandas API 로 판정한다.
    for c in g.columns:
        if c == "geometry":
            continue
        s = g[c]
        if pd.api.types.is_bool_dtype(s):
            g[c] = s.astype(int)
        elif pd.api.types.is_float_dtype(s):
            g[c] = s.astype(float).round(4)
        elif pd.api.types.is_integer_dtype(s):
            g[c] = s.astype("int64")
    g.to_file(path, driver="GeoJSON")
    return json.loads(g.to_json())


def export_report(
    path: str,
    *,
    domain: str,
    facility: str,
    ws: dict,
    params: dict,
    counts: dict,
    cov: dict,
    sel: gpd.GeoDataFrame,
    gap: list | None = None,
    spatial: dict | None = None,
) -> dict:
    """파라미터·커버곡선·진단을 한 파일에. 재현성과 설명책임용."""
    doc = {
        "domain": domain,
        "facility": facility,
        "weight_set": {
            "alpha": ws.get("alpha"),
            "decay": ws.get("decay"),
            "scale": ws.get("scale"),
            "n_candidates": ws.get("n_candidates"),
            "indicators": [
                {"id": i["id"], "w_final": i["w_final"], "radius_m": i.get("radius_m")}
                for i in ws.get("indicators", [])
            ],
        },
        "facility_params": params,
        "counts": counts,
        "coverage": (
            {
                "n_max": cov.get("n_max"),
                "cumulative": [round(float(x), 4) for x in cov.get("cum", [])],
                "marginal": [round(float(x), 4) for x in cov.get("gain", [])],
                "reach": {str(k): v for k, v in (cov.get("reach") or {}).items()},
                "knee": cov.get("knee"),
                # 커버 상한 — 100% 가 아닌 것이 버그가 아니라 사실임을 산출물에 남긴다.
                #   프런트가 "최대 99.97%" 를 표시할 근거가 여기 없으면 설명이 불가능하다.
                "ceiling": cov.get("ceiling"),
                "unreached_n": cov.get("unreached_n"),
                "unreached_val": cov.get("unreached_val"),
                # 🔴 공간 술어 회귀 대조용(S5). 아래 spatial 절 주석 참조.
                "cover_pairs": cov.get("cover_pairs"),
                "n_cand_mclp": cov.get("n_cand_mclp"),
                "n_demand": cov.get("n_demand"),
            }
            if cov
            else None
        ),
        # 🔴 공간 연산 회귀 대조용 (S5 PostGIS 전환).
        #   여기 값들은 **화면에 쓰라고 넣은 게 아니라 대조하라고 넣은 것**이다.
        #   지금까지 배제 union 면적은 어떤 산출물에도 없어 로그를 손으로 옮겨
        #   픽스처에 적었다(make_fixture.py --union). 사람이 옮겨적는 값은
        #   오타 한 번에 기준선이 조용히 바뀐다.
        "spatial": spatial,
        "topn": json.loads(sel.drop(columns="geometry").to_json(orient="records")),
        "data_gap": gap or [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, default=str)
    return doc


# =========================================================
# 갭 리포트
# =========================================================
def build_gap_report(
    reviewed: dict,
    excl_rows: list,
    jimok_rec: dict | None = None,
    odd_jimok: list | None = None,
) -> list:
    """반영하지 못한 것을 산출물에 명시한다.

    왜 필요한가
      "데이터 없으면 배제 안 함"(설계 확정 턴17)은 옳지만, 그 사실이 어디에도
      남지 않으면 **산출물만 보고 배제가 완전한 줄 안다.** 학교 정문 앞이
      상위에 나와도 왜인지 설명할 근거가 없다.
      배제를 지어내지도 않고 침묵하지도 않는 것 — fail loudly 의 표출 형태다.

    간이판: 조례 원문 파싱 없이, 이미 가진 데이터에서만 뽑는다.
      · reviewed.json 의 미확정 배제·미해결 HITL 플래그
      · 배제 레이어 로드 실패
      · 지목 판정 실패(unknown)·표준 밖 부호
      · unusable 지목 — 필지만 빠지고 '주변 이격'은 미적용
    """
    gaps = []

    # ① 배제반경이 확정되지 않은 hard_exclusion
    for r in reviewed.get("results", []):
        did = r.get("dataset_id")
        for role in r.get("roles") or []:
            if role.get("role") != "hard_exclusion":
                continue
            rad, conf = role.get("배제반경_m"), role.get("confirmed")
            if rad is None and role.get("exclusion_type") != "polygon":
                gaps.append(
                    {
                        "kind": "배제반경_미확정",
                        "target": f"dataset {did}",
                        "detail": "배제반경_m 이 없어 원형상만 배제됨",
                        "impact": "실제 규제 이격거리가 반영되지 않았을 수 있음",
                    }
                )
            elif conf is False:
                gaps.append(
                    {
                        "kind": "배제반경_미검증",
                        "target": f"dataset {did}",
                        "detail": f"{rad}m (confirmed=false)",
                        "impact": "조례 원문 대조가 끝나지 않은 값",
                    }
                )

    # ② 미해결 HITL 플래그
    #   ⚠ hitl_flags 는 HITL 로 확정한 뒤에도 지워지지 않는다(감리 파이프라인이
    #     플래그를 소거하지 않음). 플래그만 보면 이미 해결된 항목까지 잡힌다 —
    #     실제로 01·05·11 은 human_confirmed 로 확정됐는데도 남아 있었다.
    #   → 배제반경이 실제로 채워졌는지 **교차 확인**한다.
    settled = set()
    for r in reviewed.get("results", []):
        for role in r.get("roles") or []:
            if role.get("role") != "hard_exclusion":
                continue
            if (
                role.get("배제반경_m") is not None
                or role.get("exclusion_type") == "polygon"
            ):
                settled.add(str(r.get("dataset_id")))

    # 지역코드 플래그도 같은 성격이다 — HITL 에서 확정해도 플래그가 남는다.
    #   cleaning_ops 의 params.prefix_confirmed 로 교차 확인한다.
    code_settled = set()
    for r in reviewed.get("results", []):
        ops = r.get("cleaning_ops") or []
        if ops and all(
            (op.get("params") or {}).get("prefix_confirmed")
            for op in ops
            if op.get("op_id") == "filter_by_code_prefix"
        ):
            code_settled.add(str(r.get("dataset_id")))

    for r in reviewed.get("results", []):
        did = str(r.get("dataset_id"))
        for f in r.get("hitl_flags") or []:
            ftype = str(f.get("type") or "")
            if f.get("resolved") is True or f.get("confirmed") is True:
                continue
            # 배제반경 관련 플래그인데 값이 이미 확정됐다면 해결된 것
            if "exclusion_radius" in ftype and did in settled:
                continue
            if ftype == "code_prefix_unverified" and did in code_settled:
                continue
            gaps.append(
                {
                    "kind": "HITL_미해결",
                    "target": f"dataset {did}",
                    "detail": ftype or str(f)[:80],
                    "impact": "감리 단계에서 확정되지 않은 항목",
                }
            )

    # ③ 배제 레이어 로드 실패 · 점/면 판정 경고 (excl_rows: load_exclusions 의 dict)
    for row in excl_rows or []:
        did = row["id"]
        if row["note"]:
            gaps.append(
                {
                    "kind": "배제레이어_누락",
                    "target": f"dataset {did}",
                    "detail": row["note"],
                    "impact": "이 배제 규칙이 적용되지 않았음",
                }
            )
        det = row.get("detail") or {}
        # 일부만 기여 0 인 경우는 총면적이 0 이 아니라 가드를 통과한다 — 여기서 남긴다.
        for w in det.get("warnings", []):
            gaps.append(
                {
                    "kind": "배제_부분누락",
                    "target": f"dataset {did}",
                    "detail": w,
                    "impact": "그 건들은 배제 면적에 기여하지 않음",
                }
            )
        # 임계 HITL — 실행은 막지 않고 확인 대상만 넘긴다(A2 프런트에서 뒤집는다).
        for q in det.get("확인요청", []):
            th = q["임계"]
            gaps.append(
                {
                    "kind": "배제판정_확인요청",
                    "target": f"dataset {did} · 지목 {q['지목']}",
                    "detail": f"{q['판정']} 판정 — {q['점수']}점 · 관측 {q['관측']:.1%} · "
                    f"배수 {q['배수']}x  ({q['사유']}) / "
                    f"임계 배수 {th['배수']:g}x · 관측 {th['관측']:.0%} · "
                    f"표본 {th['표본']}점",
                    "impact": (
                        "면 판정이라 해당 필지 전체 + 인접 동일지목까지 배제됨"
                        if q["판정"] == "면"
                        else "배수는 임계를 넘었으나 다른 조건에 막혀 점으로 처리됨"
                    ),
                    # target 문자열을 파싱시키지 않는다 — 뒤집을 대상을 구조로 준다.
                    "review": {"dataset_id": did, **q},
                }
            )

    # ④ 지목 판정 실패
    if jimok_rec:
        unk = [
            k
            for k, v in (jimok_rec.get("roles") or {}).items()
            if v.get("role") == "unknown"
        ]
        if unk:
            gaps.append(
                {
                    "kind": "지목_판정실패",
                    "target": ", ".join(unk),
                    "detail": "LLM 응답 누락 또는 형식 위반",
                    "impact": "해당 지목 필지가 후보에서 제외됨",
                }
            )

        # ⑤ unusable 지목 — 필지만 빠지고 '주변'은 안 빠졌다
        un = [
            k
            for k, v in (jimok_rec.get("roles") or {}).items()
            if v.get("role") == "unusable"
        ]
        if un:
            gaps.append(
                {
                    "kind": "주변이격_미적용",
                    "target": ", ".join(un),
                    "detail": "해당 지목 필지 자체만 후보에서 제외했다",
                    "impact": "이 시설들의 '주변 이격거리' 규제가 있다면 미반영이다. "
                    "적용하려면 해당 시설 데이터셋을 감리에 태워야 한다",
                }
            )

    # ⑥ 표준 밖 지목 부호
    if odd_jimok:
        gaps.append(
            {
                "kind": "지목부호_비표준",
                "target": ", ".join(odd_jimok),
                "detail": "지적법 표준 28종에 없는 부호",
                "impact": "해당 필지는 후보에서 제외됨(원본 데이터 확인 필요)",
            }
        )
    return gaps


def print_gap_report(gaps: list) -> None:
    # 확인요청은 "반영 못 한 것" 이 아니라 "사람이 뒤집을지 봐야 하는 것" 이라 따로 센다.
    n_ask = sum(1 for g in gaps if g["kind"] == "배제판정_확인요청")
    print("\n" + "=" * 70)
    print(
        f"[갭 리포트]  반영하지 못한 항목 {len(gaps) - n_ask}건"
        + (f"  ·  확인 요청 {n_ask}건" if n_ask else "")
    )
    print("-" * 70)
    if not gaps:
        print("  없음")
    for g in gaps:
        print(f"  ⚠ {g['kind']:<16} {g['target']}")
        print(f"      {g['detail']}")
        print(f"      → {g['impact']}")
    print("=" * 70)


# =========================================================
# 단독 미리보기
# =========================================================
_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
 html,body{margin:0;height:100%;font-family:-apple-system,'Malgun Gothic',sans-serif}
 #map{height:100%}
 .box{position:absolute;z-index:1000;background:#fff;padding:10px 12px;
      border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,.3);font-size:12px}
 #legend{bottom:24px;left:12px;line-height:1.7}
 #info{top:12px;right:12px;max-width:260px;line-height:1.6}
 .sw{display:inline-block;width:13px;height:13px;vertical-align:-2px;
     margin-right:5px;border-radius:2px}
 h4{margin:0 0 6px;font-size:13px}
 table{border-collapse:collapse;font-size:11px}
 td{padding:1px 6px 1px 0}
</style></head><body>
<div id="map"></div>
<div id="legend" class="box">
  <h4>점수면</h4>
  <div><span class="sw" style="background:#E6F1FB"></span>낮음</div>
  <div><span class="sw" style="background:#85B7EB"></span>중간</div>
  <div><span class="sw" style="background:#185FA5"></span>높음</div>
  <div><span class="sw" style="background:#042C53"></span>최상</div>
  <div style="margin-top:6px">
    <span class="sw" style="background:rgba(226,75,74,.35);border:1px solid #E24B4A"></span>배제구역</div>
  <div><span class="sw" style="background:#EF9F27;border-radius:50%"></span>Top-N</div>
</div>
<div id="info" class="box"><h4>__FACILITY__</h4><div id="meta"></div></div>
<script>
const GRID=__GRID__, TOPN=__TOPN__, EXCL=__EXCL__, META=__META__;
const map=L.map('map',{preferCanvas:true});
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
 {attribution:'&copy; OpenStreetMap &copy; CARTO',maxZoom:20}).addTo(map);

const ramp=[[230,241,251],[181,212,244],[133,183,235],[55,138,221],
            [24,95,165],[12,68,124],[4,44,83]];
function col(t){const p=Math.max(0,Math.min(.999,t))*(ramp.length-1),
  i=Math.floor(p),f=p-i,a=ramp[i],b=ramp[Math.min(i+1,ramp.length-1)];
  return`rgb(${Math.round(a[0]+(b[0]-a[0])*f)},${Math.round(a[1]+(b[1]-a[1])*f)},${Math.round(a[2]+(b[2]-a[2])*f)})`;}

// 격자 셀 — 규칙적이라 중심점+간격으로 사각형을 복원한다
const sp=GRID.spacing_m, dLat=sp/111320, lo=GRID.score_min, hi=GRID.score_max;
const gLayer=L.layerGroup(), xLayer=L.layerGroup();
GRID.cells.forEach(([x,y,s,ex])=>{
  const dLon=sp/(111320*Math.cos(y*Math.PI/180));
  const b=[[y-dLat/2,x-dLon/2],[y+dLat/2,x+dLon/2]];
  const t=(hi>lo)?(s-lo)/(hi-lo):0;
  const r=L.rectangle(b,{stroke:false,
    fillColor: ex?'#E24B4A':col(t), fillOpacity: ex?0.35:0.75});
  r.bindPopup(`점수 <b>${s.toFixed(4)}</b>${ex?'<br><span style="color:#c0392b">배제구역 — 설치 불가</span>':''}`);
  (ex?xLayer:gLayer).addLayer(r);
});
gLayer.addTo(map); xLayer.addTo(map);

// 배제 레이어 경계
const eLayer=L.geoJSON(EXCL,{style:{color:'#E24B4A',weight:1.2,fill:false,opacity:.85},
  onEachFeature:(f,l)=>l.bindPopup(`배제: ${f.properties.label||''}`)});

// Top-N
const tLayer=L.layerGroup();
(TOPN.features||[]).forEach(f=>{
  const p=f.properties, c=f.geometry.coordinates;
  const m=L.circleMarker([c[1],c[0]],{radius:11,fillColor:'#EF9F27',
    color:'#412402',weight:1.2,fillOpacity:1}).addTo(tLayer);
  m.bindTooltip(String(p['순위']),{permanent:true,direction:'center',className:'rk'});
  const rows=[['지번',p['JIBUN']],['지목',p['지목']],
    ['면적',(p['면적']||0).toLocaleString()+' m²'],['내접폭',(p['내접폭']||0).toFixed(1)+' m'],
    ['점수',(p['점수']||0).toFixed(4)],
    ['누적커버',p['누적커버율']!=null?(p['누적커버율']*100).toFixed(1)+'%':'-'],
    ['국유지분',p['국유_건수']>0?((p['국유_지분율']*100).toFixed(0)+'%'):'-']];
  m.bindPopup(`<b>${p['순위']}위</b><table>`+
    rows.map(r=>`<tr><td>${r[0]}</td><td><b>${r[1]??'-'}</b></td></tr>`).join('')+`</table>`);
});
tLayer.addTo(map);

L.control.layers(null,{'점수면':gLayer,'배제 셀':xLayer,'배제 경계':eLayer,
  'Top-N':tLayer},{collapsed:false}).addTo(map);
map.fitBounds(L.geoJSON(TOPN).getBounds().pad(0.25));

document.getElementById('meta').innerHTML=
 `<table>${Object.entries(META).map(([k,v])=>
   `<tr><td>${k}</td><td><b>${v}</b></td></tr>`).join('')}</table>`;
</script>
<style>.rk{background:none;border:none;box-shadow:none;color:#412402;
 font-weight:600;font-size:11px}</style>
</body></html>"""


def export_preview(
    path: str,
    grid_doc: dict,
    topn_doc: dict,
    excl_doc: dict | None,
    meta: dict,
    facility: str,
) -> None:
    """단독 HTML — 데이터를 파일에 심어 더블클릭만으로 열리게 한다.

    fetch 로 외부 JSON 을 읽으면 file:// 에서 CORS 로 막힌다. 로컬 서버를
    띄우게 하는 대신 자체 완결 파일로 만든다(0.3~1MB).
    """
    html = (
        _HTML.replace("__TITLE__", f"OmniSite — {facility}")
        .replace("__FACILITY__", facility)
        .replace(
            "__GRID__", json.dumps(grid_doc, ensure_ascii=False, separators=(",", ":"))
        )
        .replace(
            "__TOPN__", json.dumps(topn_doc, ensure_ascii=False, separators=(",", ":"))
        )
        .replace(
            "__EXCL__",
            json.dumps(
                excl_doc or {"type": "FeatureCollection", "features": []},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        .replace("__META__", json.dumps(meta, ensure_ascii=False))
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def export_all(
    out_dir: str,
    prefix: str,
    *,
    grid,
    gscore,
    gexcluded,
    spacing,
    sel,
    excl_layers,
    crs,
    domain,
    facility,
    ws,
    params,
    counts,
    cov,
    gap=None,
    spatial=None,
    simplify_m: float = 1.0,
    verbose: bool = True,
) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    p = lambda name: os.path.join(out_dir, f"{prefix}_{name}")

    grid_doc = export_score_grid(p("score_grid.json"), grid, gscore, gexcluded, spacing)
    excl_doc = export_exclusion(
        p("exclusion.geojson"), excl_layers, crs, simplify_m=simplify_m
    )
    topn_doc = export_topn(p("topN.geojson"), sel)
    export_report(
        p("report.json"),
        domain=domain,
        facility=facility,
        ws=ws,
        params=params,
        counts=counts,
        cov=cov,
        sel=sel,
        gap=gap,
        spatial=spatial,
    )

    meta = {
        "후보 필지": f"{counts.get('parcels', 0):,}",
        "후보점": f"{counts.get('points', 0):,}",
        "최종 생존": f"{counts.get('survive', 0):,}",
        "선정": f"{len(sel)}개",
        "서비스반경": f"{params.get('서비스_반경_m', '-')}m",
        "최소이격": f"{params.get('최소_이격_m', '-')}m",
        "설치폭": f"{params.get('설치_소요_폭_m', '-')}m",
    }
    if cov and cov.get("knee"):
        meta["효율 변곡점"] = f"{cov['knee']}번째"
    export_preview(p("preview.html"), grid_doc, topn_doc, excl_doc, meta, facility)

    files = [
        p(n)
        for n in (
            "score_grid.json",
            "exclusion.geojson",
            "topN.geojson",
            "report.json",
            "preview.html",
        )
    ]
    if verbose:
        print("\n[J] 표출 산출물")
        for f in files:
            if os.path.exists(f):
                print(
                    f"  {os.path.basename(f):<28} {os.path.getsize(f) / 1e6:>6.2f} MB"
                )
        print(f"  → 미리보기: {p('preview.html')}")
    return {"files": files}
