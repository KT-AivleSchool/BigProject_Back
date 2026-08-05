# -*- coding: utf-8 -*-
"""
OmniSite 가중치 모델 (STEP 5 · 감리/정제 → 최종 가중치)
========================================================
정제 산출물(step2_output) + 감리 결과(reviewed.json) 로부터
위치 선정에 쓸 최종 가중치를 만든다.  위치 선정 자체는 하지 않는다(다음 단계).

파이프라인
  [R] suggest_radius   : mini 가 facility+지표 rationale 로 집계반경 R 제안 → HITL 확정
  [A] define_indicators: positive/negative role + whitelist_resolved 로
                         좌표레이어(+)통계표 자동 병합 → 지표 K개  (도메인/번호 하드코딩 0)
  [B] build_matrix     : 후보 N × 지표 K.  반경 R 내 합산 / 행정동 지표는 소속 동 값
  [C] human_weights    : 감리 weight seed 정규화  (쌍대비교는 HITL-2에서 대체)
  [D] critic_weights   : Spearman-CRITIC.  희소지표(비영<5%) 제외.  부트스트랩 CI
  [E] synthesize       : w = (1-alpha)*w_human + alpha*w_critic
  [F] build_weight_set : 전 단계 근거 동봉 dict → JSON/DB

설계 원칙
  - R 은 하드코딩하지 않는다 — mini 제안 + HITL 확정, weight_set 에 기록해 재현.
  - 지표 병합 관계는 코드에 박지 않는다 — clean_report.whitelist_resolved 를 읽는다.
  - 가중치는 평가단위(후보집합)에 의존하지 않는 '지표 간 상대 중요도' — DB 저장 후 재사용.

--------------------------------------------------------------------
# TODO(설치 시 확인):
#   1) config import — 이 파일은 app/services/ 에 둔다는 전제.
#   2) ADM_DONG_SHP 의 행정동코드 컬럼명(_ADM_CODE_COL) 을 실제 SHP 에 맞춰라.
#   3) 생활인구 행정동코드 자릿수 <-> 경계 SHP 코드 자릿수 (_admin_code_match).
--------------------------------------------------------------------
"""
from __future__ import annotations
import os, json, re, glob
import hashlib
import time as _time
from datetime import datetime
import numpy as np
import pandas as pd
import geopandas as gpd

# --- config (설치 환경) ---
# 이 파일은 app/services/ 에 있으므로 app.config 를 절대경로로 임포트.
try:
    from app.config import (ADM_DONG_SHP, OPENAI_API_KEY, SEARCH_LLM_MODEL,
                            STEP2_OUTPUT_DIR, SPATIAL_CRS)
    # 가중치 산출물은 정제(step2)와 섞지 않고 step3_output 에 둔다.
    # config 에 STEP3_OUTPUT_DIR 이 있으면 그걸 쓰고, 없으면 step2 옆에 파생.
    try:
        from app.config import STEP3_OUTPUT_DIR as WEIGHT_OUTPUT_DIR
    except Exception:
        WEIGHT_OUTPUT_DIR = os.path.join(os.path.dirname(STEP2_OUTPUT_DIR), "step3_output")
    # 공용 지역 데이터 루트. find_region_file() 의 기본 탐색 경로다.
    #   예전엔 크로스워크 폴백 안에서만 `_RD` 로 잡혀서, config 에 ADMIN_CROSSWALK_PATH 가
    #   있으면 **아예 바인딩되지 않았다** → find_region_file(root=None) 이 NameError.
    #   모듈 스코프에서 항상 잡는다.
    try:
        from app.config import REGION_DATA_DIR
    except Exception:
        REGION_DATA_DIR = os.path.join(os.path.dirname(STEP2_OUTPUT_DIR), "region_data")
    REGION_DATA_DIR = str(REGION_DATA_DIR)        # config 는 Path — glob 에 문자열로 넘긴다
    # 행정동 코드 크로스워크(참조 데이터). config 에 없으면 region_data 에서 찾는다.
    try:
        from app.config import ADMIN_CROSSWALK_PATH
    except Exception:
        ADMIN_CROSSWALK_PATH = os.path.join(REGION_DATA_DIR, "행정동_크로스워크.csv")
except Exception:                                 # 단독 실행/테스트 폴백
    ADM_DONG_SHP = os.environ.get("ADM_DONG_SHP", "")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    SEARCH_LLM_MODEL = "gpt-4o-mini"
    STEP2_OUTPUT_DIR = os.environ.get("STEP2_OUTPUT_DIR", ".")
    WEIGHT_OUTPUT_DIR = os.environ.get("STEP3_OUTPUT_DIR", "./step3_output")
    ADMIN_CROSSWALK_PATH = os.environ.get("ADMIN_CROSSWALK_PATH",
                                          "./행정동_크로스워크.csv")
    REGION_DATA_DIR = os.environ.get("REGION_DATA_DIR", "./region_data")
    SPATIAL_CRS = 5186

WORK_CRS = SPATIAL_CRS                 # 미터 단위 작업 좌표계 (거리·버퍼) — config 와 통일


def rankdata(a: np.ndarray) -> np.ndarray:
    """scipy.stats.rankdata 대체(평균 순위, 동점 처리). 의존성 최소화용.
    a: 1차원 배열 → 1부터 시작하는 순위, 동점은 평균 순위."""
    a = np.asarray(a, dtype=float)
    order = a.argsort()
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1)
    # 동점 평균 처리
    _, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    sums = np.zeros(len(cnt)); np.add.at(sums, inv, ranks)
    return (sums / cnt)[inv]
_ADM_CODE_COL = "ADM_CD"               # TODO(2): 경계 SHP 의 행정동코드 컬럼명
SPARSE_THRESHOLD = 0.05                # 비영 비율 5% 미만 -> CRITIC 제외


class Timer:
    """단계별 소요 시간 기록 → 마지막에 표로 출력.
    (gam2_run_pipeline.Timer 와 같은 형식. STEP3·STEP4 가 공유한다)"""

    def __init__(self):
        self.laps: list[tuple[str, float]] = []
        self.t0 = _time.perf_counter()
        self._mark = self.t0

    def lap(self, name: str) -> float:
        now = _time.perf_counter()
        el = now - self._mark
        self._mark = now
        self.laps.append((name, el))
        return el

    @property
    def total(self) -> float:
        return _time.perf_counter() - self.t0

    def report(self, title: str = "소요 시간", import_sec: float = 0.0,
               start: float = 0.0) -> None:
        """import_sec: 라이브러리 임포트 시간(Timer 생성 전이라 랩에 안 잡힌다).
        start: 프로세스 기동 시각 — 주면 '체감 시간'을 함께 보여준다."""
        total = self.total
        rows = list(self.laps)
        if import_sec > 0:
            rows.insert(0, ("(임포트)", import_sec))
        denom = total + (import_sec if import_sec > 0 else 0)
        print("\n" + "=" * 60)
        print(f"[{title}]")
        print("-" * 60)
        for name, sec in rows:
            pct = (sec / denom * 100) if denom else 0
            bar = "█" * max(1, int(pct / 4))
            print(f"  {name:24} {sec:7.2f}s  {pct:5.1f}%  {bar}")
        print("-" * 60)
        print(f"  {'처리 합계':24} {total:7.2f}s")
        if start:
            print(f"  {'체감(기동~종료)':24} {_time.perf_counter()-start:7.2f}s")
        print("=" * 60)


# =========================================================
# 유틸: 표기 정규화 (gis_load 와 동일 규칙 — 조인 키 맞춤)
# =========================================================
def _norm_station(v) -> str:
    return re.sub(r"\(.*?\)", "", str(v)).strip()

def _norm_none(v) -> str:
    return str(v).strip()

_NORMALIZERS = {"none": _norm_none, "strip_paren": _norm_station}

_VALUE_HINT = ("승객", "승차", "하차", "인구", "수", "량", "건수")

def _pick_value_cols(df: pd.DataFrame) -> list:
    """통계표에서 합산할 수치 컬럼 자동탐지(식별자·좌표·코드 제외)."""
    skip = ("id", "코드", "번호", "일자", "노선", "역명", "좌표", "위도", "경도",
            "ID", "CD", "NM", "geometry")
    out = []
    for c in df.columns:
        if any(s in str(c) for s in skip):
            continue
        if pd.api.types.is_numeric_dtype(df[c]) and any(h in str(c) for h in _VALUE_HINT):
            out.append(c)
    return out


