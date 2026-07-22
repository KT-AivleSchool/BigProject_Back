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
import os, json, re
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
except Exception:                                 # 단독 실행/테스트 폴백
    ADM_DONG_SHP = os.environ.get("ADM_DONG_SHP", "")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    SEARCH_LLM_MODEL = "gpt-4o-mini"
    STEP2_OUTPUT_DIR = os.environ.get("STEP2_OUTPUT_DIR", ".")
    WEIGHT_OUTPUT_DIR = os.environ.get("STEP3_OUTPUT_DIR", "./step3_output")
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
                pos[did] = {"seed_weight": float(role.get("weight", 0.5)),
                            "direction": "benefit", "rationale": role.get("rationale", "")}
            elif rt == "negative_factor":
                pos[did] = {"seed_weight": abs(float(role.get("weight", -0.5))),
                            "direction": "cost", "rationale": role.get("rationale", "")}

    consumed = {wr["from_dataset"] for wr in wr_by_id.values()}

    indicators = []
    for did, meta in pos.items():
        if did in consumed:
            continue
        wr = wr_by_id.get(did)
        if wr:
            geo_id = wr["from_dataset"]; val_id = did
            geo_seed = pos.get(geo_id, {}).get("seed_weight")
            seed = np.mean([s for s in [geo_seed, meta["seed_weight"]] if s is not None])
            indicators.append({
                "id": f"{geo_id}+{val_id}", "kind": None,
                "geo_dataset": geo_id, "val_dataset": val_id,
                "join": {"geo_key": wr["from_column"], "val_key": wr["key_col"],
                         "normalize": wr.get("normalize", "none")},
                "seed_weight": round(float(seed), 3),
                "direction": meta["direction"], "rationale": meta["rationale"]})
        else:
            indicators.append({
                "id": did, "kind": None, "geo_dataset": did, "val_dataset": None,
                "join": None, "seed_weight": meta["seed_weight"],
                "direction": meta["direction"], "rationale": meta["rationale"]})
    return indicators


# =========================================================
# [A2] 레이어 부착 — 각 지표에 실제 점/값 결합, kind 확정
# =========================================================
def attach_layers(indicators: list, loader, admin_value_col: str = "총생활인구수",
                  admin_code_hint: str = "행정동코드", verbose: bool = True) -> None:
    """loader(dataset_id) -> GeoDataFrame|DataFrame (EPSG:5186 재투영은 loader 책임).
    각 지표에 _points/_valcol(point) 또는 _admin_agg(admin) 를 심고 kind 확정.
    """
    for i in indicators:
        g = loader(i["geo_dataset"])
        is_geo = hasattr(g, "geometry") and "geometry" in getattr(g, "columns", [])

        if not is_geo:                                  # 좌표 없는 통계표 -> admin
            i["kind"] = "admin"
            code_col = next((c for c in g.columns if admin_code_hint in str(c)), None)
            vcol = admin_value_col if admin_value_col in g.columns else _pick_value_cols(g)[0]
            g = g.copy(); g[vcol] = pd.to_numeric(g[vcol], errors="coerce")
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
                 default_radius: float = 150.0, verbose: bool = True) -> pd.DataFrame:
    """후보 × 지표 행렬(원자료, 미정규화). 모두 EPSG:5186 가정."""
    cand = candidates.reset_index(drop=True).copy(); cand["_cid"] = range(len(cand))
    mat = pd.DataFrame({"_cid": cand["_cid"]})

    cand_admcd = None
    if admin_gdf is not None and any(i["kind"] == "admin" for i in indicators):
        jn = gpd.sjoin(cand[["_cid", "geometry"]], admin_gdf[[_ADM_CODE_COL, "geometry"]],
                       how="left", predicate="within")
        cand_admcd = jn.groupby("_cid")[_ADM_CODE_COL].first()

    for i in indicators:
        iid = i["id"]
        if i["kind"] == "admin":
            if cand_admcd is None:
                raise ValueError(f"[{iid}] admin 지표엔 admin_gdf(행정동 경계)가 필요합니다.")
            agg = i["_admin_agg"]; ccol = i["_admin_code_col"]; vcol = i["_admin_valcol"]
            amap = dict(zip(agg[ccol].astype(str), agg[vcol]))
            vals = cand["_cid"].map(cand_admcd).map(lambda cd: _admin_code_match(cd, amap))
            mat[iid] = vals.fillna(0.0).values
            if verbose:
                hit = int((mat[iid] > 0).sum())
                mv = mat[iid][mat[iid] > 0]
                print(f"  [{iid}] admin  hit={hit}/{len(cand)}  mean={mv.mean():,.0f}" if len(mv) else f"  [{iid}] admin hit=0")
            continue

        R = float(radius_m.get(iid) or default_radius)
        pts = i["_points"]
        buf = cand[["_cid"]].copy(); buf["geometry"] = cand.geometry.buffer(R)
        buf = gpd.GeoDataFrame(buf, geometry="geometry", crs=cand.crs)
        j = gpd.sjoin(pts, buf, how="inner", predicate="within")
        if i["kind"] == "point_count":
            s = j.groupby("_cid").size()
        else:
            s = j.groupby("_cid")[i["_valcol"]].sum()
        mat[iid] = mat["_cid"].map(s).fillna(0.0).values
        if verbose:
            hit = int((mat[iid] > 0).sum())
            print(f"  [{iid}] {i['kind']:<11} R={R:>4.0f}m  hit={hit}/{len(cand)} "
                  f"({hit/len(cand)*100:.0f}%)  max={mat[iid].max():,.0f}")

    return mat.drop(columns="_cid")

