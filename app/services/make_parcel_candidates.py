# -*- coding: utf-8 -*-
"""
지적도 -> 후보 필지 대표점 gpkg
===============================
  python make_parcel_candidates.py 흡연
  python make_parcel_candidates.py 흡연 --force-jimok      (지목 재판정)
  python make_parcel_candidates.py 흡연 --jimok 대,도,잡   (LLM 없이 직접 지정)

필지 1개당 대표점 1개. 가중치는 '지표 간 상대 중요도'이므로 필지 단위로 재는 게 맞다.
(후보점 13만 개를 쓰면 큰 필지가 점 수만큼 과대 대표된다 — 면적 가중이 걸린다.)

지목 필터
  기본은 gam4_jimok 의 LLM 판정 + 캐시. 코드에 지목을 박지 않는다.
  --jimok 은 키가 없거나 판정을 우회할 때만 쓰는 수동 경로다.

출력
  geometry(Point, EPSG:5186) · PNU · JIBUN · 지목 · 면적 · 내접폭 · 법정동코드 · 경도 · 위도
"""

from __future__ import annotations

import sys
import io

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import glob
import time
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import geopandas as gpd
import pandas as pd

try:
    from app.config import (
        REGION_DATA_DIR,
        STEP1_OUTPUT_DIR,
        STEP3_OUTPUT_DIR,
        CANDIDATE_GPKG_NAME,
        candidate_gpkg_path,
        NATIONAL_PROPERTY_CSV,
        domain_prefix,
    )
except Exception:
    REGION_DATA_DIR = STEP1_OUTPUT_DIR = STEP3_OUTPUT_DIR = "."
    CANDIDATE_GPKG_NAME = "후보_지적도필지.gpkg"

    def candidate_gpkg_path(d):
        return os.path.join(STEP3_OUTPUT_DIR, f"{d}_{CANDIDATE_GPKG_NAME}")

    NATIONAL_PROPERTY_CSV = "국유부동산_위경도_v2.csv"

    def domain_prefix(d):
        return d


try:
    from app.services import gam4_spatial_ops as S, gam4_jimok as J
except ImportError:
    import gam4_spatial_ops as S
    import gam4_jimok as J


# 지목 판정 기본 모델.
#   실측: mini 는 주유소용지를 candidate 로 판정했다(화기 취급 금지 구역).
#   체육·제방·종교용지도 "X 용도라서 불가" 식 동어반복으로 잘못 배제했다.
#   실행당 1회·20종뿐이라 4o 를 써도 비용이 거의 없어 기본값으로 둔다.
JIMOK_MODEL_DEFAULT = "gpt-4o"

# 수요 경계에서 뺄 지목 — 사람이 서 있을 수 없는 수면.
#   지목 부호는 법정 표준(공간정보관리법 시행령 제58조)이라 도메인 무관하다.
#   '설치 가능/불가' 판단이 아니라 '물리적으로 사람이 있을 수 없는 면'의 정의다.
WATER_JIMOK = ("천", "유", "구")