# =========================================================
# [R] 집계반경 제안 (mini) — 하드코딩 방지
# =========================================================
def suggest_radius(facility: str, indicators: list, model: str = None) -> dict:
    """facility 와 지표별 rationale 을 mini 에게 주고 집계반경 R(m) 을 제안받는다.
    반환: {indicator_id: {"radius_m": int|None, "rationale": str}} (confirmed=False).
    'admin' 지표는 반경 개념이 없으므로 None.  HITL 에서 확정 후 build_matrix 에 주입.

    조례에 없는 값(도보 동선 등 상식)이라 배제반경과 달리 법에서 못 뽑는다 ->
    시설 특성 기반 LLM 제안 + 사람 확정.  도메인마다 자릿수가 다르다
    (흡연 150 / 재활용 50~100 / EV 500~1000).
    """
    askable = [i for i in indicators if i.get("kind") != "admin"]
    payload = [{"id": i["id"], "설명": i.get("rationale", "")[:120]} for i in askable]

    if not OPENAI_API_KEY:
        return _mock_radius(facility, indicators)

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    m = model or SEARCH_LLM_MODEL
    prompt = (
        f"'{facility}' 입지 분석에서, 각 지표의 '수요 집계 반경(미터)'을 제안하라.\n"
        f"집계 반경 = 후보지 주변 몇 m 안의 해당 요소를 그 후보의 수요로 합칠지의 거리다.\n"
        f"시설 특성에 따라 자릿수가 다르다. 예: 도보로 잠깐 들르는 흡연부스 ~150m, "
        f"무거운 재활용을 들고 나오는 재활용정거장 ~50~100m, 차로 가는 EV충전소 ~500~1000m.\n"
        f"지표 성격도 반영하라(광역 유동인구는 넓게, 국소적 요소는 좁게).\n\n"
        f"[지표] {json.dumps(payload, ensure_ascii=False)}\n\n"
        f"JSON 하나만: {{\"<id>\": {{\"radius_m\": <정수>, \"rationale\": \"<한 문장>\"}}, ...}}"
    )
    try:
        resp = client.chat.completions.create(
            model=m, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}])
        out = json.loads(resp.choices[0].message.content)
    except Exception as e:
        print(f"  [R 제안 오류] {e} -> mock")
        return _mock_radius(facility, indicators)

    result = {}
    for i in indicators:
        if i.get("kind") == "admin":
            result[i["id"]] = {"radius_m": None, "rationale": "행정동 단위 지표(반경 무관)"}
        else:
            r = out.get(i["id"], {})
            result[i["id"]] = {"radius_m": r.get("radius_m"),
                               "rationale": r.get("rationale", "")}
    result["_confirmed"] = False
    return result

def _mock_radius(facility: str, indicators: list) -> dict:
    """키 없을 때 — 흡연 기준 기본값(수요형 150 / 국소형 100)."""
    demand = ("버스", "지하철", "정류", "역", "인구")
    out = {}
    for i in indicators:
        if i.get("kind") == "admin":
            out[i["id"]] = {"radius_m": None, "rationale": "행정동 단위(반경 무관)"}
        else:
            is_demand = any(k in i.get("rationale", "") for k in demand)
            out[i["id"]] = {"radius_m": 150 if is_demand else 100,
                            "rationale": "(mock) 수요형 150 / 국소형 100"}
    out["_confirmed"] = False
    return out


# =========================================================
# [A] 지표 정의
# =========================================================
# 크기 미정(`weight: null`)일 때의 슬라이더 초기 위치.
#   도메인 상수가 아니다 — 어떤 시설·지표에도 의미가 없는 중립점이고,
#   [W] 에서 사람이 확정하기 전까지의 자리표시일 뿐이다.
#   STEP1 HITL 이 방향만 확정하도록 바뀌면서(2026-07-31) 필요해졌다.
NEUTRAL_SEED = 0.5


def _seed_magnitude(w, did: str = "", role: str = "") -> float:
    """role['weight'] -> seed 크기(항상 >=0).

    방향은 role 이 정하고 크기는 |weight| 다. 부호가 role 과 어긋나면
    LLM 출력 형식 오류이므로 크기만 취하되 조용히 넘기지 않고 알린다.
    """
    if w is None:
        return NEUTRAL_SEED
    w = float(w)
    if (role == "positive_factor" and w < 0) or (role == "negative_factor" and w > 0):
        print(f"  ⚠ [{did}] {role} 인데 weight={w:+g} — 부호가 role 과 어긋납니다. "
              f"크기 {abs(w):g} 만 사용합니다.")
    return abs(w)


def define_indicators(reviewed: dict, report: dict) -> list:
    """positive/negative role 데이터셋을 지표로. WR 있으면 좌표레이어(+)통계표 병합."""
    wr_by_id = {}
    for r in report.get("results", []):
        wr = r.get("whitelist_resolved")
        if wr:
            wr_by_id[r["dataset_id"]] = wr[0]

    pos = {}
    for r in reviewed.get("results", []):
        did = r["dataset_id"]
        for role in (r.get("roles") or []):
            rt = role.get("role")
            if rt == "positive_factor":
                new = {"seed_weight": _seed_magnitude(role.get("weight"), did, rt),
                       "direction": "benefit", "rationale": role.get("rationale", "")}
            elif rt == "negative_factor":
                new = {"seed_weight": _seed_magnitude(role.get("weight"), did, rt),
                       "direction": "cost", "rationale": role.get("rationale", "")}
            else:
                continue
            # 같은 데이터셋에 상반된 역할이 동시에 붙으면 이전 코드는 뒤엣것으로
            # 조용히 덮어썼다(roles 리스트 순서에 결과가 좌우됨). 즉시 중단한다.
            prev = pos.get(did)
            if prev and prev["direction"] != new["direction"]:
                raise ValueError(
                    f"[{did}] 한 데이터셋에 상반된 역할이 동시에 판정됐습니다 "
                    f"({prev['direction']} / {new['direction']}).\n"
                    f"  감리 결과가 모순됩니다 — reviewed.json 의 해당 dataset "
                    f"roles 를 하나로 정리하세요.")
            pos[did] = new

    consumed = {wr["from_dataset"] for wr in wr_by_id.values()}

    indicators = []
    for did, meta in pos.items():
        if did in consumed:
            continue
        wr = wr_by_id.get(did)
        if wr:
            geo_id = wr["from_dataset"]; val_id = did
            geo_meta = pos.get(geo_id, {})
            geo_seed = geo_meta.get("seed_weight")
            geo_dir = geo_meta.get("direction")
            seed = np.mean([s for s in [geo_seed, meta["seed_weight"]] if s is not None])
            # 🔴 방향 충돌 — seed_weight 는 둘을 평균하는데 direction 은 val 쪽만 쓴다.
            #   geo 판정이 조용히 사라지므로 플래그를 남겨 [W] HITL 에서 사람이 확정한다.
            #   여기서 규칙으로 정하지 않는 이유: 어느 쪽이 옳은지는 도메인마다 다르다.
            conflict = None
            if geo_dir and geo_dir != meta["direction"]:
                conflict = {"geo_dataset": geo_id, "geo_direction": geo_dir,
                            "val_dataset": val_id, "val_direction": meta["direction"]}
            indicators.append({
                "id": f"{geo_id}+{val_id}", "kind": None,
                "geo_dataset": geo_id, "val_dataset": val_id,
                "join": {"geo_key": wr["from_column"], "val_key": wr["key_col"],
                         "normalize": wr.get("normalize", "none")},
                "seed_weight": round(float(seed), 3),
                "direction": meta["direction"], "rationale": meta["rationale"],
                "direction_conflict": conflict,
                "direction_llm": meta["direction"],
                "direction_source": "llm", "w_human_source": "llm",
                "adjusted_at": None})
        else:
            indicators.append({
                "id": did, "kind": None, "geo_dataset": did, "val_dataset": None,
                "join": None, "seed_weight": meta["seed_weight"],
                "direction": meta["direction"], "rationale": meta["rationale"],
                "direction_conflict": None,
                "direction_llm": meta["direction"],
                "direction_source": "llm", "w_human_source": "llm",
                "adjusted_at": None})
    return indicators


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
    from app.services.gam2_clean_data import _pick_lnglat   # 중복 정의 대신 재사용

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
            f"  추측해서 4326 으로 읽으면 공간조인이 조용히 0건이 됩니다.")
    else:
        raise ValueError(
            f"[{did}] 좌표가 한국 범위를 벗어납니다 "
            f"(경도 중앙 {xs.median():.3f}, 위도 중앙 {ys.median():.3f}).\n"
            f"  컬럼 짝({lng}/{lat})이 뒤바뀌었는지 확인하세요.")

    g = gpd.GeoDataFrame(df[ok].copy(), geometry=gpd.points_from_xy(xs, ys), crs=crs)
    dropped = int((~ok).sum())
    if verbose:
        note = f", 좌표결측 {dropped:,}행 제외" if dropped else ""
        print(f"  ⓘ [{did}] parquet -> geometry 복원 ({lng}/{lat}, EPSG:{crs}{note})")
    return g.to_crs(WORK_CRS)


