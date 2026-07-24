# -*- coding: utf-8 -*-
"""
OmniSite 정제 실행 엔진 (STEP 3) — 독립 실행
============================================
감리(STEP 1) 산출물 result.json 의 cleaning_ops 를, 카탈로그 execute() 로 데이터셋마다
실제 적용한다. 감리 AI 는 '무엇을 할지'만 판정했고, 실제 행 변환은 여기(결정론 op)서만.

  python clean_data.py <도메인폴더> [--csv-preview] [--no-prune]
  예) python clean_data.py 흡연

입력
  · result.json      : reviewed > enriched > audit_result 순 폴백 (HITL 확정본 우선)
                       {results:[{dataset_id, roles, coord_status, cleaning_ops}], facility_inference}
  · profiles.json    : {dataset_id: {filename, extension, columns, coord_cols, ...}}
                       (dataset_id -> 실제 원본 파일·확장자 다리. result.json 만으론 원본을 못 찾음)
  · <도메인>/data/   : 원본 (csv·xlsx·xls·shp·json)

처리 (데이터셋 1건)
  1) profile 로 원본 로드 (확장자 자동 분기, CSV 인코딩 폴백)
  2) OpContext 구성 (facility·region <- facility_inference, adm_shp_path <- config,
                     whitelists <- 데이터셋 간 누적 공유)
  3) execute(df, {cleaning_ops}, ctx) -> (정제 df, HitlFlag[], OpLog[])
  4) HitlFlag -> dict(+한글 message) 직렬화
  5) 저장: geometry 있으면 .gpkg (좌표->Point/폴리곤, GIS·QGIS 소비)
          없으면 .csv (사람 확인·범용)

출력 — 전부 config.STEP2_OUTPUT_DIR (…/data_임시/step2_output). 도메인 프리픽스로 구분.
  · <prefix>_clean_<dataset_id>.{gpkg|parquet}  (+ --csv-preview 시 _preview.csv)
  · <prefix>_clean_report.json  (dataset별 rows before/after·flags·op logs·role·GIS입력여부)

원칙
  · emit_whitelist 데이터셋을 소비(filter_by_join_key) 데이터셋보다 먼저 처리.
  · reference_only 도 정제한다(페르소나·참조 활용). 단 GIS 입력에선 제외 표식.
  · 지원 확장자(csv·xlsx·xls·shp·json) 외에는 조용히 넘기지 않고 명시적 에러.
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from dataclasses import asdict

import pandas as pd

import scripts.gam2.run_audit_judgment_test as A
import app.core.gam2_pipeline.gam2_audit_ops_catalog as cat
from app.core.gam2_pipeline.gam2_profile import (
    _read_sample,
)  # 감리와 동일한 읽기 경로 사용
from app.config import ADM_DONG_SHP, STEP2_OUTPUT_DIR


# ── HitlFlag type -> 사람이 읽는 한글 메시지 (HITL 화면·리포트 공용) ──
FLAG_MESSAGES = {
    "geocode_failed": "주소→좌표 변환 실패",
    "coord_invalid": "좌표가 전국 유효범위 밖이거나 비어 있음",
    "coord_out_of_admin": "행정동 경계 밖 좌표",
    "coord_wrong_district": "대상 자치구를 벗어난 좌표(지오코딩 오매칭 의심)",
    "admin_join_miss": "행정동 공간조인 실패(경계 밖)",
    "null_required": "필수 컬럼이 비어 있음",
    "join_key_uncovered": "화이트리스트 일부 키가 데이터에 없음",
    "unknown_op": "카탈로그에 없는 op 를 감리 AI가 지정함(건너뜀)",
    "missing_whitelist": "참조한 화이트리스트를 만드는 데이터셋이 없음(필터 건너뜀)",
    "filter_no_match": "필터 허용값이 데이터에 없어 건너뜀(컬럼·값 지정 오류)",
    "admin_name_dropped": "행정동명이 아닌 행 제외(합계·소계 등)",
    "admin_name_low_match": "행정동명 매칭률이 낮아 필터 건너뜀(다른 지역·표기 체계 의심)",
    "admin_name_map_missing": "행정동 코드표에서 대상 지역을 못 찾아 필터 건너뜀",
}

# 경도/위도 컬럼 판정 힌트(순서 보장이 없어 이름으로 구분).
#   맨 앞 것부터 우선 매칭. 한 글자 'x'/'y' 는 오검출 위험이라 쓰지 않고 'x좌표' 형태만 본다.
#   (config.COORD_COL_CANDIDATES 에 새 이름을 넣을 때 여기도 함께 볼 것)
_LNG_HINTS = ("경도", "longitude", "lng", "lon", "x좌표", "x_좌표", "xcoord")
_LAT_HINTS = ("위도", "latitude", "lat", "y좌표", "y_좌표", "ycoord")


# ══════════════════════════════════════════════════════════════════
# 1. 원본 로드
# ══════════════════════════════════════════════════════════════════


def _load_raw(profile: dict, data_dir: str):
    """profile 로 원본 파일을 로드. 확장자 자동 분기. shp 는 GeoDataFrame."""
    fname = profile.get("filename")
    if not fname:
        raise ValueError(
            f"profile 에 filename 없음: dataset_id={profile.get('dataset_id')}"
        )
    path = os.path.join(data_dir, fname)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"원본 없음: {path}")
    ext = (profile.get("extension") or os.path.splitext(fname)[1].lstrip(".")).lower()

    if ext not in ("csv", "xlsx", "xls", "json", "shp"):
        # 지원 밖 — 조용히 넘기지 않는다
        raise ValueError(
            f"[clean_data] 로더 없는 확장자 '.{ext}' (dataset_id={profile.get('dataset_id')}, "
            f"파일={fname}). 지원: csv·xlsx·xls·json·shp. "
            f"추가하려면 profile.py 의 DATA_EXTENSIONS 와 _read_sample 에 넣을 것."
        )
    # ★ 감리(profile)와 완전히 같은 읽기 경로를 쓴다(nrows=None → 전체 로드).
    #   따로 읽으면 헤더 교정·인코딩 폴백·index_col 처리가 어긋나, 감리가 본 컬럼과
    #   정제가 로드한 컬럼이 달라진다(=params 가 가리키는 컬럼이 없다는 에러).
    return _read_sample(path, "." + ext, None)


# ══════════════════════════════════════════════════════════════════
# 2. 저장 (geometry 유무로 gpkg/csv 분기)
# ══════════════════════════════════════════════════════════════════


def _pick_lnglat(cols) -> tuple:
    """컬럼명으로 (경도컬럼, 위도컬럼) 추정. 못 찾으면 (None, None)."""
    low = {c: str(c).lower() for c in cols}
    lng = next((c for c in cols if any(h in low[c] for h in _LNG_HINTS)), None)
    lat = next((c for c in cols if any(h in low[c] for h in _LAT_HINTS)), None)
    return lng, lat


def _clear_stale(out_base: str) -> None:
    """이전 실행 산출물 제거. 형식이 바뀌면(csv→gpkg) 옛 파일이 남아 혼동을 준다."""
    for ext in (".csv", ".gpkg", ".parquet", "_preview.csv"):
        f = out_base + ext
        if os.path.isfile(f):
            try:
                os.remove(f)
            except OSError:
                pass


def _save_dataset(df, out_base: str, csv_preview: bool) -> tuple:
    """정제 결과 저장. 반환: (경로, 형식). geometry 있으면 gpkg, 없으면 csv.
    csv_preview=True 면 gpkg 저장 시 속성 테이블(geometry 제외)도 csv 로 함께 덤프."""
    try:
        import geopandas as gpd
    except ImportError:
        gpd = None

    # (a) 이미 GeoDataFrame (shp 유래 폴리곤/점)
    if (
        gpd is not None
        and isinstance(df, gpd.GeoDataFrame)
        and df.geometry.notna().any()
    ):
        p = out_base + ".gpkg"
        df.to_file(p, driver="GPKG")
        if csv_preview:
            pd.DataFrame(df.drop(columns=df.geometry.name)).to_csv(
                out_base + "_preview.csv", index=False, encoding="utf-8-sig"
            )
        return p, "gpkg"

    # (b) 좌표 컬럼 쌍이 있으면 Point geometry 로 gpkg
    lng, lat = _pick_lnglat(df.columns)
    if gpd is not None and lng and lat:
        x = pd.to_numeric(df[lng], errors="coerce")
        y = pd.to_numeric(df[lat], errors="coerce")
        if (x.notna() & y.notna()).any():
            gdf = gpd.GeoDataFrame(
                df.copy(), geometry=gpd.points_from_xy(x, y), crs="EPSG:4326"
            )
            p = out_base + ".gpkg"
            gdf.to_file(p, driver="GPKG")
            if csv_preview:
                df.to_csv(out_base + "_preview.csv", index=False, encoding="utf-8-sig")
            return p, "gpkg"

    # (c) geometry 없는 통계표 → parquet
    #   csv 는 타입을 담지 못해 cast_numeric 결과가 저장 순간 문자열로 되돌아간다
    #   (승하차인원·생활인구가 str 이 되어 AHP 계산 전에 매번 형변환이 필요했다).
    #   parquet 는 dtype 을 보존하고 용량도 작다. 사람이 볼 용도는 --csv-preview 로.
    try:
        p = out_base + ".parquet"
        df.to_parquet(p, index=False)
        if csv_preview:
            df.to_csv(out_base + "_preview.csv", index=False, encoding="utf-8-sig")
        return p, "parquet"
    except Exception as e:  # pyarrow 미설치 등 → csv 로 폴백(타입은 유실)
        print(f"  [경고] parquet 저장 실패({e}) → csv 로 저장(타입 유실)")
        p = out_base + ".csv"
        df.to_csv(p, index=False, encoding="utf-8-sig")
        return p, "csv"


# ══════════════════════════════════════════════════════════════════
# 3. 보조
# ══════════════════════════════════════════════════════════════════


def _norm_vals(sr, mode: str) -> set:
    """조인 키 비교용 정규화. catalog._norm_key 와 같은 규칙(zfill5/strip_paren/none)."""
    s = sr.dropna().astype(str).str.strip()
    if mode == "zfill5":
        s = s.str.replace(r"\.0$", "", regex=True).str.zfill(5)
    elif mode == "strip_paren":
        s = s.map(lambda v: re.sub(r"\(.*?\)", "", v).strip())
    return {v for v in s.unique().tolist() if v}


def _norm_vals_series(sr, mode: str):
    """_norm_vals 와 같은 규칙이지만 집합이 아니라 Series 를 돌려준다(행 마스크용)."""
    s = sr.fillna("").astype(str).str.strip()
    if mode == "zfill5":
        s = s.str.replace(r"\.0$", "", regex=True).str.zfill(5)
    elif mode == "strip_paren":
        s = s.map(lambda v: re.sub(r"\(.*?\)", "", v).strip())
    return s


def _index_keys(did: str, df, cap: int = 200_000) -> dict:
    """정제된 데이터셋에서 '조인 키가 될 수 있는 컬럼'의 값 집합을 뽑아 둔다.
    뒤에 오는 데이터셋이 whitelist 를 못 찾을 때 이 색인에서 짝을 찾는다.
    좌표·수치 컬럼은 키가 될 수 없으므로 제외."""
    out = {}
    if df is None or len(df) == 0:
        return out
    skip = ("geometry", "위도", "경도", "lat", "lng", "X좌표", "Y좌표")
    for c in df.columns:
        if any(k.lower() in str(c).lower() for k in skip):
            continue
        try:
            vals = df[c].dropna().astype(str).str.strip()
        except Exception:
            continue
        u = {v for v in vals.unique().tolist() if v}
        if 1 <= len(u) <= cap:
            out[c] = u
    return {did: out} if out else {}


def _autoresolve_whitelist(
    df,
    key_col: str,
    mode: str,
    key_index: dict,
    min_keys: int = 3,
    min_cover: float = 0.5,
) -> tuple:
    """감리 AI 가 filter_by_join_key 만 지정하고 emit_whitelist 짝을 못 냈을 때,
    **값이 실제로 겹치는지**로 생산자 데이터셋·컬럼을 찾아 자동 연결한다.
    컬럼명이 서로 달라도 되고(표준버스정류장ID↔NODE_ID), 이름만 같고 값이 다른
    가짜 짝은 걸러진다. 반환: (키 목록|None, 근거 dict).

      min_cover : 생산자 값 중 이 비율 이상이 소비자에 있어야 진짜 짝으로 본다.
                  (생산자=지역 필터된 소수 집합 ⊂ 소비자=전역 집합 이 정상 형태)
    """
    if key_col not in df.columns:
        return None, {"reason": f"key_col '{key_col}' 이 이 데이터셋에 없음"}

    # 정규화 방식을 여러 개 시도해 가장 잘 맞는 것을 고른다.
    #   같은 시설을 두 데이터가 다르게 적는 일이 흔하다
    #   (예: 역사마스터 '삼각지' ↔ 승하차 '삼각지(전쟁기념관)' → strip_paren 이라야 매칭).
    #   감리 AI 가 normalize 를 잘못 고르면 조용히 일부가 누락되므로 코드가 검증한다.
    modes = [mode] + [m for m in ("strip_paren", "none", "zfill5") if m != mode]
    best, best_score, best_mode = None, 0.0, mode
    tried = []
    for m in modes:
        mine = _norm_vals(df[key_col], m)
        if not mine:
            continue
        for did, cols in key_index.items():
            for col, raw_vals in cols.items():
                vals = _norm_vals(pd.Series(sorted(raw_vals)), m)
                if len(vals) < min_keys:
                    continue
                hit = len(vals & mine)
                cover = hit / len(vals)  # 생산자 값이 소비자에 얼마나 들어있나
                if hit:
                    tried.append(
                        {
                            "dataset_id": did,
                            "column": col,
                            "normalize": m,
                            "matched": hit,
                            "cover": round(cover, 3),
                        }
                    )
                if cover >= min_cover and hit >= min_keys and cover > best_score:
                    best, best_score, best_mode = (did, col, sorted(vals)), cover, m
    if not best:
        return None, {
            "reason": "값이 충분히 겹치는 컬럼을 다른 데이터셋에서 찾지 못함",
            "후보": sorted(tried, key=lambda x: -x["cover"])[:5],
        }
    did, col, vals = best
    return vals, {
        "from_dataset": did,
        "from_column": col,
        "key_col": key_col,
        "normalize": best_mode,
        "normalize_changed": best_mode != mode,
        "matched_keys": len(vals),
        "cover": round(best_score, 3),
    }


COORD_PARAM_OPS = ("spatial_join_admin", "reverse_geocode", "validate_geocode")


def _autofill_params(ops: list, df, profile: dict) -> tuple:
    """감리 AI 가 빠뜨린 params 중 '데이터에서 결정론적으로 알 수 있는 것'만 코드가 채운다.
    지금은 coord_cols(경도·위도 컬럼) 하나. LLM 판단을 덮어쓰지 않고 '없을 때만' 채우며,
    채운 내역은 리포트에 params_autofilled 로 남겨 감춰지지 않게 한다."""
    filled = []
    cand = list(profile.get("coord_cols") or []) or list(df.columns)
    lng, lat = _pick_lnglat(cand)
    for op in ops:
        if op.get("op_id") not in COORD_PARAM_OPS:
            continue
        prm = op.setdefault("params", {})
        if prm.get("coord_cols"):
            continue
        if lng and lat:
            prm["coord_cols"] = [lng, lat]  # x=경도, y=위도 순서
            filled.append({"op_id": op["op_id"], "coord_cols": [lng, lat]})
    return ops, filled


def _flag_to_dict(f) -> dict:
    """HitlFlag(dataclass) -> dict + 한글 message (dict flag 체계와 통합)."""
    d = {
        "type": f.type,
        "severity": f.severity,
        "row_id": f.row_id,
        "message": FLAG_MESSAGES.get(f.type, f.type),
    }
    if f.raw_text:
        d["raw_text"] = f.raw_text
    if f.dong_guess:
        d["dong_guess"] = f.dong_guess
    if f.approx_coord:
        d["approx_coord"] = f.approx_coord
    return d


def _is_reference_only(roles) -> bool:
    """위치선정 입력 role 이 하나도 없으면 True (GIS 입력 제외 대상)."""
    if not roles:
        return False
    return all(r.get("role") == "reference_only" for r in roles)


def _order_datasets(results: list) -> list:
    """처리 순서: emit_whitelist 보유(생산자) → 일반 → filter_by_join_key 보유(소비자).
    소비자를 맨 뒤로 미뤄야, 앞선 데이터셋들의 정제 결과에서 조인 키를 찾아
    whitelist 를 자동 연결할 수 있다."""

    def rank(r):
        ops = [o.get("op_id") for o in (r.get("cleaning_ops") or [])]
        # 소비 판정을 먼저 본다: emit 과 consume 을 둘 다 가진 데이터셋을 앞에 두면
        # 정작 자기 소비 시점에 다른 데이터셋 결과가 아직 없어 자동연결이 실패한다.
        if "filter_by_join_key" in ops:
            return 2
        if "emit_whitelist" in ops:
            return 0
        return 1

    return sorted(results, key=rank)


def _pick_result_path() -> str:
    """reviewed > enriched > audit_result 순 폴백 (HITL 확정본 우선)."""
    for name in (
        "audit_result_reviewed.json",
        "audit_result_enriched.json",
        "audit_result.json",
    ):
        p = A._out_path(name)
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(
        f"result.json 없음 — 먼저 감리(STEP 1)를 실행하세요. 찾은 경로: {A._out_path('audit_result.json')}"
    )


# ══════════════════════════════════════════════════════════════════
# 4. 메인
# ══════════════════════════════════════════════════════════════════


def clean_domain(
    domain_dir: str,
    csv_preview: bool = False,
    prune: bool = True,
    progress_callback=None,
) -> str:
    A.set_domain(domain_dir)
    prefix = A._DOMAIN["prefix"]
    data_dir = A._DOMAIN["data"]

    os.makedirs(STEP2_OUTPUT_DIR, exist_ok=True)  # 없으면 생성
    result_path = _pick_result_path()
    print(f"[정제] 입력 감리결과: {result_path}")
    with open(result_path, encoding="utf-8") as f:
        doc = json.load(f)
    results = doc.get("results", [])
    fac = doc.get("facility_inference", {}) or {}
    facility, region = fac.get("facility"), fac.get("region")
    if not facility or not region:
        raise ValueError(
            "facility_inference 에 facility/region 이 비어 있다. "
            "HITL(시설·지역 확정) 후 정제하거나, result.json 을 확인하세요."
        )
    print(f"[대상] 시설='{facility}' 지역='{region}'  데이터셋 {len(results)}개")

    with open(A._DOMAIN["profiles"], encoding="utf-8") as f:
        profiles = json.load(f)

    wl: dict = {}  # 데이터셋 간 누적 공유 whitelist
    key_index: dict = {}  # {dataset_id: {컬럼: 값집합}} — 자동 연결용
    cleaned: dict = {}  # {dataset_id: (정제 df, out_base)} — prune 후처리용
    prune_reqs: list = []  # 생산자에서 '소비자에 없는 키' 제거 요청
    report = []

    for r in _order_datasets(results):
        did = r["dataset_id"]
        prof = profiles.get(did)
        if not prof:
            print(f"  [건너뜀] {did}: profiles.json 에 프로파일 없음")
            report.append({"dataset_id": did, "status": "no_profile"})
            continue

        ops = r.get("cleaning_ops") or []
        ref_only = _is_reference_only(r.get("roles", []))
        try:
            df = _load_raw(prof, data_dir)
            before = len(df)
            ops, autofilled = _autofill_params(ops, df, prof)
            # whitelist 자동 연결: 감리 AI 가 소비만 지정한 경우, 앞서 정제된
            # 데이터셋들에서 '값이 실제로 겹치는' 키 컬럼을 찾아 whitelist 를 만든다.
            wl_resolved = []
            # 순환 참조 제거: 같은 데이터셋이 emit 한 이름을 그 데이터셋이 소비하면
            # 아무 효과가 없다(정제 전 자기 값으로 자기를 거름). 게다가 지역 필터가
            # 안 된 상태의 키가 공용 whitelist 를 오염시켜 다른 데이터셋까지 망친다.
            emitted_here = {
                (o.get("params") or {}).get("name")
                for o in ops
                if o.get("op_id") == "emit_whitelist"
            }
            circular = {
                (o.get("params") or {}).get("whitelist")
                for o in ops
                if o.get("op_id") == "filter_by_join_key"
            } & emitted_here
            circular.discard(None)
            if circular:
                ops = [
                    o
                    for o in ops
                    if not (
                        o.get("op_id") == "filter_by_join_key"
                        and (o.get("params") or {}).get("whitelist") in circular
                    )
                    and not (
                        o.get("op_id") == "emit_whitelist"
                        and (o.get("params") or {}).get("name") in circular
                    )
                ]
                for nm in sorted(circular):
                    wl_resolved.append(
                        {
                            "whitelist": nm,
                            "reason": "순환 참조(같은 데이터셋이 생산·소비) — 두 op 모두 제거. "
                            "이 데이터셋은 자체 컬럼(코드 접두 등)으로 걸러야 한다.",
                        }
                    )
            for op in ops:
                if op.get("op_id") != "filter_by_join_key":
                    continue
                prm = op.get("params") or {}
                nm = prm.get("whitelist")
                if not nm or nm in wl:
                    continue
                keys, why = _autoresolve_whitelist(
                    df, prm.get("key_col", ""), prm.get("normalize", "none"), key_index
                )
                if keys:
                    wl[nm] = keys
                    # 자동 선택한 정규화를 실제 필터에도 적용(안 하면 기준이 어긋나 매칭 실패)
                    prm["normalize"] = why.get(
                        "normalize", prm.get("normalize", "none")
                    )
                    why["whitelist"] = nm
                    wl_resolved.append(why)
                else:
                    why["whitelist"] = nm
                    wl_resolved.append(why)
            ctx = cat.OpContext(
                facility=facility,
                region=region,
                domain=domain_dir,
                adm_shp_path=ADM_DONG_SHP,
                whitelists=wl,
            )
            clean, flags, logs = cat.execute(df, {"cleaning_ops": ops}, ctx)
            unknown_ops = [f.raw_text for f in flags if f.type == "unknown_op"]
            skipped = unknown_ops + [
                f.raw_text for f in flags if f.type == "missing_whitelist"
            ]
            skipped += [w["whitelist"] for w in wl_resolved if "reason" in w]
            skipped += [
                f.raw_text
                for f in flags
                if f.type
                in ("filter_no_match", "admin_name_low_match", "admin_name_map_missing")
            ]
            # 상태 구분 — '조용히 틀린 결과'를 ok 로 표시하지 않는다.
            #   empty_result : 결과 0행. 필터 조건이 데이터와 안 맞았을 가능성이 크다
            #                  (예: 자치구명으로 거르려 했으나 컬럼에는 행정동명만 있음).
            #   near_empty   : 99% 이상 소실. 지역 필터로 설명되는 경우도 있으나 확인 필요.
            drop_ratio = (1 - len(clean) / before) if before else 0
            if len(clean) == 0 and before > 0:
                status = "empty_result"
            elif not ops:
                status = "no_ops"
            elif skipped:
                status = "partial"
            elif drop_ratio >= 0.99 and len(clean) < 10:
                # 전국→자치구 필터는 99% 넘게 줄어드는 게 정상이라 비율만으로 경고하지 않는다.
                # 남은 행이 사실상 없을 때만 경고.
                status = "near_empty"
            else:
                status = "ok"

            key_index.update(
                _index_keys(did, clean)
            )  # 뒤 데이터셋의 조인 짝 후보로 등록
            cleaned[did] = clean
            for w in wl_resolved:  # 생산자 정리 요청 적재
                if w.get("from_dataset"):
                    prune_reqs.append(
                        {
                            "producer": w["from_dataset"],
                            "producer_col": w["from_column"],
                            "consumer": did,
                            "consumer_col": w["key_col"],
                            "normalize": w.get("normalize", "none"),
                        }
                    )
            out_base = os.path.join(
                STEP2_OUTPUT_DIR, f"{prefix + '_' if prefix else ''}clean_{did}"
            )
            _clear_stale(out_base)  # 옛 형식 파일 잔재 제거
            out_path, fmt = _save_dataset(clean, out_base, csv_preview)

            report.append(
                {
                    "dataset_id": did,
                    "filename": prof.get("filename"),
                    "roles": [x.get("role") for x in r.get("roles", [])],
                    "reference_only": ref_only,
                    "gis_input": not ref_only,  # 위치선정 입력 여부
                    "rows_before": before,
                    "rows_after": len(clean),
                    "drop_ratio": round(drop_ratio, 4),
                    "n_ops": len(ops),
                    "n_flags": len(flags),
                    "flags": [_flag_to_dict(x) for x in flags],
                    "op_logs": [asdict(log) for log in logs],
                    "output": out_path,
                    "format": fmt,
                    "params_autofilled": autofilled,
                    "whitelist_resolved": wl_resolved,
                    "unknown_ops": unknown_ops,
                    "status": status,
                }
            )
            tags = []
            if ref_only:
                tags.append("reference_only·GIS제외")
            if status == "no_ops":
                tags.append("!! 정제규칙 0건 — 원본 그대로 통과")
            if status == "empty_result":
                tags.append("!!! 결과 0행 — 필터 조건이 데이터와 불일치 의심")
            if status == "near_empty":
                tags.append(
                    f"!! {drop_ratio:.1%} 소실, {len(clean)}행만 남음 — 확인 필요"
                )
            if unknown_ops:
                tags.append(f"!! 미등록op 건너뜀: {unknown_ops}")
            if any(f.type == "missing_whitelist" for f in flags):
                tags.append("!! whitelist 생산자 없음 — 필터 건너뜀")
            if any(f.type == "filter_no_match" for f in flags):
                tags.append("!! 필터 허용값이 데이터에 없음 — 건너뜀")
            if autofilled:
                tags.append(f"coord_cols 자동주입 {len(autofilled)}건")
            for w in wl_resolved:
                if w.get("from_dataset"):
                    tags.append(
                        f"whitelist '{w['whitelist']}' ← "
                        f"{w['from_dataset']}.{w['from_column']}({w['matched_keys']}키"
                        + (
                            f", normalize={w['normalize']}"
                            if w.get("normalize_changed")
                            else ""
                        )
                        + f", cover {w['cover']:.0%})"
                    )
                elif "순환" in w.get("reason", ""):
                    tags.append(f"!! whitelist '{w['whitelist']}' 순환참조 — op 제거")
                else:
                    tags.append(f"!! whitelist '{w['whitelist']}' 자동연결 실패")
            print(
                f"  [{did}] {prof.get('filename', '')}: {before}→{len(clean)}행, "
                f"flag {len(flags)}개, {fmt} [{status}] "
                + (" ".join("(" + t + ")" for t in tags))
            )
        except Exception as e:  # 한 데이터셋 실패가 전체를 멈추지 않음
            print(f"  [실패] {did}: {e}")
            report.append({"dataset_id": did, "status": "error", "error": str(e)})

    # ── 후처리: 생산자에서 '소비자에 실제 데이터가 없는' 행 제거 ───────────────
    #   예) 용산 버스정류소 336개 중 승하차 통계에 한 번도 안 나오는 22개(폐지·미운행)
    #   → 수요 데이터가 없는 지점은 입지 후보/가중치 계산에서 잡음이므로 정리한다.
    if prune:
        by_out = {r["dataset_id"]: r for r in report if r.get("status") != "error"}
        for q in prune_reqs:
            pdid, cdid = q["producer"], q["consumer"]
            pdf, cdf = cleaned.get(pdid), cleaned.get(cdid)
            if pdf is None or cdf is None:
                continue
            if (
                q["producer_col"] not in pdf.columns
                or q["consumer_col"] not in cdf.columns
            ):
                continue
            have = _norm_vals(cdf[q["consumer_col"]], q["normalize"])
            keys = _norm_vals_series(pdf[q["producer_col"]], q["normalize"])
            keep = keys.isin(have)
            n_drop = int((~keep).sum())
            if n_drop == 0:
                continue
            pruned = pdf[keep].copy()
            cleaned[pdid] = pruned
            rec = by_out.get(pdid)
            base = os.path.join(
                STEP2_OUTPUT_DIR, f"{prefix + '_' if prefix else ''}clean_{pdid}"
            )
            _clear_stale(base)
            out_path, fmt = _save_dataset(pruned, base, csv_preview)
            print(
                f"  [{pdid}] 정리: {len(pdf)}→{len(pruned)}행 "
                f"({cdid} 에 데이터 없는 {n_drop}건 제거)"
            )
            if rec is not None:
                rec["rows_after"] = len(pruned)
                rec["output"], rec["format"] = out_path, fmt
                rec["pruned_by"] = {
                    "consumer": cdid,
                    "dropped": n_drop,
                    "reason": f"{cdid} 에 대응 데이터가 없는 키",
                }

    # whitelist 요약(생산된 것)
    wl_summary = {k: len(v) for k, v in wl.items()}

    # 산출물 배열을 dataset_id 기준 정렬 (처리순 → id순).
    # 소비 측에서 위치 인덱스로 접근할 때의 오정렬을 막기 위해 정렬해 저장.
    report.sort(key=lambda r: r.get("dataset_id", ""))

    out_report = os.path.join(
        STEP2_OUTPUT_DIR, f"{prefix + '_' if prefix else ''}clean_report.json"
    )
    with open(out_report, "w", encoding="utf-8") as f:
        json.dump(
            {
                "_schema": {
                    "설명": "STEP3 정제 실행 리포트. dataset별 정제 결과·flag·op로그.",
                    "gis_input": "위치선정 GIS 입력 대상 여부(reference_only면 false)",
                    "whitelists_생산": wl_summary,
                },
                "facility": facility,
                "region": region,
                "results": report,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    from collections import Counter

    cnt = Counter(r.get("status") for r in report)
    print(
        f"\n[정제 완료] ok {cnt['ok']} / near_empty {cnt['near_empty']} / "
        f"empty_result {cnt['empty_result']} / no_ops {cnt['no_ops']} / "
        f"partial {cnt['partial']} / error {cnt['error']}  (총 {len(results)})"
    )
    if cnt["empty_result"]:
        print(
            "  ※ empty_result = 결과가 0행. GIS 입력에서 그 레이어가 통째로 빠진다. "
            "cleaning_ops 의 필터 조건을 확인하세요(리포트 op_logs 참고)."
        )
    if cnt["no_ops"]:
        print(
            "  ※ no_ops = 감리가 cleaning_ops 를 내지 않은 데이터셋. "
            "원본이 그대로 복사됐으니 감리 결과를 확인하세요."
        )
    print(f"  리포트 → {out_report}")
    if wl_summary:
        print(f"[whitelist 생산] {wl_summary}")
    return out_report


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags_ = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print("사용법: python clean_data.py <도메인폴더> [--csv-preview] [--no-prune]")
        sys.exit(1)
    try:
        clean_domain(
            args[0],
            csv_preview="--csv-preview" in flags_,
            prune="--no-prune" not in flags_,
        )
    except Exception:
        print("\n[중단] 정제 오류:")
        traceback.print_exc()
        sys.exit(1)