def _region_of(domain: str, override: str | None = None) -> str:
    """대상 지역명. reviewed.json 의 감리 확정값을 쓴다(지적도·국유지 경로 해석에 사용)."""
    if override:
        return override
    p = os.path.join(
        STEP1_OUTPUT_DIR, f"{domain_prefix(domain)}_audit_result_reviewed.json"
    )
    if os.path.isfile(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            r = (d.get("facility_inference") or {}).get("region")
            if r:
                return r
        except Exception as e:
            print(f"  ⚠ reviewed.json 읽기 실패({e})")
    return ""


def _find_cadastral(path: str | None, region: str = "") -> str:
    """연속지적도 SHP. **시군구코드로 고른다** — region_data 하위 지자체 폴더를 재귀 탐색.

    예전에는 glob 결과의 hits[-1] 을 그냥 썼다. region_data 에 두 구 지적도가 있으면
    **어느 구를 쓰는지 모르고 지나간다**(마포구 사건과 같은 구조).
    """
    if path and os.path.isfile(path):
        return path
    try:
        from app.services.gam2_weight_model import find_region_file

        found = find_region_file("LSMD_CONT_LDREG_*.shp", region, must=False)
        if found:
            return found
    except Exception:
        pass
    hits = sorted(
        glob.glob(
            os.path.join(REGION_DATA_DIR, "**", "LSMD_CONT_LDREG_*.shp"), recursive=True
        )
    )
    if len(hits) == 1:
        return hits[0]
    print(f"  ⚠ 연속지적도 SHP 부재(후보 {len(hits)}개) — 지적도 스텁(stub_cadastral.shp)으로 진행합니다.")
    return "stub_cadastral.shp"


def _facility_of(domain: str, override: str | None) -> str:
    """시설명 — 지목 판정의 캐시 키. reviewed.json 의 감리 확정값을 쓴다."""
    if override:
        return override
    p = os.path.join(
        STEP1_OUTPUT_DIR, f"{domain_prefix(domain)}_audit_result_reviewed.json"
    )
    if os.path.isfile(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            f = (d.get("facility_inference") or {}).get("facility")
            if f:
                return f
        except Exception as e:
            print(f"  ⚠ reviewed.json 읽기 실패({e})")
    print(
        f"  ⚠ 시설명을 못 찾아 도메인명 '{domain}' 을 사용합니다 "
        f"(--facility 로 지정 가능)"
    )
    return domain


def attach_ownership(
    parcels: gpd.GeoDataFrame, csv_path: str, verbose: bool = True
) -> gpd.GeoDataFrame:
    """국유부동산 좌표를 필지에 공간조인해 '국유 지분' 정보를 붙인다.

    ⚠ 국유부동산은 '필지'가 아니라 '지분'이다 (실측으로 확인).
      대장면적 0.3m² 짜리 땅은 존재하지 않는다 — 170m² 필지를 여럿이 공유하고
      그중 국가 몫이 0.3m² 인 것이다. 실제로 갈월동 65-26(5.5m²)과
      65-24(18.5m²)가 **같은 지적도 필지(53-1, 170.9m²)** 로 조인된다.
      따라서 "이 필지는 국유지" 가 아니라 "이 필지에 국유 지분이 있다" 가 맞다.

    지분율을 필드로 내보내고 임계값은 정하지 않는다. 100%에 가까우면 사실상
    국유지, 1%면 공유지분 — 그 판단은 담당자 몫이다(2축 분리 원칙).

    붙는 컬럼
      국유_건수     같은 필지에 조인된 국유재산 건수
      국유_지분면적 대장면적 합(m²)
      국유_지분율   지분면적 / 필지면적
      국유_지번일치 CSV 지번 == 지적도 지번 (조인 신뢰도)
    """
    if not os.path.isfile(csv_path):
        if verbose:
            print(f"  ⚠ 국유부동산 CSV 없음 — 지분 태그 생략: {csv_path}")
        return parcels

    for enc in ("utf-8", "utf-8-sig", "cp949"):
        try:
            d = pd.read_csv(csv_path, encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        print("  ⚠ 국유부동산 CSV 인코딩 실패 — 지분 태그 생략")
        return parcels

    lon = next((c for c in d.columns if "경도" in c), None)
    lat = next((c for c in d.columns if "위도" in c), None)
    ar = next((c for c in d.columns if "면적" in c), None)
    addr = next((c for c in d.columns if "소재지" in c), None)
    if not (lon and lat):
        print("  ⚠ 국유부동산 CSV 에 경위도 없음 — 지분 태그 생략")
        return parcels

    own = gpd.GeoDataFrame(
        d, geometry=gpd.points_from_xy(d[lon], d[lat]), crs=4326
    ).to_crs(parcels.crs)
    own = own[~own.geometry.is_empty & own.geometry.notna()]
    if addr:  # "…용산구 용문동 5-100" -> "5-100"
        own["_지번"] = own[addr].astype(str).str.extract(r"구\s+\S+\s+(\S+)")[0]

    p = parcels.reset_index(drop=True)
    j = gpd.sjoin(own, p[["geometry"]], how="inner", predicate="within")
    if j.empty:
        if verbose:
            print("  ⚠ 국유부동산이 필지 안에 하나도 안 떨어짐 — 좌표계 확인 필요")
        return parcels

    # 🔴 면적 컬럼이 **문자열일 수 있다.** 성동 원본 .xls 는 `'112.00'` 처럼
    #    작은따옴표로 감싼 텍스트 서식이었다 — 그러면 `sum()` 이 문자열 연결이
    #    되고 아래 나눗셈이 `TypeError` 로 터진다(r_20260812_012). 여기서 터진 건
    #    운이 좋았던 것이다: 나눌 상대가 없었으면 **이어붙인 문자열이 면적인 척**
    #    그대로 흘러갔다. 못 읽는 값은 0 이 아니라 NaN 으로 두고 개수를 알린다.
    if ar:
        _a = pd.to_numeric(j[ar], errors="coerce")
        _bad = int(_a.isna().sum() - j[ar].isna().sum())
        if _bad and verbose:
            print(f"  ⚠ 국유 대장면적을 숫자로 못 읽음 {_bad:,}건 — 지분면적에서 제외")
        j = j.assign(**{ar: _a})

    grp = j.groupby("index_right")
    p["국유_건수"] = grp.size().reindex(p.index).fillna(0).astype(int)
    p["국유_지분면적"] = grp[ar].sum().reindex(p.index).fillna(0.0) if ar else 0.0
    p["국유_지분율"] = (
        (p["국유_지분면적"] / p["면적"]).replace([float("inf")], 0).fillna(0)
    )

    if addr and "지번" not in p.columns:
        pj = (
            p["JIBUN"]
            .astype(str)
            .str.replace(r"\s*[가-힣]+$", "", regex=True)
            .str.strip()
        )
        first = grp["_지번"].first().reindex(p.index)
        p["국유_지번일치"] = first.notna() & (first.values == pj.values)
    else:
        p["국유_지번일치"] = False

    if verbose:
        has = p["국유_건수"] > 0
        print(
            f"[국유지분] {len(j):,}건 조인 → {has.sum():,}필지에 지분 존재 "
            f"({has.mean() * 100:.1f}%)"
        )
        if has.any():
            r = p.loc[has, "국유_지분율"]
            print(
                f"           지분율 중앙 {r.median() * 100:.1f}%  "
                f"90%이상 {(r >= 0.9).sum():,}필지  10%미만 {(r < 0.1).sum():,}필지"
            )
            print(
                f"           지번일치 {p.loc[has, '국유_지번일치'].mean() * 100:.1f}% "
                f"(조인 신뢰도)"
            )
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain", help="도메인 폴더명 (예: 흡연)")
    ap.add_argument(
        "--facility", default=None, help="시설명 override (지목 판정 캐시 키)"
    )
    ap.add_argument("--cadastral", default=None, help="연속지적도 SHP")
    ap.add_argument("--out", default=None, help="출력 gpkg")
    ap.add_argument(
        "--jimok", default=None, help="지목 직접 지정(쉼표구분). LLM 판정을 건너뛴다"
    )
    ap.add_argument("--force-jimok", action="store_true", help="캐시 무시하고 재판정")
    ap.add_argument(
        "--model",
        default=JIMOK_MODEL_DEFAULT,
        help=f"지목 판정 모델 (기본 {JIMOK_MODEL_DEFAULT}). "
        f"mini 는 주유소용지를 candidate 로 오판한 이력이 있다",
    )
    ap.add_argument(
        "--no-width", action="store_true", help="내접폭 계산 생략(6~7초 절약)"
    )
    ap.add_argument(
        "--national",
        default=None,
        help="국유부동산 CSV (기본: config.NATIONAL_PROPERTY_CSV)",
    )
    ap.add_argument("--no-national", action="store_true", help="국유지분 태그 생략")
    ap.add_argument(
        "--region",
        default=None,
        help="대상 지역명(예 '서울특별시 성동구'). 미지정 시 reviewed.json 에서 읽는다",
    )
    args = ap.parse_args()

    T0 = time.perf_counter()
    region = _region_of(args.domain, args.region)
    path = _find_cadastral(args.cadastral, region)
    print(f"[입력] {path}" + (f"   지역: {region}" if region else ""))
    _t = time.perf_counter()
    g = S.load_parcels(path)
    print(f"         로드 {time.perf_counter() - _t:.1f}s")
    stats = J.jimok_stats(g)

    # 지역 경계 — STEP4 수요 격자가 쓴다.
    #   수요는 '지을 수 있는 곳'이 아니라 '사람이 있는 곳'이다.
    #   · 학교·철도용지는 **포함**한다 — 설치는 못 해도 주변 수요는 실재한다.
    #   · 수면(하천·유지·구거)은 **제외**한다 — 사람이 서 있을 수 없다.
    #     용산구는 한강이 4.76km²(21.7%)라, 넣으면 커버율 분모가 부풀고
    #     admin 지표(생활인구)가 동 평균값을 수면에까지 부여한다.
    _t = time.perf_counter()
    full = g.geometry.union_all()
    land = g[~g["지목"].isin(WATER_JIMOK)]
    boundary = land.geometry.union_all() if len(land) < len(g) else full
    print(
        f"[경계]   {boundary.area / 1e6:.2f}km²  "
        f"(전체 {full.area / 1e6:.2f} − 수면 {(full.area - boundary.area) / 1e6:.2f})  "
        f"[{time.perf_counter() - _t:.1f}s]"
    )

    # ── 지목 판정 ──────────────────────────────────────────
    if args.jimok:
        want = [x.strip() for x in args.jimok.split(",") if x.strip()]
        bad = [x for x in want if x not in S.STD_JIMOK]
        if bad:
            raise ValueError(
                f"표준 지목 부호가 아님: {bad}\n"
                f"  사용 가능: {''.join(sorted(S.STD_JIMOK))}"
            )
        print(f"  ⚠ --jimok 수동 지정 — LLM 판정을 건너뜁니다: {want}")
        rec = {
            "facility": "(수동)",
            "roles": {k: {"role": "candidate", "이유": "수동 지정"} for k in want},
            "flags": ["수동 지정"],
        }
    else:
        facility = _facility_of(args.domain, args.facility)
        rec = J.judge(facility, stats, model=args.model, force=args.force_jimok)
        J.print_judgment(rec, stats)
        want = J.candidate_jimok(rec)
        if not want:
            raise SystemExit("[중단] candidate 지목 0종 — 판정 결과를 확인하세요.")

    before = len(g)
    g = g[g["지목"].isin(want)].copy()
    print(
        f"\n[지목필터] {len(g):,} / {before:,} ({len(g) / before * 100:.1f}%)  "
        f"제외 {before - len(g):,}필지"
    )
    if g.empty:
        raise SystemExit("[중단] 남은 필지 0")

    # ── 내접폭 ────────────────────────────────────────────
    #   필터가 아니라 컬럼이다. 임계값은 결과화면에서 조절한다(설계 확정 턴23).
    if not args.no_width:
        print("[내접폭] 계산 중...")
        _t = time.perf_counter()
        g["내접폭"] = S.inscribed_width(g)
        print(
            f"         중앙 {g['내접폭'].median():.2f}m  "
            f"2m이상 {(g['내접폭'] >= 2).sum():,}필지 "
            f"({(g['내접폭'] >= 2).mean() * 100:.0f}%)  [{time.perf_counter() - _t:.1f}s]"
        )

    # ── 국유지분 태그 ──────────────────────────────────────
    #   점수축이 아니라 '실행축'이다. 가중합에 섞지 않는다(설계 확정 Q-feas).
    if not args.no_national:
        nat = args.national
        if not nat:
            # 국유부동산도 지자체별 파일이다. 지역 폴더에서 찾고, 없으면 config 기본값.
            try:
                from app.services.gam2_weight_model import find_region_file

                nat = find_region_file(
                    "국유부동산*.csv", region, must=False
                ) or find_region_file("국유부동산*.xls*", region, must=False)
            except ImportError:
                nat = None
            nat = nat or NATIONAL_PROPERTY_CSV
        g = attach_ownership(g, nat)

    # ── 대표점 ────────────────────────────────────────────
    #   centroid 는 오목한 필지(ㄱ자·굽은 도로)에서 밖으로 나간다.
    rep = g.geometry.representative_point()
    inside = rep.within(g.geometry)
    print(
        f"[대표점] {len(rep):,}개  필지 내부 보장 "
        f"{'✅' if inside.all() else '❌ ' + str((~inside).sum())}"
    )

    cols = [
        c
        for c in (
            "PNU",
            "JIBUN",
            "지목",
            "면적",
            "내접폭",
            "국유_건수",
            "국유_지분면적",
            "국유_지분율",
            "국유_지번일치",
        )
        if c in g.columns
    ]
    out = gpd.GeoDataFrame(g[cols].copy(), geometry=rep.values, crs=g.crs)

    # 층화 진단용 법정동 코드 — PNU 앞 10자리(시도2+시군구3+읍면동3+리2)
    if "PNU" in out.columns:
        out["법정동코드"] = out["PNU"].astype(str).str[:10]
        vc = out["법정동코드"].value_counts()
        print(
            f"[법정동] {len(vc)}개  최다 {vc.iloc[0] / len(out) * 100:.1f}%  "
            f"상위5 {vc.head(5).sum() / len(out) * 100:.1f}%"
        )

    wgs = out.to_crs(4326)
    out["경도"] = wgs.geometry.x
    out["위도"] = wgs.geometry.y

    # region_data 는 **원본·참조 데이터** 폴더다. 생성물을 섞으면 DB 적재 대상을
    #   가릴 때 헷갈리고, 무엇이 재생성 가능한지 구분이 안 된다.
    # 파일명에 도메인 프리픽스를 붙인다 — 후보 집합은 **지목 판정 결과에 의존**하고
    #   지목 판정은 시설별로 다르다(흡연부스 vs 재활용정거장). 프리픽스가 없으면
    #   흡연으로 만든 후보를 재활용 도메인이 그대로 쓰게 된다(조용한 오염).
    dst = args.out or candidate_gpkg_path(args.domain)
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)

    # 레이어 2개를 쓴다 — 단계마다 필요한 geometry 가 다르다.
    #   candidates(Point)  : STEP3 가중치. 필지당 1점(면적 가중 방지). **먼저 써서 기본 레이어**
    #   parcels(Polygon)   : STEP4 위치선정. 필지 내부에 격자점을 찍으려면 폴리곤이 필요하다.
    # 속성 컬럼을 먼저 뽑고 geometry 를 마지막에 붙인다.
    #   out 에서 geometry 만 빼면 컬럼 순서에 구멍이 생겨 pyogrio 가
    #   FieldError: Could not find field index 를 낸다.
    attr = [c for c in out.columns if c != "geometry"]
    out = out[attr + ["geometry"]]

    poly = gpd.GeoDataFrame(
        pd.DataFrame(out[attr]).reset_index(drop=True),
        geometry=g.geometry.reset_index(drop=True).values,
        crs=g.crs,
    )

    # 임시 파일에 쓴 뒤 원자적으로 교체한다.
    #   · 기존 파일에 mode="a" 로 덧쓰면 **옛 레이어 스키마**에 맞추려 하므로
    #     컬럼이 바뀌었을 때 FieldError: Could not find field index 가 난다.
    #   · 그렇다고 먼저 지우면, 쓰기가 중간에 실패했을 때 원본까지 잃는다.
    #   os.replace 는 같은 볼륨에서 원자적이라 실패해도 원본이 남는다.
    tmp = dst + ".tmp"
    for f in (tmp, tmp + "-wal", tmp + "-shm"):  # SQLite 잔여물 정리
        if os.path.exists(f):
            os.remove(f)
    try:
        out.to_file(tmp, driver="GPKG", layer="candidates")
        poly.to_file(tmp, driver="GPKG", layer="parcels", mode="a")
        gpd.GeoDataFrame({"name": ["region"]}, geometry=[boundary], crs=g.crs).to_file(
            tmp, driver="GPKG", layer="boundary", mode="a"
        )
        os.replace(tmp, dst)  # 성공했을 때만 교체
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

    print(
        f"\n[저장] {dst}  (EPSG:{out.crs.to_epsg()})  "
        f"[전체 {time.perf_counter() - T0:.1f}s]"
    )
    print(f"       layer 'candidates'  Point   {len(out):,}  <- STEP3 가중치용")
    print(f"       layer 'parcels'     Polygon {len(poly):,}  <- STEP4 위치선정용")
    print(
        f"       layer 'boundary'    Polygon 1       <- 수요격자 경계 "
        f"({boundary.area / 1e6:.2f}km², 수면 제외)"
    )
    print(f"       컬럼: {list(out.columns)}")


if __name__ == "__main__":
    main()