def _norm_dong(s) -> pd.Series:
    """행정동명 표기 정규화. 같은 동이 소스마다 다르게 적힌다.

      '금호2·3가동' / '금호2ㆍ3가동' / '금호2,3가동' / '금호2.3가동'
      '왕십리도선동 ' / '성수1가 1동' / '용답동(용답)'

    가운뎃점·구분자·공백·괄호주석을 걷어내고 비교한다. 표기 규칙을 코드에 박는 게
    아니라 **양쪽에 같은 정규화를 걸어** 맞추는 것이므로 도메인 무관하다.
    """
    return (s.astype(str)
            .str.replace(r"\(.*?\)", "", regex=True)      # 괄호 주석
            .str.replace(r"[·ㆍ・∙,\.\-~/]", "", regex=True)  # 구분자
            .str.replace(r"\s+", "", regex=True)           # 공백
            .str.strip())


def _detect_admin_key_col(g, did: str = "", min_hit: float = 0.8) -> tuple:
    """행정동 조인 키 컬럼을 **값**으로 판정. 반환 (컬럼명, 종류, 매칭률, 진단).

    종류: "code"(행정동코드) | "name"(행정동명)

    이름으로 추측하지 않는 이유 —
      ["기관","동","ADM","코드"] 같은 키워드는 과다 매칭된다.
      '동' 은 자'동'차등록대수·활'동'인구에 걸리고, next() 는 첫 매칭을 쓰므로
      **컬럼 순서가 조인 키를 정하게 된다.** 결과는 나오고 행 수도 그럴듯해서
      조용히 틀린다(마포구 사건과 같은 계열).

    실측(성동구 07 인구현황): 컬럼이 '행정기관' 뿐이고 값은 '성수1가1동' 같은 **이름**이다.
      키워드 폴백이 '기관' 으로 이걸 집으면 groupby 는 이름으로 되는데
      build_matrix 는 경계 SHP 의 ADM_CD(숫자)로 조인하므로 **매칭 0건**이 된다.
      → 이름 컬럼도 정식으로 지원하고, 코드 변환은 크로스워크가 한다.
    """
    xw = load_admin_crosswalk()
    codes = set()
    for c in ("행정구역코드", "행정동코드", "행정동코드8"):
        if c in xw.columns:
            codes |= set(xw[c].dropna().astype(str).str.strip())
    names = (set(_norm_dong(xw["행정동명"].dropna()))
             if "행정동명" in xw.columns else set())

    hits, unmatched = {}, {}
    for c in g.columns:
        if c == "geometry":
            continue
        raw = g[c].astype(str).str.strip()
        s = raw.str.replace(r"\.0$", "", regex=True)
        if s.str.fullmatch(r"\d{5,10}").mean() >= 0.9:
            ok = s.isin(codes)
            hits[c] = ("code", float(ok.mean()))
        elif names and raw.str.endswith(("동", "읍", "면", "가")).mean() >= 0.7:
            nz = _norm_dong(raw)
            ok = nz.isin(names)
            hits[c] = ("name", float(ok.mean()))
        else:
            continue
        if not ok.all():
            unmatched[c] = sorted(set(raw[~ok]))[:8]

    good = {c: v for c, v in hits.items() if v[1] >= min_hit}
    # 코드가 이름보다 안전하다(동명이 여러 구에 있을 수 있다)
    for want in ("code", "name"):
        sel = {c: v for c, v in good.items() if v[0] == want}
        if len(sel) == 1:
            c, (kind, rate) = next(iter(sel.items()))
            return c, kind, rate, hits
        if len(sel) > 1:
            raise ValueError(
                f"[{did}] 행정동 {want} 후보가 여러 개입니다: "
                f"{ {c: f'{v[1]:.0%}' for c, v in sel.items()} }\n"
                f"  어느 것이 조인 키인지 확정할 수 없습니다 — 데이터를 확인하세요.")

    msg = [f"[{did}] 행정동 조인 키(코드 또는 이름)를 찾지 못했습니다.",
           f"  컬럼: {[c for c in g.columns if c != 'geometry']}",
           f"  후보 매칭률: "
           f"{ {c: f'{v[0]} {v[1]:.0%}' for c, v in hits.items()} if hits else '후보 없음' }"]
    for c, vals in unmatched.items():
        msg.append(f"  [{c}] 크로스워크에 없는 값 (최대 8개): {vals}")
    msg.append(f"  참조: {os.path.basename(ADMIN_CROSSWALK_PATH)}")
    raise ValueError("\n".join(msg))


def admin_names_to_codes(s, region: str = "", did: str = "") -> pd.Series:
    """행정동명 -> 행정구역코드(8자리). 시군구는 데이터에서 **최빈값으로 추론**한다.

    같은 동명이 여러 시군구에 있으므로(중앙동 등) 시군구를 좁히지 않으면 틀린다.
    region 이 주어지면 그것을 쓰고, 없으면 이름들이 가장 많이 속한 시군구를 택한다
    (validate_geocode 의 'ADM_CD 앞5자리 최빈값' 과 같은 패턴).
    """
    xw = load_admin_crosswalk()
    nm = _norm_dong(s)
    xnm = _norm_dong(xw["행정동명"])
    cand = xw[xnm.isin(set(nm))]
    if cand.empty:
        raise ValueError(f"[{did}] 행정동명이 크로스워크에 하나도 없습니다.")

    sgg = None
    if region:
        sgg = sgg_code_of(region)
    if not sgg:
        top = cand["행정구역코드"].astype(str).str[:5].value_counts()
        sgg = top.index[0]
        if len(top) > 1:
            print(f"  ⓘ [{did}] 행정동명 소속 시군구 최빈값 {sgg} 사용 "
                  f"({top.iloc[0]}/{top.sum()}건)")
    cand = cand[cand["행정구역코드"].astype(str).str[:5] == sgg]
    cnm = _norm_dong(cand["행정동명"])

    dup = int(cnm.duplicated().sum())
    if dup:
        raise ValueError(f"[{did}] 시군구 {sgg} 안에 같은 행정동명이 {dup}건 중복입니다.")

    m = dict(zip(cnm, cand["행정구역코드"].astype(str)))
    out = nm.map(m)
    miss = int(out.isna().sum())
    if miss:
        bad = sorted(set(s.astype(str)[out.isna()]))[:8]
        print(f"  ⚠ [{did}] 시군구 {sgg} 에서 코드 변환 실패 {miss}건 — {bad}")
    return out