def _admin_code_match(cd, amap: dict):
    """행정동코드 체계 차이 흡수.
    경계SHP ADM_CD(통계청, 11030xxx)와 생활인구(행자부, 11170xxx)는 앞 5자리
    (자치구 코드)가 서로 다르다 — 통계청 11030 vs 행자부 11170 (둘 다 용산구).
    그러나 뒤 3자리(동 식별자)는 공유한다: ...510=후암동, ...520=용산2가동.
    같은 자치구 내 매칭이므로 뒤 3자리로 연결한다.
      실측(용산): ADM_CD 11030510 ↔ 생활인구 11170510  (앞5 다름, 뒤3 '510' 동일)
    """
    if cd is None or (isinstance(cd, float) and np.isnan(cd)):
        return np.nan
    cd = str(cd)
    if cd in amap:                       # 완전 일치 우선
        return amap[cd]
    # 뒤 3자리(동 코드)로 매칭
    tail = cd[-3:]
    for k, v in amap.items():
        if str(k)[-3:] == tail:
            return v
    return np.nan


# =========================================================
# [B2] 정규화
# =========================================================
def normalize_matrix(mat: pd.DataFrame, indicators: list) -> pd.DataFrame:
    dir_by = {i["id"]: i["direction"] for i in indicators}
    out = pd.DataFrame(index=mat.index)
    for c in mat.columns:
        x = mat[c].astype(float); lo, hi = x.min(), x.max()
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
def detect_sparse(mat: pd.DataFrame, threshold: float = SPARSE_THRESHOLD) -> set:
    return {c for c in mat.columns if (mat[c] > 0).mean() < threshold}

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
    s = {i["id"]: i["seed_weight"] for i in indicators}
    tot = sum(s.values())
    return {k: v / tot for k, v in s.items()} if tot else s

def synthesize(w_human: dict, w_critic: dict, alpha: float = 0.3, sparse_ids: set = None) -> dict:
    """w = (1-a)*human + a*critic. 희소지표는 human 만. 최종 sum=1 재정규화."""
    out = {}
    for i in w_human:
        h = w_human.get(i, 0.0)
        if sparse_ids and i in sparse_ids:
            out[i] = h
        else:
            out[i] = (1 - alpha) * h + alpha * w_critic.get(i, 0.0)
    tot = sum(out.values())
    return {k: v / tot for k, v in out.items()} if tot else out


# =========================================================
# [F] weight_set 조립 + 저장 (DB 이관 전 JSON)
# =========================================================
def build_weight_set(domain: str, facility: str, region: str,
                     indicators: list, radius_conf: dict, alpha: float,
                     w_human: dict, w_critic: dict, w_final: dict,
                     boot: dict, sparse_ids: set, n_candidates: int,
                     engine_version: str = "wm-1.0") -> dict:
    """DB 한 행이 될 dict. '왜 이 값인가' 근거를 전부 동봉(B2G 설명책임)."""
    return {
        "domain": domain, "facility": facility, "region": region,
        "engine_version": engine_version, "alpha": alpha,
        "n_candidates": n_candidates, "candidate_unit": "국유부동산 필지",
        "indicators": [{
            "id": i["id"], "kind": i["kind"], "direction": i["direction"],
            "components": {"geo": i["geo_dataset"], "val": i["val_dataset"]},
            "radius_m": radius_conf.get(i["id"], {}).get("radius_m"),
            "radius_rationale": radius_conf.get(i["id"], {}).get("rationale", ""),
            "seed_rationale": i.get("rationale", ""),
            "sparse_excluded": i["id"] in sparse_ids,
            "w_human": round(w_human.get(i["id"], 0), 4),
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
