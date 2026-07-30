# -*- coding: utf-8 -*-
"""
OmniSite 감리 AI — 연산 카탈로그 (확장형 레지스트리) v4
======================================================

v4 변경 (STEP 2 정제 착수 전, 지역판정 하드코딩 제거)
  [삭제] EXPECTED_RECIPES        : 채점 구조 폐기됨 → 정답표 제거(하네스도 정리)
  [삭제] DISTRICT_BBOX 의존       : 구별 bbox 좌표를 손으로 박던 하드코딩 소스 제거
  [삭제] filter_bbox op          : bbox 폴백의 사전필터 → 폴백 폐지로 제거 (op 13→12)
  [변경] reverse_geocode          : filter_bbox 의존 제거 (독립 폴백으로 유지)
  [변경] spatial_join_admin       : params.shp_path 제거 → ctx.adm_shp_path 단일 소스
  [변경] validate_geocode         : bbox 검사 → SHP within + 최빈 ADM_CD 자동 판정
        · (1) 전국 유효범위(위33~39·경124~132) 밖·null → coord_invalid
        · (2) 행정동 경계 어디에도 안 듦 → coord_out_of_admin
        · (3) ADM_CD 앞5자리 최빈값 = 대상 구, 그와 다른 행 → coord_wrong_district
        · region 이름도 구코드 매핑도 불필요 — 데이터가 스스로 대상 구를 알려줌
        · target_admin_prefix 로 대상 구를 고정할 수도 있음(HITL 확정 시)

v3 유지 (버그 수정)
  [1] 같은 op 2회 실행 가능(리스트 순서 그대로) [2] stage 안정정렬+depends_on 위반 에러
  [3] 도메인 기본값 제거 [4] OpContext 스키마 [5] crs_transform SHP 재로딩 금지
  [6] run_geocode 검증컬럼+재시도 [7] 컬럼부재 명시적 에러  [+] execute 실행로그

설계 원칙
  - 감리 AI(LLM)는 "무엇을/어떻게 정제할지"만 판정하고 규칙 JSON을 낸다.
  - 실제 행 변환은 이 모듈의 결정론 op(run 함수)만 수행한다. LLM은 코드를 실행하지 않는다.
  - op 본문에 데이터셋별 분기/도메인 기본값을 넣지 않는다. 전부 params/ctx로 받는다.
  - op 추가 = register_op 엔트리 1개. 감리 코어/프롬프트 수정 0.

의존성: pandas, requests, geopandas(공간 op)
설정/비밀값: 코드에 상수를 박지 않는다. 모두 config.py / .env 에서 로드.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd
import requests

from app.config import (
    VWORLD_KEY,
    VWORLD_ENDPOINT,
    ADM_DONG_SHP,
    SIGUNGU_SHP,
    ADM_CODE_MAP,
    DISPLAY_CRS,
    GEOCODE_SLEEP_SEC,
    REVERSE_GEOCODE_SLEEP_SEC,
)

GEOCODE_RETRY = 2
GEOCODE_TIMEOUT = 10

# 전국 좌표 유효범위(WGS84). 특정 도메인이 아니라 '한국 어디든'에 통하는 지리 상수라
# 하드코딩이 아니다(모든 한국 좌표 데이터에 동일 적용). 필요 시 config 로 옮겨도 됨.
KOREA_LAT_RANGE = (33.0, 39.0)  # 위도(y)
KOREA_LNG_RANGE = (124.0, 132.0)  # 경도(x)


# ══════════════════════════════════════════════════════════════════
# 0. 레지스트리 인프라 + ctx 스키마
# ══════════════════════════════════════════════════════════════════


@dataclass
class HitlFlag:
    type: str
    severity: str
    row_id: int
    raw_text: str | None = None
    dong_guess: str | None = None
    approx_coord: list | None = None


@dataclass
class OpContext:
    """정제 op에 주입되는 도메인 단위 컨텍스트. **데이터셋 단위 값은 여기 넣지 않는다**
    (데이터셋별 값 = cleaning_ops[i].params 의 몫).

    누가 채우나
      facility, region : audit_result.json 의 facility_inference.{facility, region}
      domain           : 파이프라인 인자 (예: "흡연")
      adm_shp_path     : config.ADM_DONG_SHP (행정동 경계 — ADM_CD·ADM_NM)
      sigungu_shp_path : config.SIGUNGU_SHP  (시군구 경계 — SIGUNGU_CD·SIGUNGU_NM='용산구')
                         자치구명이 행정동 경계에는 없어서 별도 필요
      whitelists       : 정제 엔진이 앞선 데이터셋 결과로 만든 키 집합
                         (filter_by_join_key 전용. 없으면 {})
    """

    facility: str
    region: str
    domain: str
    adm_shp_path: str = ADM_DONG_SHP
    sigungu_shp_path: str = SIGUNGU_SHP
    whitelists: dict[str, list] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "facility": self.facility,
            "region": self.region,
            "domain": self.domain,
            "adm_shp_path": self.adm_shp_path,
            "sigungu_shp_path": self.sigungu_shp_path,
            "whitelists": self.whitelists,
        }


CTX_REQUIRED_KEYS = ("facility", "region", "domain", "adm_shp_path")


def _ctx(ctx: dict, key: str) -> Any:
    """ctx 필수값 접근. 없으면 조용히 넘어가지 않고 에러."""
    if key not in ctx or ctx[key] in (None, ""):
        raise ValueError(
            f"ctx.{key} 가 비어 있다. OpContext 를 채워서 넘길 것 "
            f"(필수: {', '.join(CTX_REQUIRED_KEYS)}). 기본값으로 대체하지 않는다."
        )
    return ctx[key]


def _require_cols(df: pd.DataFrame, cols: list[str], op_id: str) -> None:
    """params가 가리키는 컬럼이 실제 데이터에 있는지 검증.
    → 여기서 나는 에러는 'op 버그'가 아니라 '감리 AI params 오류'다. op를 고치지 말 것."""
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(
            f"[{op_id}] params가 가리키는 컬럼이 데이터에 없다: {missing}\n"
            f"  실제 컬럼: {list(df.columns)}\n"
            f"  → 원인은 감리 AI의 params 오류(2). op 코드를 고치지 말고 프롬프트/HITL에서 교정할 것."
        )


def _require_params(p: dict, keys: list[str], op_id: str) -> None:
    """필수 params 검증. 없으면 bare KeyError 대신 op·항목·스키마를 담은 에러.
    → 이 에러는 'op 버그'가 아니라 '감리 AI가 params 를 안 채운 것'(2)이다."""
    missing = [k for k in keys if k not in p or p[k] in (None, "", [])]
    if missing:
        schema = REGISTRY[op_id].params_schema if op_id in REGISTRY else {}
        raise ValueError(
            f"[{op_id}] 필수 params 누락: {missing}\n"
            f"  받은 params: {p}\n"
            f"  기대 스키마: {schema}\n"
            f"  -> 감리 AI가 파라미터를 채우지 않았다(2). op 코드를 고치지 말 것. "
            f"정제 엔진의 자동주입 대상이면 profile 값으로 채워진다."
        )


@dataclass
class BaseOp:
    """모든 정제 연산의 부모. run()만 구현하면 카탈로그에 자동 편입된다."""

    op_id: str
    applies_to: list  # tabular | point | polygon | any
    depends_on: list  # 선행 op_id (순서 검증용. 코드가 재배치하지 않는다)
    stage: int  # 실행 단계 (같은 stage 내 순서는 AI가 낸 순서 보존)
    description: str  # 감리 AI가 읽고 선택 판단하는 설명(프롬프트 주입)
    params_schema: dict
    run: Callable  # (df, params, ctx) -> (df, list[HitlFlag])


REGISTRY: dict[str, BaseOp] = {}


def register_op(op: BaseOp) -> BaseOp:
    if op.op_id in REGISTRY:
        raise ValueError(f"중복 op_id: {op.op_id}")
    REGISTRY[op.op_id] = op
    return op


def describe_all() -> list[dict]:
    """감리 AI 프롬프트에 주입할 카탈로그 서술(op_id·applies_to·depends_on·설명·params)."""
    return [
        {
            "op_id": o.op_id,
            "applies_to": o.applies_to,
            "depends_on": o.depends_on,
            "description": o.description,
            "params_schema": o.params_schema,
        }
        for o in REGISTRY.values()
    ]


# ══════════════════════════════════════════════════════════════════
# 1. 공통 유틸
# ══════════════════════════════════════════════════════════════════


def _normalize_addr(a) -> str:
    """주소 정규화. '서울 '->'서울특별시 ' 는 모든 한국 주소 데이터에 이득이라 정당(하드코딩 아님)."""
    if not isinstance(a, str):
        return ""
    a = " ".join(a.strip().split())
    if a.startswith("서울 "):
        a = "서울특별시 " + a[len("서울 ") :]
    return a


def _join_admin(
    df: pd.DataFrame,
    xcol: str,
    ycol: str,
    shp_path: str,
    code_col: str = "ADM_CD",
    name_col: str = "ADM_NM",
    sigungu_shp: str | None = None,
):
    """점 좌표(x=lng, y=lat, EPSG:4326)를 행정동 경계에 within 조인해 ADM_CD·ADM_NM 부여.
    sigungu_shp 를 주면 시군구 경계도 조인해 SIGUNGU_CD·SIGUNGU_NM('용산구')까지 붙인다.
    spatial_join_admin·validate_geocode 공용. 반환: (조인된 DataFrame, 조인실패 인덱스).
    ※ 성능: SHP(대용량)를 매 호출 로드한다. 잦아지면 세션 캐시로 최적화(지금은 정확성 우선)."""
    import geopandas as gpd

    bnd = gpd.read_file(shp_path)
    _require_cols(bnd, [code_col, name_col], "join_admin(경계SHP)")
    pts = gpd.GeoDataFrame(
        df.copy(),
        geometry=gpd.points_from_xy(
            pd.to_numeric(df[xcol], errors="coerce"),
            pd.to_numeric(df[ycol], errors="coerce"),
        ),
        crs=f"EPSG:{DISPLAY_CRS}",  # 4326 (입력 좌표 가정)
    ).to_crs(bnd.crs)  # -> 경계 CRS(5186)로 맞춰 조인
    joined = gpd.sjoin(
        pts, bnd[[code_col, name_col, "geometry"]], how="left", predicate="within"
    )
    joined = joined.drop(columns=["index_right"], errors="ignore")

    # 시군구 경계도 조인 → SIGUNGU_NM('용산구') 부여.
    #   행정동 경계에는 자치구'명'이 없어(ADM_NM 은 동 이름) 지역 필터를 걸 수 없다.
    #   과거 이 때문에 filter_by_value(col='ADM_NM', allowed=['용산구']) 가 0행을 냈다.
    if sigungu_shp:
        try:
            sgg = gpd.read_file(sigungu_shp)
            keep = [c for c in ("SIGUNGU_CD", "SIGUNGU_NM") if c in sgg.columns]
            if keep:
                joined = gpd.sjoin(
                    joined.to_crs(sgg.crs),
                    sgg[keep + ["geometry"]],
                    how="left",
                    predicate="within",
                )
                joined = joined.drop(columns=["index_right"], errors="ignore")
        except Exception as e:  # 시군구 경계가 없어도 행정동 결과는 살린다
            print(f"  [경고] 시군구 경계 조인 실패({e}) — SIGUNGU_NM 없이 진행")

    out = pd.DataFrame(joined.drop(columns=["geometry"], errors="ignore"))
    miss_idx = out.index[out[code_col].isna()]
    return out, miss_idx


def _addr_variants(addr: str) -> list[str]:
    """지오코딩 실패 시 시도할 주소 변형(원본 → 단순화 순). 한국 주소 전반에 통하는 규칙이라
    특정 데이터셋용 처리가 아니다. 실제 실패 사례에서 도출:
      '...성수이로 51 서울숲한라시그마벨리 지하1층'  → 건물명·층
      '...마장로39길 7 (마장동)'                    → 괄호 법정동
      '...동일로 151 2층 (성수동2가 앰코코리아)'      → 층 + 괄호
    변형:
      1) 원본
      2) 괄호와 그 안 내용 제거
      3) '<도로명> <건물번호>' 까지만 남김(뒤의 건물명·동·층 절단)
    """
    out, seen = [], set()

    def _add(v: str):
        v = " ".join(str(v).split())
        if v and v not in seen:
            seen.add(v)
            out.append(v)

    _add(addr)
    no_paren = re.sub(r"\([^)]*\)", " ", addr)
    _add(no_paren)
    # 마지막 '…로/길 + 번호(-번호)' 까지만. greedy 라 가장 뒤의 도로명+번호를 잡는다.
    m = re.match(r"^(.*[로길]\s*\d+(?:-\d+)?)", no_paren)
    if m:
        _add(m.group(1))
    return out


def _vworld_geocode(address: str, addr_type: str) -> tuple | None:
    """주소->(lat, lng, matched). addr_type: road|parcel. 재시도 2회."""
    address = _normalize_addr(address)
    if not address or not VWORLD_KEY:
        return None
    params = dict(
        service="address",
        request="getcoord",
        version="2.0",
        crs="epsg:4326",
        address=address,
        refine="true",
        simple="false",
        format="json",
        type=addr_type,
        key=VWORLD_KEY,
    )
    for attempt in range(GEOCODE_RETRY + 1):
        try:
            resp = requests.get(
                VWORLD_ENDPOINT, params=params, timeout=GEOCODE_TIMEOUT
            ).json()["response"]
        except Exception:
            if attempt < GEOCODE_RETRY:
                time.sleep(GEOCODE_SLEEP_SEC * (attempt + 1))
                continue
            return None
        if resp.get("status") != "OK":
            return None  # 주소 자체가 매칭 실패 -> 재시도 무의미
        try:
            pt = resp["result"]["point"]
            matched = resp.get("refined", {}).get("text", address)
            return float(pt["y"]), float(pt["x"]), matched  # lat, lng
        except (KeyError, ValueError, TypeError):
            return None
    return None


def _vworld_sigungu(x, y) -> str | None:
    """좌표(x=lng, y=lat)->시군구(level2)."""
    if not VWORLD_KEY:
        return None
    params = dict(
        service="address",
        request="getAddress",
        version="2.0",
        crs="epsg:4326",
        point=f"{x},{y}",
        format="json",
        type="parcel",
        key=VWORLD_KEY,
    )
    try:
        resp = requests.get(
            VWORLD_ENDPOINT, params=params, timeout=GEOCODE_TIMEOUT
        ).json()["response"]
    except Exception:
        return None
    if resp.get("status") != "OK":
        return None
    try:
        return resp["result"][0]["structure"].get("level2")
    except (KeyError, IndexError, TypeError):
        return None


# ══════════════════════════════════════════════════════════════════
# 2. 카탈로그 — 원자 op 12개
# ══════════════════════════════════════════════════════════════════


# -- op 1: crs_transform -------------------------------------------
def _run_crs_transform(df, p, ctx):
    import geopandas as gpd  # 지연 임포트(폴리곤 전용)

    if not isinstance(df, gpd.GeoDataFrame):
        raise TypeError(
            "[crs_transform] 입력이 GeoDataFrame이 아니다. SHP는 로더가 GeoDataFrame으로 "
            "읽어서 넘겨야 한다."
        )
    return df.to_crs(epsg=p.get("target_epsg", DISPLAY_CRS)), []


register_op(
    BaseOp(
        op_id="crs_transform",
        applies_to=["polygon", "point"],
        depends_on=[],
        stage=2,
        description="SHP 좌표계를 목표 EPSG(기본 4326)로 변환. 연속지적도(5186)·행정동경계 등.",
        params_schema={"target_epsg": "int=4326"},
        run=_run_crs_transform,
    )
)


# -- op 2: run_geocode ---------------------------------------------
def _run_geocode(df, p, ctx):
    _require_params(p, ["address_cols"], "run_geocode")
    _require_cols(df, list(p["address_cols"]), "run_geocode")
    order = p.get(
        "type_order", ["road", "parcel"]
    )  # 데이터마다 다름 (도로우선/지번우선)
    addr_cols = p["address_cols"]
    out_lat, out_lng = p.get("out_cols", ["위도", "경도"])
    _ctx(ctx, "region")  # 기본값 대체 없이 존재만 강제

    cache: dict = {}
    lats, lngs, srcs, matches, states, flags = [], [], [], [], [], []
    for idx, row in df.iterrows():
        raw = next(
            (
                row.get(c)
                for c in addr_cols
                if isinstance(row.get(c), str) and row.get(c).strip()
            ),
            "",
        )
        key = _normalize_addr(raw)
        if not key:
            lats.append(None)
            lngs.append(None)
            srcs.append(None)
            matches.append(None)
            states.append("EMPTY_ADDRESS")
            flags.append(
                HitlFlag(
                    type="geocode_failed",
                    severity="high",
                    row_id=int(idx),
                    raw_text=raw,
                )
            )
            continue
        if key in cache:
            res, used, vi = cache[key]
        else:
            res, used, vi = None, None, 0
            # 원본 주소가 실패하면 괄호 제거 → 도로명+건물번호 순으로 단순화해 재시도.
            # (건물명·층·괄호 법정동이 붙어 있으면 지오코더가 못 찾는 사례가 흔하다)
            for vi, cand in enumerate(_addr_variants(raw)):
                for t in order:
                    res = _vworld_geocode(cand, t)
                    if res:
                        used = t
                        break
                if res:
                    break
            cache[key] = (res, used, vi)
            time.sleep(GEOCODE_SLEEP_SEC)
        if res:
            lats.append(res[0])
            lngs.append(res[1])
            srcs.append(used)
            matches.append(res[2])
            st = "OK" if used == order[0] else f"OK({used} 폴백)"
            if vi:  # 주소를 단순화해서 성공한 경우 표시
                st += f"[주소정리{vi}]"
            states.append(st)
        else:
            lats.append(None)
            lngs.append(None)
            srcs.append(None)
            matches.append(None)
            states.append("FAIL")
            flags.append(
                HitlFlag(
                    type="geocode_failed",
                    severity="high",
                    row_id=int(idx),
                    raw_text=raw,
                )
            )

    df = df.copy()
    df[out_lat], df[out_lng] = lats, lngs
    # 자기검증(성공률·폴백률)에 필요한 컬럼
    df["지오코딩출처"], df["매칭주소"], df["지오코딩상태"] = srcs, matches, states
    return df, flags


register_op(
    BaseOp(
        op_id="run_geocode",
        applies_to=["tabular"],
        depends_on=[],
        stage=3,
        description="좌표 없고 주소만 있을 때 주소->좌표 생성. type_order로 도로/지번 우선순위 지정, "
        "주소 정규화·중복캐시·재시도 포함. 실패 시 괄호 제거·도로명+건물번호 절단으로 "
        "단순화해 재시도한다. 실패행은 hitl_flag(geocode_failed)로 방출. "
        "부가 컬럼 지오코딩출처·매칭주소·지오코딩상태를 함께 남긴다.",
        params_schema={
            "address_cols": "[str,...] (앞에서부터 우선 사용)",
            "type_order": "['road','parcel'] 또는 ['parcel','road']",
            "out_cols": "[lat_col, lng_col] = ['위도','경도']",
        },
        run=_run_geocode,
    )
)


# -- op 3: reverse_geocode -----------------------------------------
def _run_reverse_geocode(df, p, ctx):
    _require_params(p, ["coord_cols"], "reverse_geocode")
    _require_cols(df, list(p["coord_cols"]), "reverse_geocode")
    xcol, ycol = p["coord_cols"]
    out = p.get("out_col", "시군구")
    vals = []
    for _, row in df.iterrows():
        vals.append(_vworld_sigungu(row[xcol], row[ycol]))
        time.sleep(REVERSE_GEOCODE_SLEEP_SEC)
    df = df.copy()
    df[out] = vals
    return df, []


register_op(
    BaseOp(
        op_id="reverse_geocode",
        applies_to=["point"],
        depends_on=[],
        stage=4,
        description="[폴백 전용] 좌표->시군구(level2) 컬럼 추가(브이월드 API, 행 단위 호출). "
        "경계 SHP가 없을 때만 사용. 경계 SHP가 있으면 spatial_join_admin이 "
        "API 0회로 같은 일을 하며 행정동(ADM_CD·ADM_NM)까지 얻으므로 그쪽을 쓸 것.",
        params_schema={"coord_cols": "[x_col, y_col]", "out_col": "str='시군구'"},
        run=_run_reverse_geocode,
    )
)


# -- op 4: spatial_join_admin (지역 판정 기본 경로) ----------------
def _run_spatial_join_admin(df, p, ctx):
    """점 좌표 -> 행정동 경계 폴리곤 공간조인 -> ADM_CD·ADM_NM 부여. (API 0회)
    ※ 코드체계 주의: ADM_CD 는 통계청(SGIS) 체계. 행안부 체계와 값이 달라 코드 직결 금지."""
    _require_params(p, ["coord_cols"], "spatial_join_admin")
    _require_cols(df, list(p["coord_cols"]), "spatial_join_admin")
    xcol, ycol = p["coord_cols"]  # x=lng, y=lat (EPSG:4326 가정)
    shp = _ctx(ctx, "adm_shp_path")  # 단일 소스 (params 로 안 받음)
    out, miss_idx = _join_admin(
        df,
        xcol,
        ycol,
        shp,
        p.get("code_col", "ADM_CD"),
        p.get("name_col", "ADM_NM"),
        sigungu_shp=ctx.get("sigungu_shp_path"),
    )
    flags = [
        HitlFlag(type="admin_join_miss", severity="high", row_id=int(i))
        for i in miss_idx
    ]
    return out, flags


register_op(
    BaseOp(
        op_id="spatial_join_admin",
        applies_to=["point"],
        depends_on=[],
        stage=4,
        description="[지역 판정 기본] 점 좌표를 경계 폴리곤에 공간조인하여 네 컬럼을 부여한다: "
        "SIGUNGU_NM(자치구명 예 '용산구')·SIGUNGU_CD(예 '11030')·"
        "ADM_NM(행정동명 예 '이촌1동')·ADM_CD(행정동 8자리). API 호출 0회. "
        "'구' 컬럼 없는 좌표 데이터를 대상 자치구로 좁힐 때 쓴다. "
        "★ 대상 자치구 필터는 반드시 filter_by_value(col='SIGUNGU_NM', allowed=['<대상구>']) 로 할 것. "
        "ADM_NM 에는 동 이름만 들어 있어 자치구명으로 거르면 결과가 0행이 된다. "
        "행정동 단위로 더 좁히려면 ADM_NM 을 쓴다. "
        "코드는 SGIS 체계라 행안부 코드와 직결 금지(이름/공간조인으로 연결). "
        "경계 SHP 경로는 코드가 ctx 에서 채운다(params 로 지정하지 말 것).",
        params_schema={
            "coord_cols": "[x_col, y_col]",
            "code_col": "str='ADM_CD'",
            "name_col": "str='ADM_NM'",
        },
        run=_run_spatial_join_admin,
    )
)


# -- op 5: filter_by_value -----------------------------------------
def _run_filter_by_value(df, p, ctx):
    _require_params(p, ["col", "allowed"], "filter_by_value")
    _require_cols(df, [p["col"]], "filter_by_value")
    col, allowed = p["col"], set(p["allowed"])
    vals = df[col].astype(str).str.strip()
    keep = vals.isin(allowed)

    # 허용값이 그 컬럼에 **하나도 없으면** 필터가 잘못 지정된 것이다.
    #   실제 사례: 행정동 통계표의 '행정기관' 컬럼(값: 합계·왕십리제2동…)에
    #   allowed=['성동구'] 를 걸어 18행이 0행이 됐다. 그대로 적용하면 레이어가 통째로 사라진다.
    #   → 적용하지 않고 flag 로 크게 알린다(데이터를 죽이지 않고 사람이 판단하게).
    if len(df) and not keep.any():
        sample = sorted(vals.dropna().unique().tolist())[:8]
        return df, [
            HitlFlag(
                type="filter_no_match",
                severity="high",
                row_id=-1,
                raw_text=f"'{col}' 에 허용값 {sorted(allowed)} 이 하나도 없어 필터를 건너뜀. "
                f"실제 값(일부): {sample}",
            )
        ]
    return df[keep].copy(), []


register_op(
    BaseOp(
        op_id="filter_by_value",
        applies_to=["any"],
        depends_on=[],
        stage=5,
        description="지정 컬럼 값이 허용목록에 드는 행만 남긴다. 시군구명 필터, 운영현황 필터, "
        "허용값이 그 컬럼에 하나도 없으면 필터를 적용하지 않고 flag 를 낸다(레이어 보호). "
        "spatial_join_admin/reverse_geocode 결과 필터 등에 공통 사용. "
        "**한 데이터셋에 두 번 이상 쓸 수 있다**(예: 자치구 필터 후 운영현황 필터). "
        "그 경우 필요한 순서대로 배열에 두 번 넣을 것.",
        params_schema={"col": "str", "allowed": "[str,...]"},
        run=_run_filter_by_value,
    )
)


# -- op 6: filter_by_address_contains ------------------------------
def _run_filter_by_address_contains(df, p, ctx):
    _require_params(p, ["addr_cols", "contains"], "filter_by_address_contains")
    _require_cols(df, list(p["addr_cols"]), "filter_by_address_contains")
    sub = p["contains"]
    mask = pd.Series(False, index=df.index)
    for c in p["addr_cols"]:
        mask = mask | df[c].fillna("").astype(str).str.contains(sub, regex=False)
    return df[mask].copy(), []


register_op(
    BaseOp(
        op_id="filter_by_address_contains",
        applies_to=["any"],
        depends_on=[],
        stage=5,
        description="도로명/지번 주소 컬럼 중 하나라도 지정 문자열을 포함하는 행만 남긴다. "
        "'시군구명' 컬럼이 없는 표준데이터의 자치구 필터용.",
        params_schema={"addr_cols": "[str,...]", "contains": "str"},
        run=_run_filter_by_address_contains,
    )
)


# -- op 7: filter_by_code_prefix -----------------------------------
def _run_filter_by_code_prefix(df, p, ctx):
    _require_params(p, ["col", "prefix"], "filter_by_code_prefix")
    _require_cols(df, [p["col"]], "filter_by_code_prefix")
    keep = df[p["col"]].astype(str).str.startswith(p["prefix"])
    return df[keep].copy(), []


register_op(
    BaseOp(
        op_id="filter_by_code_prefix",
        applies_to=["tabular"],
        depends_on=[],
        stage=5,
        description="코드 컬럼이 특정 접두로 시작하는 행만 남긴다. 행안부 행정동코드 앞 5자리 "
        "(자치구 코드) 필터 등. 접두 코드는 params로 받는다.",
        params_schema={"col": "str", "prefix": "str"},
        run=_run_filter_by_code_prefix,
    )
)


_ADM_NAMES_CACHE: dict | None = None


def _admin_names_of(region: str) -> list[str]:
    """코드표에서 해당 자치구의 행정동명 목록. 세션 1회 로드 후 캐시."""
    global _ADM_NAMES_CACHE
    if _ADM_NAMES_CACHE is None:
        _ADM_NAMES_CACHE = {}
        try:
            import pandas as _pd

            df = _pd.read_excel(
                ADM_CODE_MAP, sheet_name="행정동코드", dtype=str, skiprows=1
            )
            df.columns = [
                "통계청행정동코드",
                "행자부행정동코드",
                "시도명",
                "시군구명",
                "행정동명",
            ][: len(df.columns)]
            for gu, dong in zip(df["시군구명"], df["행정동명"]):
                if isinstance(gu, str) and isinstance(dong, str):
                    _ADM_NAMES_CACHE.setdefault(gu.strip(), []).append(dong.strip())
        except Exception as e:
            print(
                f"  [경고] 행정동 코드표 로드 실패({e}) — filter_by_admin_name 사용 불가"
            )
    return _ADM_NAMES_CACHE.get(region, [])


def _norm_dong(v) -> str:
    """행정동명 표기 정규화. 같은 동을 자료마다 다르게 적어 그냥은 매칭되지 않는다.
    실측 대조(성동구 인구현황 ↔ 행정동 코드표)에서 도출:
        '행당제1동'   → '행당1동'      ('제' + 숫자 → 숫자)
        '성수1가제1동' → '성수1가1동'
        '왕십리제2동'  → '왕십리2동'
        '금호2·3가동'  → '금호2.3가동'  (가운뎃점 표기 차이)
        '마장동   '    → '마장동'       (자릿수 맞춤 공백)
    """
    s = re.sub(r"\s+", "", str(v))
    s = re.sub(r"제(\d)", r"\1", s)
    return s.replace("·", ".").replace("ㆍ", ".")


def _run_filter_by_admin_name(df, p, ctx):
    """행정동명 컬럼을 대상 자치구의 행정동 목록과 대조해 그 동에 속한 행만 남긴다.

    왜 필요한가 — 행정동 통계표에는 '자치구' 표현이 아예 없다(값이 '왕십리제2동' 뿐).
    여기에 filter_by_value(allowed=['성동구']) 를 걸면 0행이 된다(실제 사고).
    코드 컬럼이면 filter_by_code_prefix 로 접두를 볼 수 있지만, 이름 컬럼은 그럴 수 없다.
    → 코드표(config.ADM_CODE_MAP)의 '그 자치구 행정동 목록'과 이름을 대조한다.
      부수 효과로 '합계'·'소계' 같은 집계 행이 자동으로 빠진다(행정동명이 아니므로).
      이걸 안 빼면 행정동별 합산 시 값이 두 배가 된다.

    안전장치: 매칭률이 min_cover 미만이면 필터를 적용하지 않고 flag 만 낸다.
      (이름 체계가 예상과 다를 때 레이어를 통째로 날리지 않기 위해)
    """
    _require_params(p, ["col"], "filter_by_admin_name")
    _require_cols(df, [p["col"]], "filter_by_admin_name")
    region = p.get("region") or _ctx(ctx, "region")
    names = _admin_names_of(region)
    if not names:
        return df, [
            HitlFlag(
                type="admin_name_map_missing",
                severity="high",
                row_id=-1,
                raw_text=f"'{region}' 의 행정동 목록을 코드표에서 찾지 못해 필터를 건너뜀 "
                f"(config.ADM_CODE_MAP 확인)",
            )
        ]

    target = {_norm_dong(n) for n in names}
    key = df[p["col"]].map(_norm_dong)
    keep = key.isin(target)
    cover = keep.mean() if len(df) else 0.0
    min_cover = float(p.get("min_cover", 0.5))
    if cover < min_cover:
        sample = sorted(key[~keep].unique().tolist())[:8]
        return df, [
            HitlFlag(
                type="admin_name_low_match",
                severity="high",
                row_id=-1,
                raw_text=f"'{p['col']}' 값이 '{region}' 행정동과 {cover:.0%} 만 일치해 필터를 건너뜀. "
                f"미매칭 예: {sample}",
            )
        ]

    dropped = sorted(key[~keep].unique().tolist())
    flags = []
    if dropped:  # 보통 '합계' 같은 집계 행 — 무엇이 빠졌는지 남긴다
        flags.append(
            HitlFlag(
                type="admin_name_dropped",
                severity="low",
                row_id=-1,
                raw_text=f"행정동 아님으로 제외: {dropped[:10]}",
            )
        )
    return df[keep].copy(), flags


register_op(
    BaseOp(
        op_id="filter_by_admin_name",
        applies_to=["tabular"],
        depends_on=[],
        stage=5,
        description="행정동명 컬럼을 대상 자치구의 행정동 목록과 대조해 그 동의 행만 남긴다. "
        "행정동 통계표처럼 **자치구 표현이 없고 동 이름만 있는 데이터** 전용 "
        "(예: '행정기관' 컬럼 값이 '왕십리제2동'·'합계'). "
        "이런 데이터에 filter_by_value(allowed=['<자치구>']) 를 걸면 0행이 된다. "
        "표기 차이(행당제1동↔행당1동, 성수1가제1동↔성수1가1동)는 코드가 정규화한다. "
        "'합계'·'소계' 같은 집계 행도 자동으로 빠진다(행정동명이 아니므로) "
        "— 안 빼면 행정동별 합산 시 값이 두 배가 된다. "
        "코드 컬럼(행정동코드)이 있으면 filter_by_code_prefix 를 쓰고 이 op 는 이름 컬럼에만.",
        params_schema={
            "col": "행정동명 컬럼",
            "region": "str|null(=ctx.region)",
            "min_cover": "float=0.5 (이 미만 매칭이면 적용 안 하고 flag)",
        },
        run=_run_filter_by_admin_name,
    )
)


# -- op 8: filter_by_join_key --------------------------------------
def _norm_key(series, mode):
    s = series.astype(str).str.strip()
    if mode == "zfill5":
        return s.str.replace(r"\.0$", "", regex=True).str.zfill(5)
    if mode == "strip_paren":
        return s.map(lambda v: re.sub(r"\(.*?\)", "", v).strip())
    return s


def _run_filter_by_join_key(df, p, ctx):
    _require_params(p, ["key_col", "whitelist"], "filter_by_join_key")
    _require_cols(df, [p["key_col"]], "filter_by_join_key")
    whitelists = _ctx(ctx, "whitelists") if ctx.get("whitelists") else {}
    name = p["whitelist"]
    if name not in whitelists:
        # 감리 AI 가 소비(filter_by_join_key)만 지정하고 생산(emit_whitelist)을 안 넣은 경우.
        # 데이터셋 전체를 죽이지 않고 이 op 만 건너뛴다 → 다만 지역 필터가 안 걸린 상태이므로
        # missing_whitelist flag 로 크게 표시한다(원본 범위 그대로 남았을 수 있음).
        return df, [
            HitlFlag(
                type="missing_whitelist",
                severity="high",
                row_id=-1,
                raw_text=f"'{name}' 생산자 없음(보유: {sorted(whitelists)}) — 이 필터를 건너뜀. "
                f"다른 데이터셋에 emit_whitelist(name='{name}') 가 필요하다.",
            )
        ]
    mode = p.get("normalize", "none")
    wl = set(_norm_key(pd.Series(whitelists[name]), mode))
    key = _norm_key(df[p["key_col"]], mode)
    hit = df[key.isin(wl)].copy()
    miss = wl - set(key[key.isin(wl)].unique())
    flags = []
    if miss:  # 화이트리스트 커버 로그 -> HITL 참고(치명 아님)
        flags.append(
            HitlFlag(
                type="join_key_uncovered",
                severity="low",
                row_id=-1,
                raw_text=f"미매칭 키 {len(miss)}개: {sorted(miss)[:10]}",
            )
        )
    return hit, flags


register_op(
    BaseOp(
        op_id="filter_by_join_key",
        applies_to=["tabular"],
        depends_on=[],
        stage=5,
        description="다른 데이터에서 만든 화이트리스트(ctx.whitelists)에 키가 드는 행만 남긴다. "
        "키 정규화(zfill5=코드 5자리 0채움, strip_paren=괄호제거) 포함. "
        "좌표 없는 통계표를 대상 지역으로 좁힐 때 사용.",
        params_schema={
            "key_col": "str",
            "whitelist": "화이트리스트 이름(ctx.whitelists)",
            "normalize": "zfill5|strip_paren|none",
        },
        run=_run_filter_by_join_key,
    )
)


# -- op 8b: emit_whitelist (whitelist 생산) ------------------------
def _run_emit_whitelist(df, p, ctx):
    """이 데이터셋의 정제된 key_col 고유값을 ctx.whitelists[name] 에 저장한다.
    다음 데이터셋의 filter_by_join_key 가 이 목록을 소비한다. df 는 변형하지 않는다(부작용만).
    ※ 반드시 이 데이터셋의 필터(대상 지역 등)가 끝난 뒤 실행되어야 정제된 키만 나온다
      → stage 를 필터(5)보다 늦게 둔다. 엔진은 emit 데이터셋을 소비 데이터셋보다 먼저 처리한다."""
    _require_params(p, ["name", "key_col"], "emit_whitelist")
    _require_cols(df, [p["key_col"]], "emit_whitelist")
    keys = _norm_key(df[p["key_col"]], p.get("normalize", "none")).dropna()
    keys = [k for k in keys.unique().tolist() if k != ""]
    ctx.setdefault("whitelists", {})[p["name"]] = keys
    return df, []


register_op(
    BaseOp(
        op_id="emit_whitelist",
        applies_to=["any"],
        depends_on=[],
        stage=10,
        description="이 데이터셋의 key_col 고유값을 화이트리스트로 만들어 다음 데이터셋에 넘긴다. "
        "'코드만 있고 자치구명이 없는' 통계표를 대상 지역으로 좁혀야 할 때, "
        "먼저 경계·마스터 데이터에서 대상 지역의 코드 목록을 emit 하고 "
        "그 통계표에서 filter_by_join_key 로 소비한다. df 는 바꾸지 않는다. "
        "normalize 는 filter_by_join_key 와 같은 값을 써야 매칭된다(zfill5/strip_paren/none).",
        params_schema={
            "name": "화이트리스트 이름(filter_by_join_key.whitelist 와 일치)",
            "key_col": "str",
            "normalize": "zfill5|strip_paren|none",
        },
        run=_run_emit_whitelist,
    )
)


# -- op 0: trim_whitespace (문자열 정리, 가장 먼저) ----------------
def _is_text_col(sr) -> bool:
    """문자열 계열 컬럼인지. pandas 3.x 는 문자열 dtype 이 'str'(object 아님)이라
    dtype == object 비교만 하면 전부 건너뛴다(조용한 무동작). 두 경우를 모두 본다."""
    return pd.api.types.is_string_dtype(sr) or pd.api.types.is_object_dtype(sr)


def _run_trim_whitespace(df, p, ctx):
    """문자열 컬럼의 앞뒤 공백·중복 공백·비가시 문자(NBSP·BOM)를 제거.
    한국 공공데이터 전반에 통하는 범용 정리라 특정 데이터셋 분기가 아니다.
    cols 미지정이면 문자열 컬럼 전체에 적용(숫자 컬럼은 건드리지 않는다)."""
    df = df.copy()
    cols = p.get("cols")
    if cols:
        _require_cols(df, list(cols), "trim_whitespace")
    else:
        cols = [c for c in df.columns if _is_text_col(df[c])]
    for c in cols:
        if not _is_text_col(df[c]):
            continue
        df[c] = (
            df[c]
            .astype(str)
            .str.replace("\u00a0", " ", regex=False)  # NBSP
            .str.replace("\ufeff", "", regex=False)  # BOM
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
        )  # 중복 공백
        df[c] = df[c].replace({"nan": None, "None": None, "": None})
    return df, []


register_op(
    BaseOp(
        op_id="trim_whitespace",
        applies_to=["any"],
        depends_on=[],
        stage=1,
        description="문자열 컬럼의 앞뒤 공백·중복 공백·비가시문자(NBSP/BOM)를 제거하고 "
        "빈 문자열을 결측으로 통일한다. cols 를 안 주면 문자열 컬럼 전체에 적용. "
        "값 비교(filter_by_value)·키 매칭 전에 두면 공백 때문에 안 걸리는 문제를 막는다.",
        params_schema={"cols": "[str,...] | 생략시 문자열 컬럼 전체"},
        run=_run_trim_whitespace,
    )
)


# -- op 9: cast_numeric --------------------------------------------
def _run_cast_numeric(df, p, ctx):
    _require_params(p, ["cols"], "cast_numeric")
    _require_cols(df, list(p["cols"]), "cast_numeric")
    df = df.copy()
    for c in p["cols"]:
        df[c] = pd.to_numeric(
            df[c].astype(str).str.replace(",", "", regex=False), errors="coerce"
        )
    return df, []


register_op(
    BaseOp(
        op_id="cast_numeric",
        applies_to=["tabular"],
        depends_on=[],
        stage=6,
        description="문자로 적재된 수치 컬럼을 콤마 제거 후 숫자화. 승하차 인원·생활인구 등 통계 컬럼.",
        params_schema={"cols": "[str,...]"},
        run=_run_cast_numeric,
    )
)


# -- op 10: drop_null ----------------------------------------------
def _run_drop_null(df, p, ctx):
    _require_params(p, ["cols"], "drop_null")
    _require_cols(df, list(p["cols"]), "drop_null")
    action = p.get("action", "flag")
    mask = df[p["cols"]].isna().any(axis=1)
    if action == "drop":
        return df[~mask].copy(), []
    flags = [
        HitlFlag(type="null_required", severity="mid", row_id=int(i))
        for i in df.index[mask]
    ]
    return df, flags  # 기본은 경고만 (자동삭제하지 않는다)


register_op(
    BaseOp(
        op_id="drop_null",
        applies_to=["any"],
        depends_on=["run_geocode"],
        stage=7,
        description="필수 컬럼(좌표 등)이 널인 행을 flag(기본) 또는 drop. 기본은 자동삭제하지 않고 HITL로 넘김.",
        params_schema={"cols": "[str,...]", "action": "flag|drop"},
        run=_run_drop_null,
    )
)


# -- op 11: dedup --------------------------------------------------
def _run_dedup(df, p, ctx):
    _require_params(p, ["keys"], "dedup")
    _require_cols(df, list(p["keys"]), "dedup")
    return df.drop_duplicates(subset=p["keys"]).copy(), []


register_op(
    BaseOp(
        op_id="dedup",
        applies_to=["any"],
        depends_on=[],
        stage=8,
        description="지정 키 기준 중복 행 제거.",
        params_schema={"keys": "[str,...]"},
        run=_run_dedup,
    )
)


# -- op 12: validate_geocode ---------------------------------------
def _run_validate_geocode(df, p, ctx):
    """좌표 최종 검증(값 변경 없이 flag만). DISTRICT_BBOX 대신 SHP 폴리곤 + 최빈 판정.
      (1) 전국 유효범위 밖·null           -> coord_invalid
      (2) 행정동 경계 어디에도 안 듦        -> coord_out_of_admin
      (3) ADM_CD 앞5자리 최빈값=대상 구,    -> coord_wrong_district
          그와 다른 행(지오코딩 오매칭 의심)
    최종 단계라 이미 필터로 대상 구만 남은 상태 -> 최빈값이 곧 대상 구.
    region 이름·구코드 매핑 불필요. target_admin_prefix 로 고정도 가능."""
    _require_params(p, ["coord_cols"], "validate_geocode")
    _require_cols(df, list(p["coord_cols"]), "validate_geocode")
    xcol, ycol = p["coord_cols"]  # x=경도, y=위도
    x = pd.to_numeric(df[xcol], errors="coerce")
    y = pd.to_numeric(df[ycol], errors="coerce")

    flags: list[HitlFlag] = []
    lat_lo, lat_hi = KOREA_LAT_RANGE
    lng_lo, lng_hi = KOREA_LNG_RANGE

    # (1) 전국 유효범위 밖 or null
    bad = ~(y.between(lat_lo, lat_hi) & x.between(lng_lo, lng_hi)) | x.isna() | y.isna()
    flags += [
        HitlFlag(type="coord_invalid", severity="high", row_id=int(i))
        for i in df.index[bad]
    ]

    valid = df[~bad]
    if len(valid) == 0:
        return df, flags

    # (2) 유효 좌표만 행정동 경계에 within 조인
    shp = _ctx(ctx, "adm_shp_path")
    joined, miss_idx = _join_admin(
        valid, xcol, ycol, shp, sigungu_shp=ctx.get("sigungu_shp_path")
    )
    flags += [
        HitlFlag(type="coord_out_of_admin", severity="high", row_id=int(i))
        for i in miss_idx
    ]

    code = joined["ADM_CD"].dropna().astype(str)
    if code.empty:
        return df, flags
    sigungu = code.str[:5]

    # (3) 대상 구 = target_admin_prefix(고정) 또는 ADM_CD 앞5자리 최빈값(자동)
    target = p.get("target_admin_prefix") or sigungu.mode().iloc[0]
    wrong = joined.index[
        joined["ADM_CD"].notna() & (joined["ADM_CD"].astype(str).str[:5] != target)
    ]
    flags += [
        HitlFlag(type="coord_wrong_district", severity="mid", row_id=int(i))
        for i in wrong
    ]
    return df, flags  # 값은 안 바꾸고 flag만 -> HITL 검수


register_op(
    BaseOp(
        op_id="validate_geocode",
        applies_to=["point"],
        depends_on=["run_geocode"],
        stage=9,
        description="좌표 최종 검증(값 변경 없이 flag만). (1) 전국 유효범위 밖·null "
        "(2) 행정동 경계 밖 (3) 대상 자치구 아님(ADM_CD 앞5자리 최빈값 기준, "
        "지오코딩 오매칭 의심). target_admin_prefix 로 대상 구를 고정할 수 있고, "
        "없으면 데이터 최빈값을 대상 구로 자동 판정한다(region 매핑 불필요). "
        "경계 SHP 경로는 코드가 ctx.adm_shp_path 로 채운다.",
        params_schema={
            "coord_cols": "[x_col, y_col] (x=경도, y=위도)",
            "target_admin_prefix": "str|null(=자동: ADM_CD 앞5자리 최빈값)",
        },
        run=_run_validate_geocode,
    )
)


# ══════════════════════════════════════════════════════════════════
# 3. Executor — stage 안정 정렬 + depends_on 위반 검출
# ══════════════════════════════════════════════════════════════════


@dataclass
class OpLog:
    seq: int
    op_id: str
    params: dict
    rows_before: int
    rows_after: int
    n_flags: int
    elapsed_sec: float


def _plan_ops(cleaning_ops: list[dict]) -> list[dict]:
    """실행 계획 수립.
      · dict 로 뭉개지 않는다 -> 같은 op_id 2회 이상 허용
      · stage 기준 **안정 정렬** -> 같은 stage 내 AI 순서 보존
      · depends_on 위반은 재배치하지 않고 **에러**
      · 미등록 op(감리 AI 환각)는 예외 대신 건너뛰고 반환값으로 보고 -> 엔진이 flag 처리
    반환: (plan, unknown_op_ids)
    depends_on 은 '규칙에 함께 포함된 경우'에만 강제한다
    (예: 좌표 내장 데이터는 run_geocode 없이 validate_geocode 만 쓸 수 있다).
    """
    plan, unknown = [], []
    for i, op in enumerate(cleaning_ops):
        oid = op["op_id"]
        if oid not in REGISTRY:
            unknown.append(oid)  # 데이터셋 전체를 죽이지 않고 그 op 만 건너뜀
            continue
        plan.append({"ai_seq": i, "op_id": oid, "params": op.get("params") or {}})

    # 안정 정렬: Python sorted() 는 stable -> stage 동률이면 ai_seq 순서 그대로
    plan = sorted(plan, key=lambda o: REGISTRY[o["op_id"]].stage)

    # depends_on 위반 검출 (코드가 순서를 바꾸지 않고, 틀렸으면 알린다)
    chosen = {o["op_id"] for o in plan}
    seen: set[str] = set()
    for o in plan:
        for dep in REGISTRY[o["op_id"]].depends_on:
            if dep in chosen and dep not in seen:
                raise ValueError(
                    f"[순서 위반] '{o['op_id']}' 는 '{dep}' 뒤에 와야 한다. "
                    f"현재 계획: {[x['op_id'] for x in plan]}\n"
                    f"  -> 감리 AI의 cleaning_ops 순서 또는 op stage 정의를 확인할 것."
                )
        seen.add(o["op_id"])
    return plan, unknown


def execute(
    df, rule_json: dict, ctx: dict | OpContext
) -> tuple[pd.DataFrame, list[HitlFlag], list[OpLog]]:
    """감리 AI가 낸 rule_json['cleaning_ops'] 를 계획대로 적용.
    반환: (정제된 df, HITL flag 목록, op별 실행 로그)"""
    if isinstance(ctx, OpContext):
        ctx = ctx.as_dict()
    for k in CTX_REQUIRED_KEYS:
        _ctx(ctx, k)  # 실행 전에 ctx 검증

    plan, unknown = _plan_ops(rule_json.get("cleaning_ops") or [])
    all_flags: list[HitlFlag] = [
        HitlFlag(type="unknown_op", severity="high", row_id=-1, raw_text=oid)
        for oid in unknown
    ]  # 카탈로그에 없는 op 를 감리 AI 가 지어낸 경우
    logs: list[OpLog] = []

    for seq, o in enumerate(plan):
        before = len(df)
        t0 = time.perf_counter()
        df, flags = REGISTRY[o["op_id"]].run(df, o["params"], ctx)
        elapsed = time.perf_counter() - t0
        all_flags.extend(flags)
        logs.append(
            OpLog(
                seq=seq,
                op_id=o["op_id"],
                params=o["params"],
                rows_before=before,
                rows_after=len(df),
                n_flags=len(flags),
                elapsed_sec=round(elapsed, 2),
            )
        )
    return df, all_flags, logs


if __name__ == "__main__":
    print(f"등록된 op: {len(REGISTRY)}개")
    for o in REGISTRY.values():
        print(f"  - {o.op_id:26} stage={o.stage} depends_on={o.depends_on}")