def attach_layers(indicators: list, loader, admin_value_col: str = "총생활인구수",
                  admin_code_hint: str = "행정동코드", region: str = "",
                  verbose: bool = True) -> None:
    """loader(dataset_id) -> GeoDataFrame|DataFrame (EPSG:5186 재투영은 loader 책임).
    각 지표에 _points/_valcol(point) 또는 _admin_agg(admin) 를 심고 kind 확정.
    """
    for i in indicators:
        g = loader(i["geo_dataset"])
        is_geo = hasattr(g, "geometry") and "geometry" in getattr(g, "columns", [])

        if not is_geo:                                  # 좌표 없는 통계표 -> admin
            i["kind"] = "admin"
            code_col = next((c for c in g.columns if admin_code_hint in str(c)), None)
            code_src, key_kind = "hint", "code"
            if code_col is None:
                # 이름 힌트 실패 -> 값으로 판정(코드/이름). 못 찾거나 애매하면 raise.
                code_col, key_kind, rate, _ = _detect_admin_key_col(g, i["id"])
                code_src = f"crosswalk_{key_kind}"
                if verbose:
                    print(f"  [{i['id']}] 행정동 조인키 자동판정: '{code_col}' "
                          f"({key_kind}, 크로스워크 매칭 {rate:.0%})")
            i["_admin_code_source"] = code_src

            vcols = _pick_value_cols(g)
            if admin_value_col in g.columns:
                vcol = admin_value_col
            elif len(vcols) == 1:
                vcol = vcols[0]
            elif vcols:
                vcol = vcols[0]
                if verbose:
                    print(f"  ⚠ [{i['id']}] 값 컬럼 후보 {vcols} 중 '{vcol}' 사용 — 확인 필요")
            else:
                raise ValueError(
                    f"[{i['id']}] 집계할 수치 컬럼이 없습니다.\n"
                    f"  컬럼: {[c for c in g.columns if c != 'geometry']}")
            i["_admin_valcol_candidates"] = vcols

            g = g.copy()
            if key_kind == "name":
                # 이름 그대로 두면 build_matrix 의 코드 조인에서 0건이 된다 -> 코드로 변환
                g["_admcd"] = admin_names_to_codes(g[code_col], region, i["id"])
                g = g[g["_admcd"].notna()]
                code_col = "_admcd"
            g[vcol] = pd.to_numeric(g[vcol], errors="coerce")
            agg = g.groupby(code_col)[vcol].mean().reset_index()   # 시간대·일 평균
            i["_admin_agg"] = agg; i["_admin_code_col"] = code_col; i["_admin_valcol"] = vcol
            if verbose: print(f"  [{i['id']}] admin  code={code_col} val={vcol} dongs={len(agg)}")
            continue

        g = g[g.geometry.notna() & ~g.geometry.is_empty].copy()
        if i["val_dataset"]:                            # 병합 point_sum
            val = loader(i["val_dataset"])
            nf = _NORMALIZERS.get(i["join"]["normalize"], _norm_none)
            vcols = _pick_value_cols(val)
            days = val["사용일자"].nunique() if "사용일자" in val.columns else 1
            val = val.copy(); val["_k"] = val[i["join"]["val_key"]].map(nf)
            agg = val.groupby("_k")[vcols].sum().sum(axis=1) / max(days, 1)  # 일평균
            g["_k"] = g[i["join"]["geo_key"]].map(nf)
            g["_val"] = g["_k"].map(agg).fillna(0.0)
            i["kind"] = "point_sum"; i["_valcol"] = "_val"
            i["_points"] = g[["_val", "geometry"]].copy(); i["_days"] = int(days)
            if verbose: print(f"  [{i['id']}] point_sum  pts={len(g)} days={days} vcols={vcols}")
        else:                                           # 단독 점 -> 개수
            i["kind"] = "point_count"; i["_points"] = g[["geometry"]].copy()
            if verbose: print(f"  [{i['id']}] point_count  pts={len(g)}")


# =========================================================
# [B] 지표 행렬 — 후보 N × 지표 K
# =========================================================
def build_matrix(candidates: gpd.GeoDataFrame, indicators: list,
                 radius_m: dict, admin_gdf: gpd.GeoDataFrame = None,
                 default_radius: float = 150.0, verbose: bool = True,
                 decay: str | None = None, sigma_ratio: float = 1/3,
                 chunk: int = 20000) -> pd.DataFrame:
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
    cand = candidates.reset_index(drop=True).copy(); cand["_cid"] = range(len(cand))
    mat = pd.DataFrame({"_cid": cand["_cid"]})

    # 후보 중심좌표 — _cid 가 0..N-1 이라 위치 인덱스로 바로 접근 가능
    _cgeom = cand.geometry
    if not (_cgeom.geom_type == "Point").all():      # 폴리곤이 오면 내부 대표점
        _cgeom = _cgeom.representative_point()
    CX = _cgeom.x.to_numpy(); CY = _cgeom.y.to_numpy()

    cand_admcd = None
    if admin_gdf is not None and any(i["kind"] == "admin" for i in indicators):
        jn = gpd.sjoin(cand[["_cid", "geometry"]], admin_gdf[[_ADM_CODE_COL, "geometry"]],
                       how="left", predicate="within")
        cand_admcd = jn.groupby("_cid")[_ADM_CODE_COL].first()

    for i in indicators:
        iid = i["id"]
        _t0 = _time.perf_counter()

        # ---- admin 지표: 반경 개념이 없어 감쇠와 무관 (기존 로직 그대로) ----
        if i["kind"] == "admin":
            if cand_admcd is None:
                raise ValueError(f"[{iid}] admin 지표엔 admin_gdf(행정동 경계)가 필요합니다.")
            agg = i["_admin_agg"]; ccol = i["_admin_code_col"]; vcol = i["_admin_valcol"]
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
                if c in amap:                       # 같은 코드 체계
                    code_val[c] = amap[c]
                    continue
                t = x2a.get(c)                      # 코드 체계 변환
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
                    f"    {c}  {x2s.get(c,'?')} {x2d.get(c,'?')}  후보 {cnt.get(c,0):,}점"
                    f"  {'크로스워크 없음' if c in no_xwalk else '집계 테이블에 값 없음'}"
                    for c in sorted(fatal))
                raise ValueError(
                    f"[{iid}] 행정동 매칭 실패 — 후보 {n:,}/{len(cand):,} "
                    f"({n/len(cand)*100:.1f}%)\n{det}\n"
                    f"  대상 시군구: {main_sgg}   집계 테이블 코드 {len(amap)}종\n"
                    f"  0 으로 채우면 해당 동 후보가 이 지표에서 구조적으로 불리해집니다.")

            vals = codes.astype(str).map(code_val)
            mat[iid] = vals.fillna(0.0).values
            if verbose:
                hit = int((mat[iid] > 0).sum())
                mv = mat[iid][mat[iid] > 0]
                _el = _time.perf_counter() - _t0
                print(f"  [{iid}] admin  hit={hit}/{len(cand)}  mean={mv.mean():,.0f}"
                      f"  [{_el:.1f}s]"
                      if len(mv) else f"  [{iid}] admin hit=0  [{_el:.1f}s]")
                print(f"         행정동 {len(code_val)}종 매칭 "
                      f"({main_sgg}) · 크로스워크 {os.path.basename(ADMIN_CROSSWALK_PATH)}")
                for c in warn:
                    print(f"         ⓘ {c} {x2s.get(c,'?')} {x2d.get(c,'?')} "
                          f"후보 {cnt.get(c,0):,}점 — 대상 시군구 밖이라 0 처리")
            continue

        R = float(radius_m.get(iid) or default_radius)
        pts = i["_points"]

        # ---- 감쇠 OFF: 기존 경로 100% 동일 ----
        if decay is None:
            buf = cand[["_cid"]].copy(); buf["geometry"] = cand.geometry.buffer(R)
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
            _pts = pts.reset_index(drop=True)          # 위치 인덱스 보장
            if len(_pts):
                sigma = R * float(sigma_ratio)
                _pg = _pts.geometry
                PX = _pg.x.to_numpy(); PY = _pg.y.to_numpy()
                PV = (_pts[i["_valcol"]].to_numpy(dtype=float)
                      if i["kind"] == "point_sum" else None)

                for st in range(0, len(cand), chunk):
                    sl = cand.iloc[st:st + chunk]
                    buf = sl[["_cid"]].copy()
                    buf["geometry"] = sl.geometry.buffer(R)
                    buf = gpd.GeoDataFrame(buf, geometry="geometry", crs=cand.crs)
                    j = gpd.sjoin(_pts, buf, how="inner", predicate="within")
                    if len(j) == 0:
                        continue
                    cid = j["_cid"].to_numpy()
                    pi = j.index.to_numpy()               # _pts 위치 인덱스
                    d = np.hypot(PX[pi] - CX[cid], PY[pi] - CY[cid])
                    if decay == "gaussian":
                        w = np.exp(-(d * d) / (2.0 * sigma * sigma))
                    elif decay == "linear":
                        w = np.maximum(0.0, 1.0 - d / R)
                    else:
                        raise ValueError(f"decay 는 None/'gaussian'/'linear' 중 하나: {decay}")
                    if PV is not None:
                        w = w * PV[pi]
                    acc += np.bincount(cid, weights=w, minlength=len(cand))
            mat[iid] = acc

        if verbose:
            hit = int((mat[iid] > 0).sum())
            tag = i["kind"] if decay is None else f"{i['kind']}~{decay[:4]}"
            print(f"  [{iid}] {tag:<16} R={R:>4.0f}m  hit={hit}/{len(cand)} "
                  f"({hit/len(cand)*100:.0f}%)  max={mat[iid].max():,.1f}"
                  f"  [{_time.perf_counter()-_t0:.1f}s]")

    return mat.drop(columns="_cid")

_XWALK_CACHE: dict = {}


def sgg_code_of(region: str) -> str | None:
    """'서울특별시 성동구' -> '11200'. 크로스워크에서 조회(하드코딩 없음).

    지역명 표기가 흔들려도(용산구 / 서울특별시 용산구) 시군구명으로 맞춘다.
    동명 시군구가 여러 시도에 있으면(예: 중구) 시도명까지 일치해야 확정한다.
    """
    if not region:
        return None
    xw = load_admin_crosswalk()
    if "시군구명" not in xw.columns or "행정구역코드" not in xw.columns:
        return None
    r = str(region).strip()
    sub = xw[xw["시군구명"].astype(str).apply(lambda s: bool(s) and s in r)]
    if sub.empty:
        return None
    if "시도명" in sub.columns and sub["시군구명"].nunique() > 1:
        sub = sub[sub["시도명"].astype(str).apply(lambda s: bool(s) and s in r)]
    codes = sorted(set(sub["행정구역코드"].astype(str).str[:5]))
    if len(codes) != 1:
        raise ValueError(
            f"지역 '{region}' 의 시군구코드를 확정할 수 없습니다: {codes}\n"
            f"  '<시도명> <시군구명>' 형태로 지정하세요(예: '서울특별시 중구').")
    return codes[0]


def find_region_file(pattern: str, region: str = "", sgg_code: str | None = None,
                     root: str | None = None, must: bool = True) -> str | None:
    """지역 데이터 파일 탐색. **시군구코드로 고른다.**

    region_data/ 아래 지자체별 하위폴더(용산구/ · 성동구/)를 재귀 탐색한다.
    폴더명이 아니라 **파일명의 시군구코드**로 판정하므로 폴더 구성이 바뀌어도 동작한다.

      find_region_file("LSMD_CONT_LDREG_*.shp", "서울특별시 성동구")
        -> region_data/성동구/LSMD_CONT_LDREG_11200_202607.shp

    코드가 파일명에 없으면(국유부동산 CSV 등) **폴더명**으로 좁힌 뒤,
    후보가 여러 개면 중단한다 — 예전처럼 `hits[-1]` 로 아무거나 집으면
    다른 구 파일을 쓰고도 조용히 지나간다(마포구 사건과 같은 구조).
    """
    base = root or REGION_DATA_DIR
    code = sgg_code or (sgg_code_of(region) if region else None)
    hits = sorted(glob.glob(os.path.join(base, "**", pattern), recursive=True))

    if code:
        coded = [h for h in hits if code in os.path.basename(h)]
        if coded:
            hits = coded
        elif region:                       # 파일명에 코드가 없으면 폴더명으로
            gu = region.split()[-1]
            named = [h for h in hits if gu in h.replace("\\", "/").split("/")[:-1].__str__()]
            if named:
                hits = named

    if not hits:
        if not must:
            return None
        raise FileNotFoundError(
            f"지역 파일 없음: {pattern}\n"
            f"  지역: {region or '(미지정)'}"
            + (f" (시군구코드 {code})" if code else "") + "\n"
            f"  탐색: {os.path.join(base, '**', pattern)}\n"
            f"  region_data 하위에 해당 지자체 파일을 두세요.")

    if len(hits) > 1:
        # 같은 지역의 여러 연월이면 최신을 쓰되 알린다. 다른 지역이 섞였으면 중단.
        bns = {os.path.basename(h) for h in hits}
        if code and all(code in b for b in bns):
            pick = hits[-1]
            print(f"  ⚠ {pattern} 후보 {len(hits)}개 — 최신본 사용: {os.path.basename(pick)}")
            return pick
        raise ValueError(
            f"지역 파일 후보가 여러 개입니다 — 어느 지역인지 확정할 수 없습니다.\n  "
            + "\n  ".join(hits)
            + f"\n\n  지역 '{region or '(미지정)'}' 로는 좁혀지지 않습니다. "
              f"경로를 직접 지정하세요.")
    return hits[0]


def load_admin_crosswalk(path: str | None = None) -> pd.DataFrame:
    """행정동 코드 크로스워크. 프로세스당 파일별 1회만 읽는다.

    컬럼: 행정구역코드(통계청 8) · 행정동코드8(행자부) · 행정동명 · 시도명 · 시군구명

    없으면 만들라고 알리고 중단한다. 예전처럼 뒤 3자리 매칭으로 넘어가면
    맞은 것과 틀린 것이 섞인 채 조용히 지나간다(용산 실측 12/16).
    """
    p = path or ADMIN_CROSSWALK_PATH
    if p in _XWALK_CACHE:
        return _XWALK_CACHE[p]
    if not os.path.isfile(p):
        raise FileNotFoundError(
            f"행정동 크로스워크 없음: {p}\n"
            f"  경계 SHP(통계청 코드)와 집계 테이블(행자부 코드)은 같은 동에\n"
            f"  다른 번호를 씁니다. 변환표 없이는 admin 지표를 계산할 수 없습니다.\n"
            f"  생성: python make_admin_crosswalk.py <국가데이터처_법정동_연계정보.csv>")
    df = pd.read_csv(p, dtype=str)
    need = ["행정구역코드", "행정동코드8", "행정동명", "시군구명"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise ValueError(f"크로스워크 컬럼 없음 {miss}: {p}")
    _XWALK_CACHE[p] = df
    return df


def _admin_code_match(cd, amap: dict, xwalk: pd.DataFrame | None = None):
    """경계 SHP 코드 -> 집계 테이블 값.

    ① 완전 일치 (같은 코드 체계일 때)
    ② 크로스워크 변환 후 완전 일치

    ※ '뒤 3자리 매칭' 은 제거했다. 통계청/행자부는 같은 동에 다른 번호를 붙이고
      뒤 3자리가 겹치는 건 우연이다 — 용산구 실측 16개 중 12개만 맞았고
      청파·원효로1·한강로·한남 4개가 조용히 0 이 됐다(후보 39.3%).
    """
    if cd is None or (isinstance(cd, float) and np.isnan(cd)):
        return np.nan
    cd = str(cd)
    if cd in amap:                       # ① 같은 체계
        return amap[cd]
    if xwalk is not None:                # ② 코드 체계 변환
        hit = xwalk.loc[xwalk["행정구역코드"] == cd, "행정동코드8"]
        if len(hit):
            return amap.get(str(hit.iloc[0]), np.nan)
    return np.nan


# =========================================================
# [B2] 정규화
# =========================================================
def normalize_matrix(mat: pd.DataFrame, indicators: list,
                    scale: str = "minmax") -> pd.DataFrame:
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
def detect_sparse(mat: pd.DataFrame, indicators: list | None = None,
                  threshold: float = SPARSE_THRESHOLD) -> set:
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

def _safe_corr(Xr: np.ndarray) -> np.ndarray:
    """0-분산 컬럼이 있어도 NaN 없이 상관행렬. 분산 0인 열의 상관은 0으로."""
    m = Xr.shape[1]; std = Xr.std(axis=0); corr = np.eye(m)
    for a in range(m):
        for b in range(a + 1, m):
            if std[a] < 1e-12 or std[b] < 1e-12:
                r = 0.0
            else:
                r = np.corrcoef(Xr[:, a], Xr[:, b])[0, 1]
                r = 0.0 if np.isnan(r) else r
            corr[a, b] = corr[b, a] = r
    return corr

def critic_weights(norm: pd.DataFrame, sparse_ids: set = None, spearman: bool = True) -> dict:
    """CRITIC. 반환 {id: weight}. sparse_ids 제외."""
    cols = [c for c in norm.columns if not (sparse_ids and c in sparse_ids)]
    if not cols:
        return {}
    X = norm[cols].values
    Xr = np.column_stack([rankdata(X[:, j]) for j in range(X.shape[1])]) if spearman else X
    std = Xr.std(axis=0, ddof=1)
    corr = _safe_corr(Xr) if len(cols) > 1 else np.array([[1.0]])
    conflict = np.sum(1 - corr, axis=1)
    C = std * conflict
    if C.sum() <= 0:
        return {c: 1.0 / len(cols) for c in cols}
    w = C / C.sum()
    return dict(zip(cols, w))

def critic_bootstrap(norm: pd.DataFrame, sparse_ids: set = None,
                     B: int = 1000, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    cols = [c for c in norm.columns if not (sparse_ids and c in sparse_ids)]
    acc = {c: [] for c in cols}; n = len(norm)
    for _ in range(B):
        idx = rng.integers(0, n, n)
        w = critic_weights(norm.iloc[idx].reset_index(drop=True), sparse_ids)
        for c in cols:
            acc[c].append(w.get(c, 0.0))
    return {c: {"mean": float(np.mean(v)), "std": float(np.std(v)),
                "ci_low": float(np.percentile(v, 2.5)),
                "ci_high": float(np.percentile(v, 97.5))} for c, v in acc.items()}


# =========================================================
# [C] w_human / [E] 합성
# =========================================================
def human_weights(indicators: list) -> dict:
    """seed_weight 를 합=1 로 정규화.

    seed_weight 는 **크기만** 담는다(항상 >=0). 방향은 `direction` 필드가 갖는다
    — define_indicators 가 negative_factor 에 abs() 를 씌우는 이유다.
    부호를 여기 넣으면 normalize_matrix 의 cost 반전과 이중으로 걸려
    감점 지표가 조용히 가점으로 작동한다.
    """
    s = {i["id"]: float(i["seed_weight"]) for i in indicators}

    # 지표 0개 — 아래 합계 0 가드에도 걸리지만 원인이 전혀 다르다.
    #   합계 0 = 사람이 슬라이더를 전부 0으로 내림
    #   지표 0개 = 감리가 가점/감점을 하나도 판정하지 않음 (STEP1 문제)
    #   같은 메시지를 내면 STEP3 를 붙잡고 있게 된다.
    if not s:
        raise ValueError(
            "지표가 0개입니다 — 가중치를 계산할 대상이 없습니다.\n"
            "  감리 결과에 positive_factor / negative_factor 판정이 없습니다.\n"
            "  reviewed.json 의 roles 를 확인하세요 "
            "(전부 hard_exclusion / reference_only 로 판정됐을 수 있습니다).")

    # 음수 방지 — [W] HITL 이 슬라이더 -1~+1 을 분해하지 않고 그대로 넣으면 여기 걸린다.
    neg = {k: v for k, v in s.items() if v < 0}
    if neg:
        raise ValueError(
            f"seed_weight 에 음수가 있습니다: {neg}\n"
            f"  가중치는 크기(>=0)만 담습니다. 방향은 indicator['direction'] "
            f"('benefit'|'cost') 로 표현하세요.")

    tot = sum(s.values())
    if tot <= 0:
        raise ValueError(
            f"가중치 합이 {tot} 입니다 — 최소 하나는 0보다 커야 합니다.\n"
            f"  입력값: {s}\n"
            f"  전부 0이면 모든 후보 점수가 0이 되어 순위가 무의미해집니다.")

    return {k: v / tot for k, v in s.items()}


def synthesize(w_human: dict, w_critic: dict, alpha: float = 0.3,
               sparse_ids: set = None) -> dict:
    """w = (1-a)*human + a*critic. 희소지표는 human 만. 최종 sum=1 재정규화."""
    out = {}
    for i in w_human:
        h = w_human.get(i, 0.0)
        if sparse_ids and i in sparse_ids:
            out[i] = h
        else:
            out[i] = (1 - alpha) * h + alpha * w_critic.get(i, 0.0)

    tot = sum(out.values())
    if tot <= 0:
        raise ValueError(
            f"합성 가중치 합이 {tot} 입니다 — 정규화 불가.\n"
            f"  w_human={w_human}\n  w_critic={w_critic}\n  alpha={alpha}")

    return {k: v / tot for k, v in out.items()}


# =========================================================
# [W] 가중치 HITL — 슬라이더 -1~+1 (부호=방향, abs=크기)
# =========================================================
#   설계: STEP3_가중치_설계 11·12절
#
#   🔴 핵심 규약 — 슬라이더 값을 seed_weight 에 그대로 넣으면 안 된다.
#      seed_weight 는 크기만 담고(항상 >=0), 방향은 direction 필드가 갖는다.
#      부호를 seed_weight 에 넣으면 normalize_matrix 의 cost 반전과 이중으로 걸려
#      감점 지표가 조용히 가점으로 작동한다. 반드시 경계에서 분해한다.
#
#      슬라이더 -0.6  ─┬─ abs → seed_weight = 0.6
#                      └─ 부호 → direction  = "cost"
#
#   ⚠ 희소 판정은 여기서 표시할 수 없다. detect_sparse 는 [B] 지표 행렬이
#     있어야 계산되는데 [W] 는 그 앞이다. 대신 레코드 수를 근거로 보여준다.
def slider_from_indicators(indicators: list) -> dict:
    """지표 현재 상태 -> 슬라이더 초기값 {id: -1~+1}. cost 는 음수로 표시."""
    return {i["id"]: round((-1.0 if i.get("direction") == "cost" else 1.0)
                           * float(i["seed_weight"]), 3)
            for i in indicators}


def slider_pct(slider: dict) -> dict:
    """슬라이더 -> 정규화 비중 %. **abs 기준**이라 감점 지표도 양수 %가 된다.

    그냥 sum() 으로 나누면 감점이 분모를 깎아 합계가 100%가 안 된다.
      sum : 0.70 + 0.75 + (-0.55) = 0.90  ->  78% + 83% - 61%
      abs : 0.70 + 0.75 +   0.55  = 2.00  ->  35% + 37.5% + 27.5% = 100%
    """
    tot = sum(abs(float(v)) for v in slider.values())
    if tot <= 0:
        return {k: 0.0 for k in slider}
    return {k: abs(float(v)) / tot * 100.0 for k, v in slider.items()}


def data_note(ind: dict) -> str:
    """[W] 화면용 데이터 근거 한 줄. 희소 판정이 아니라 레코드 수다."""
    if ind.get("kind") == "admin":
        agg = ind.get("_admin_agg")
        return f"행정동 {len(agg)}종" if agg is not None else "행정동 집계"
    pts = ind.get("_points")
    n = len(pts) if pts is not None else 0
    return f"{n:,}건" + (" × 값" if ind.get("val_dataset") else "")


def apply_weight_hitl(indicators: list, slider: dict, sources="hitl") -> None:
    """[W] 확정값을 지표에 반영. 슬라이더를 (크기, 방향) 으로 분해한다.

    sources : str(전체 동일) 또는 {id: "llm"|"hitl"|"cli"}
    """
    ids = {i["id"] for i in indicators}
    unknown = set(slider) - ids
    if unknown:
        raise ValueError(f"없는 지표 ID: {sorted(unknown)}\n  사용 가능: {sorted(ids)}")

    tot = sum(abs(float(v)) for v in slider.values())
    if tot <= 0:
        raise ValueError(
            f"가중치 절대값 합이 {tot} 입니다 — 최소 하나는 0이 아니어야 합니다.\n"
            f"  입력값: {slider}\n"
            f"  전부 0이면 모든 후보 점수가 0이 되어 순위가 무의미해집니다.")

    at = datetime.now().isoformat(timespec="seconds")
    src_of = (lambda k: sources) if isinstance(sources, str) else (
        lambda k: sources.get(k, "llm"))

    for i in indicators:
        v = slider.get(i["id"])
        if v is None:
            continue
        v = float(v)
        if not (-1.0 <= v <= 1.0):
            raise ValueError(f"[{i['id']}] 슬라이더 범위는 -1 ~ +1 입니다: {v}")

        src = src_of(i["id"])
        # 0 은 '그 지표 제외' — 방향이 무의미하므로 원래 값을 유지한다.
        new_dir = i["direction"] if v == 0 else ("cost" if v < 0 else "benefit")
        if new_dir != i["direction"]:
            i["direction_source"] = src
        i["direction"] = new_dir
        i["seed_weight"] = round(abs(v), 3)
        i["w_human_source"] = src
        i["adjusted_at"] = at if src != "llm" else i.get("adjusted_at")


# =========================================================
# [F] weight_set 조립 + 저장 (DB 이관 전 JSON)
# =========================================================
def fingerprint(path: str | None) -> dict | None:
    """입력 파일 지문. **어느 데이터로 계산된 가중치인지** 특정한다.

    왜 필요한가 — STEP4 의 check_consistency 는 지표 ID·kind·구성 데이터셋만 본다.
    정제를 다시 돌려 clean 산출물 내용이 바뀌어도 **구조는 그대로**라 통과한다.
    그러면 옛 데이터로 만든 가중치로 새 데이터를 점수화하게 되는데, 조용히 지나간다.

    대상은 제어 파일 3개(reviewed · clean_report · 후보)로 한정한다.
    정제 산출물 본체(버스 127만행 등)까지 해시하면 실행마다 수 초가 붙는데,
    clean_report 를 해시하면 정제 재실행 자체는 어차피 잡힌다.
    """
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    st = os.stat(path)
    return {"file": os.path.basename(path),
            "sha256": h.hexdigest(),
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "size": int(st.st_size)}


def build_weight_set(domain: str, facility: str, region: str,
                     indicators: list, radius_conf: dict, alpha: float,
                     w_human: dict, w_critic: dict, w_final: dict,
                     boot: dict, sparse_ids: set, n_candidates: int,
                     engine_version: str = "wm-1.0",
                     candidate_unit: str | None = None,
                     candidate_source: dict | None = None,
                     inputs: dict | None = None,
                     hitl: dict | None = None) -> dict:
    """DB 한 행이 될 dict. '왜 이 값인가' 근거를 전부 동봉(B2G 설명책임).

    candidate_unit / candidate_source
      후보 1건이 무엇인지는 **코드가 알 수 없는 도메인 지식**이다.
      과거엔 "국유부동산 필지" 가 박혀 있었는데, 후보가 지적도 42,216필지로
      바뀐 뒤에도 그대로 찍혀 산출물이 존재하지 않는 숫자를 주장했다.
      호출부가 주입하고, 없으면 후보 파일에서 사실만 기술한다.

    generated_at / inputs
      **언제, 무엇으로** 만든 파일인지. 없으면 여러 실행분을 구분할 수 없다.
      설계노트 6절의 '인수인계 표 ↔ JSON 불일치' 가 이것 때문에 원인 규명이 늦었다.

    hitl / radius_source / w_human_source
      **누가 정했나.** 대외 설명이 "사람 70% / 데이터 30%" 인데 실제로는 LLM 값이
      들어갈 수 있으므로, 값마다 출처를 남겨야 그 주장이 방어된다.
      radius_source 가 없으면 --radius 로 덮어쓴 뒤에도 rationale 은 LLM 제안값
      기준 문장이 남아 산출물이 앞뒤 안 맞는 근거를 주장하게 된다.
    """
    return {
        "domain": domain, "facility": facility, "region": region,
        "engine_version": engine_version,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": inputs,
        "hitl": hitl,
        "alpha": alpha,
        "n_candidates": n_candidates,
        "candidate_unit": candidate_unit or "미기록",
        "candidate_source": candidate_source,
        "indicators": [{
            "id": i["id"], "kind": i["kind"], "direction": i["direction"],
            "components": {"geo": i["geo_dataset"], "val": i["val_dataset"]},
            "radius_m": radius_conf.get(i["id"], {}).get("radius_m"),
            "radius_source": radius_conf.get(i["id"], {}).get("source", "llm"),
            "radius_rationale": radius_conf.get(i["id"], {}).get("rationale", ""),
            "seed_rationale": i.get("rationale", ""),
            "sparse_excluded": i["id"] in sparse_ids,
            "w_human": round(w_human.get(i["id"], 0), 4),
            # 출처 — "사람 70%" 를 방어하려면 누가 정했는지가 근거가 된다(B2G 설명책임).
            "w_human_source": i.get("w_human_source", "llm"),
            "direction_source": i.get("direction_source", "llm"),
            "direction_llm": i.get("direction_llm", i["direction"]),
            "direction_conflict": i.get("direction_conflict"),
            "adjusted_at": i.get("adjusted_at"),
            "w_critic": None if i["id"] in sparse_ids else round(w_critic.get(i["id"], 0), 4),
            "w_critic_ci": boot.get(i["id"]),
            "w_final": round(w_final.get(i["id"], 0), 4),
        } for i in indicators],
        "notes": {
            "critic_method": "Spearman-CRITIC",
            "sparse_threshold": SPARSE_THRESHOLD,
            "sparse_excluded_ids": sorted(sparse_ids),
            "weight_meaning": "지표 간 상대 중요도(평가단위 독립). 위치선정이 이 값으로 후보 점수화.",
        },
    }

def save_weight_set(ws: dict, domain: str) -> str:
    os.makedirs(WEIGHT_OUTPUT_DIR, exist_ok=True)
    path = os.path.join(WEIGHT_OUTPUT_DIR, f"{domain}_weight_set.json")
    json.dump(ws, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return path


def build_weight_proposal(domain: str, facility: str, region: str,
                          indicators: list, radius_conf: dict, slider: dict,
                          run_id: str | None = None) -> dict:
    """[W] 게이트 화면에 보여줄 **제안값**. 확정값이 아니다.

    왜 별도 산출물인가 —
      [R] 반경 제안과 [W] 슬라이더 초기값은 `run_weight_model` 프로세스 **안에서만**
      만들어진다(LLM 제안 + define_indicators). 파일로 꺼내지 않으면 사람에게
      "AI 가 뭘 제안했는지"를 보여줄 방법이 없고, 그러면 원칙 3(LLM 제안 → 사람 확정)이
      화면에서 성립하지 않는다.

    weight_set 과 이름이 비슷하지만 **성격이 반대**다:
      · weight_set      = 확정 결과 (w_human·w_critic·w_final)
      · weight_proposal = 확정 **전** 제안 (radius_proposed·slider_proposed)
    그래서 w_* 를 담지 않는다. 담으면 확정 전 값이 확정값인 척한다(원칙 4).

    `conflicts` 는 define_indicators 가 reviewed.json 만으로 판정한다(:300).
    반경·CRITIC 과 접점이 없으므로 이 시점에 전부 알 수 있다.
    """
    return {
        "run_id": run_id,
        "domain": domain, "facility": facility, "region": region,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "indicators": [{
            "id": i["id"], "kind": i["kind"], "direction": i["direction"],
            "seed_weight": i["seed_weight"],
            "components": {"geo": i["geo_dataset"], "val": i["val_dataset"]},
            "rationale": i.get("rationale", ""),
            "data_note": data_note(i),
        } for i in indicators],
        "radius_proposed": {
            i["id"]: {
                "radius_m": radius_conf.get(i["id"], {}).get("radius_m"),
                "rationale": radius_conf.get(i["id"], {}).get("rationale", ""),
                "source": radius_conf.get(i["id"], {}).get("source", "llm"),
            } for i in indicators},
        "slider_proposed": {i["id"]: slider[i["id"]] for i in indicators},
        "conflicts": [{
            "indicator_id": i["id"],
            **i["direction_conflict"],
        } for i in indicators if i.get("direction_conflict")],
    }


def save_weight_proposal(prop: dict, domain: str, run_id: str | None = None) -> str:
    """weight_set 과 **같은 디렉터리**에 저장한다(save_weight_set 규칙 재사용).

    별도 경로를 파면 mock/실제 오염 방지 규칙을 한 곳 더 관리해야 한다.
    run_id 가 있으면 파일명에 붙인다 — API 는 run 마다 이 디렉터리를 가르지만,
    CLI 로 직접 부르면 한 폴더에 여러 실행분이 쌓이기 때문이다.
    """
    os.makedirs(WEIGHT_OUTPUT_DIR, exist_ok=True)
    name = f"{domain}_weight_proposal{'_' + run_id if run_id else ''}.json"
    path = os.path.join(WEIGHT_OUTPUT_DIR, name)
    json.dump(prop, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return path


# =========================================================
# [진단] 후보 표본 대표성 — 편향이 가중치를 왜곡하는지 검사
# =========================================================
def diagnose_sample_bias(mat: pd.DataFrame, indicators: list, sparse_ids: set,
                         strata: pd.Series = None, seed: int = 0,
                         verbose: bool = True) -> dict:
    """후보 집합이 한쪽에 쏠려 있을 때 CRITIC 가중치가 흔들리는지 검사한다.

    가중치는 '후보 집합'을 표본으로 계산되므로, 표본이 편향되면 가중치도 편향될
    수 있다. 표본을 여러 방식으로 바꿔가며 다시 계산해 변동 폭을 본다.
      · 전체        : 기준값
      · 층화 균등   : strata(동 등) 별로 같은 수만 뽑음 → 쏠림 제거
      · 최다 2계층 제외 : 가장 많은 두 계층을 통째로 제거
      · 무작위 절반 : 표본 크기 절반

    strata: 후보와 같은 길이의 계층 라벨(예: 법정동명). None이면 층화 검사 생략.
    반환: {지표: {"base","min","max","spread"}, "_max_spread":float, "_rank_stable":bool}
          — **그대로 json 직렬화 가능해야 한다.** 산출물(`weight_set.diagnostics`)로 나간다.

    판정 기준(경험적): spread < 0.05 이고 순위가 유지되면 '표본 편향의 영향 미미'.
    실측(용산 국유지 2,486, 후암동 21%·상위5동 49% 쏠림): 최대 spread 0.018,
    순위 완전 유지 → 편향은 있으나 가중치는 사실상 불변.
    """
    rng = np.random.default_rng(seed)
    variants = {}

    variants["전체"] = mat
    if strata is not None:
        s = pd.Series(list(strata)).reset_index(drop=True)
        n_each = int(np.median(s.value_counts()))
        idx = []
        for _, grp in s.groupby(s):
            take = grp.index[:n_each] if len(grp) > n_each else grp.index
            idx.extend(take)
        variants["층화균등"] = mat.loc[sorted(idx)].reset_index(drop=True)
        top2 = s.value_counts().head(2).index
        keep = s[~s.isin(top2)].index
        if len(keep) > 30:
            variants["최다2계층제외"] = mat.loc[sorted(keep)].reset_index(drop=True)
    half = rng.choice(len(mat), max(len(mat) // 2, 30), replace=False)
    variants["무작위절반"] = mat.loc[sorted(half)].reset_index(drop=True)

    ws = {k: critic_weights(normalize_matrix(v, indicators), sparse_ids=sparse_ids)
          for k, v in variants.items()}

    cols = [c for c in mat.columns if c not in sparse_ids]
    out, max_spread = {}, 0.0
    for c in cols:
        vals = [ws[k].get(c, 0.0) for k in ws]
        sp = max(vals) - min(vals)
        max_spread = max(max_spread, sp)
        # float() — numpy 스칼라는 json.dump 가 못 삼킨다. 이 값은 이제 산출물로 나간다(S4).
        out[c] = {"base": float(ws["전체"].get(c, 0.0)), "min": float(min(vals)),
                  "max": float(max(vals)), "spread": float(sp)}

    ranks = [tuple(sorted(cols, key=lambda c: -ws[k].get(c, 0.0))) for k in ws]
    rank_stable = len(set(ranks)) == 1

    # 순위가 뒤집힌 쌍을 찾아 '왜' 뒤집혔는지까지 남긴다.
    #   두 지표의 기준값 격차가 각자의 변동폭보다 작으면, 순서가 바뀌는 것은
    #   편향의 증거가 아니라 **근접 동률의 당연한 결과**다.
    #   (새 임계값을 정하지 않는다 — 이미 측정된 변동폭을 잣대로 쓴다)
    base_order = sorted(cols, key=lambda c: -ws["전체"].get(c, 0.0))
    pos = {c: i for i, c in enumerate(base_order)}
    flips, seen = [], set()
    for k in ws:
        p = {c: i for i, c in enumerate(
            sorted(cols, key=lambda c: -ws[k].get(c, 0.0)))}
        for a in cols:
            for b in cols:
                if pos[a] < pos[b] and p[a] > p[b] and (a, b) not in seen:
                    seen.add((a, b))
                    gap = abs(out[a]["base"] - out[b]["base"])
                    tol = max(out[a]["spread"], out[b]["spread"])
                    flips.append({"pair": [a, b], "gap": float(gap),
                                  "tol": float(tol), "explained": bool(gap < tol),
                                  "variant": k})
    unexplained = [f for f in flips if not f["explained"]]

    out["_max_spread"] = float(max_spread)
    out["_rank_stable"] = bool(rank_stable)
    out["_rank_flips"] = flips
    out["_variants"] = {k: len(v) for k, v in variants.items()}

    if verbose:
        print(f"\n[표본 대표성 진단]  변형: " +
              ", ".join(f"{k}({n})" for k, n in out["_variants"].items()))
        for c in cols:
            d = out[c]
            print(f"  {c:<10} {d['base']:.3f}  범위 [{d['min']:.3f}, {d['max']:.3f}]"
                  f"  변동폭 {d['spread']:.3f}")
        verdict = ("영향 미미 — 표본 편향이 가중치를 왜곡하지 않음"
                   if max_spread < 0.05 and not unexplained else
                   "⚠ 표본 편향 영향 있음 — 후보 집합 재검토 필요")
        for f in flips:
            a, b = f["pair"]
            tag = "근접 동률" if f["explained"] else "⚠ 유의미한 역전"
            print(f"  순위 교체 {a}↔{b}  격차 {f['gap']:.3f} vs 변동폭 "
                  f"{f['tol']:.3f}  [{tag}]  ({f['variant']})")
        note = ("" if rank_stable else
                f" · 순위 교체 {len(flips)}쌍(설명 안 되는 것 {len(unexplained)})")
        print(f"  최대 변동폭 {max_spread:.3f}{note} → {verdict}")
    return out


# =========================================================
# [진단] alpha 민감도 — 합성 비율이 결과를 얼마나 바꾸는가
# =========================================================
def diagnose_alpha(w_human: dict, w_critic: dict, sparse_ids: set,
                   alphas=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5),
                   verbose: bool = True) -> dict:
    """alpha 를 바꿔가며 최종 가중치·순위가 얼마나 달라지는지 표로 본다.
    'alpha=0.3 인 이유'를 관례가 아니라 근거로 설명하기 위한 진단.
    순위가 바뀌지 않는 구간이 넓으면 그 중앙값을 택하는 것이 방어 가능하다.

    반환 dict
      weights      {alpha: {지표: w_final}}      — json 직렬화 가능
      rank_groups  [{"alphas": [...], "order": [...]}]  순위가 같은 alpha 구간
      table        pandas DataFrame. **사람이 보는 용도 — json 에는 싣지 않는다.**

    순위 구간은 verbose 여부와 무관하게 계산한다. 예전에는 print 블록 안에서만
    만들어져, `--no-diag` 가 아니어도 **근거가 콘솔에만 남고 사라졌다**(S4).
    """
    rows = {}
    for a in alphas:
        rows[a] = synthesize(w_human, w_critic, alpha=a, sparse_ids=sparse_ids)
    df = pd.DataFrame(rows)

    ranks = {a: tuple(df[a].sort_values(ascending=False).index) for a in alphas}
    uniq = {}
    for a, r in ranks.items():
        uniq.setdefault(r, []).append(a)

    if verbose:
        print("\n[alpha 민감도]")
        print(df.round(3).to_string())
        print("  순위가 같은 alpha 구간:")
        for r, aa in uniq.items():
            print(f"    α={aa}  →  {' > '.join(r)}")

    return {
        "weights": {str(a): {k: float(v) for k, v in rows[a].items()} for a in alphas},
        "rank_groups": [{"alphas": [float(x) for x in aa], "order": list(r)}
                        for r, aa in uniq.items()],
        "table": df,
    }
