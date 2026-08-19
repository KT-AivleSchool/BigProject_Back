# -*- coding: utf-8 -*-
"""행정동 조달 — 표기 정규화 · 크로스워크 · 지역 파일 탐색"""
from __future__ import annotations

import glob
import os
import numpy as np
import pandas as pd

from .state import ADMIN_CROSSWALK_PATH, REGION_DATA_DIR


def _norm_dong(s) -> pd.Series:
    """행정동명 표기 정규화. 같은 동이 소스마다 다르게 적힌다.

      '금호2·3가동' / '금호2ㆍ3가동' / '금호2,3가동' / '금호2.3가동'
      '왕십리도선동 ' / '성수1가 1동' / '용답동(용답)'
      '왕십리제2동' / '성수1가제1동' / '행당제1동'   ← 서수 '제'

    가운뎃점·구분자·공백·괄호주석을 걷어내고 비교한다. 표기 규칙을 코드에 박는 게
    아니라 **양쪽에 같은 정규화를 걸어** 맞추는 것이므로 도메인 무관하다.

    🔴 서수 '제' 는 법정 표기(왕십리제2동)와 약식 표기(왕십리2동)가 갈린다. 성동구
       인구현황 xlsx 가 법정 표기라 크로스워크(약식)와 **59% 만 맞았고** STEP3 가
       `행정동 조인 키를 찾지 못했습니다` 로 멈췄다(r_20260812_013). 숫자 앞의 '제'
       만 지운다 — 양쪽에 같은 규칙을 걸므로 '홍제1동'→'홍1동' 처럼 과하게 깎여도
       **짝이 같이 깎여 매칭은 유지**된다. 전국 크로스워크로 실측: 바뀌는 행 9개,
       시군구 내 중복은 2건으로 **전후 동일**(새 오매칭 0). 그래서 '홍제'·'거제'
       같은 지명을 예외로 적어두지 않는다 — 예외 목록은 다음 지역에서 또 틀린다.
    """
    return (
        s.astype(str)
        .str.replace(r"\(.*?\)", "", regex=True)  # 괄호 주석
        .str.replace(r"[·ㆍ・∙,\.\-~/]", "", regex=True)  # 구분자
        .str.replace(r"\s+", "", regex=True)  # 공백
        .str.replace(r"제(?=\d)", "", regex=True)  # 서수 '제' (제2동 == 2동)
        .str.strip()
    )


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
    names = (
        set(_norm_dong(xw["행정동명"].dropna())) if "행정동명" in xw.columns else set()
    )

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
                f"  어느 것이 조인 키인지 확정할 수 없습니다 — 데이터를 확인하세요."
            )

    msg = [
        f"[{did}] 행정동 조인 키(코드 또는 이름)를 찾지 못했습니다.",
        f"  컬럼: {[c for c in g.columns if c != 'geometry']}",
        f"  후보 매칭률: "
        f"{ {c: f'{v[0]} {v[1]:.0%}' for c, v in hits.items()} if hits else '후보 없음' }",
    ]
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
            print(
                f"  ⓘ [{did}] 행정동명 소속 시군구 최빈값 {sgg} 사용 "
                f"({top.iloc[0]}/{top.sum()}건)"
            )
    cand = cand[cand["행정구역코드"].astype(str).str[:5] == sgg]
    cnm = _norm_dong(cand["행정동명"])

    dup = int(cnm.duplicated().sum())
    if dup:
        raise ValueError(
            f"[{did}] 시군구 {sgg} 안에 같은 행정동명이 {dup}건 중복입니다."
        )

    m = dict(zip(cnm, cand["행정구역코드"].astype(str)))
    out = nm.map(m)
    miss = int(out.isna().sum())
    if miss:
        bad = sorted(set(s.astype(str)[out.isna()]))[:8]
        print(f"  ⚠ [{did}] 시군구 {sgg} 에서 코드 변환 실패 {miss}건 — {bad}")
    return out


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
            f"  '<시도명> <시군구명>' 형태로 지정하세요(예: '서울특별시 중구')."
        )
    return codes[0]


def find_region_file(
    pattern: str,
    region: str = "",
    sgg_code: str | None = None,
    root: str | None = None,
    must: bool = True,
) -> str | None:
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

    def _reldirs(h: str) -> list[str]:
        """base 기준 상대경로의 **폴더 부분**. 루트 직속이면 빈 리스트다."""
        return os.path.relpath(h, base).replace("\\", "/").split("/")[:-1]

    if code:
        coded = [h for h in hits if code in os.path.basename(h)]
        if coded:
            hits = coded
        elif region:  # 파일명에 코드가 없으면 폴더명으로
            gu = region.split()[-1]
            named = [h for h in hits if gu in _reldirs(h)]
            if named:
                hits = named
            else:
                # 🔴 여기서 `hits` 를 그대로 두면 **남의 구 파일을 조용히 집는다.**
                #    실측(2026-08-12): `국유부동산*.csv` + '서울특별시 성동구' 가
                #    `region_data/용산구/국유부동산_위경도_v2.csv` 를 돌려줬다. 파일명에
                #    코드가 없고 폴더명도 안 맞는데 **후보가 하나뿐이라** len(hits)>1
                #    중단 검사에도 안 걸렸다 — 마포구 사건과 같은 구조다.
                #    (반대 방향도 같다: `국유부동산*.xls*` + 용산 → 성동 파일)
                #    다만 `BND_*.shp`·`행정동_크로스워크.csv` 처럼 **루트 직속 공용
                #    파일**은 특정 구의 것이 아니므로 남긴다. 지자체 폴더 안에만
                #    있는데 우리 구가 아니면 **없는 것으로 친다**.
                hits = [h for h in hits if not _reldirs(h)]

    if not hits:
        if not must:
            return None
        raise FileNotFoundError(
            f"지역 파일 없음: {pattern}\n"
            f"  지역: {region or '(미지정)'}"
            + (f" (시군구코드 {code})" if code else "")
            + "\n"
            f"  탐색: {os.path.join(base, '**', pattern)}\n"
            f"  region_data 하위에 해당 지자체 파일을 두세요."
        )

    if len(hits) > 1:
        # 같은 지역의 여러 연월이면 최신을 쓰되 알린다. 다른 지역이 섞였으면 중단.
        bns = {os.path.basename(h) for h in hits}
        if code and all(code in b for b in bns):
            pick = hits[-1]
            print(
                f"  ⚠ {pattern} 후보 {len(hits)}개 — 최신본 사용: {os.path.basename(pick)}"
            )
            return pick
        raise ValueError(
            "지역 파일 후보가 여러 개입니다 — 어느 지역인지 확정할 수 없습니다.\n  "
            + "\n  ".join(hits)
            + f"\n\n  지역 '{region or '(미지정)'}' 로는 좁혀지지 않습니다. "
            f"경로를 직접 지정하세요."
        )
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
            f"  생성: python make_admin_crosswalk.py <국가데이터처_법정동_연계정보.csv>"
        )
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
    if cd in amap:  # ① 같은 체계
        return amap[cd]
    if xwalk is not None:  # ② 코드 체계 변환
        hit = xwalk.loc[xwalk["행정구역코드"] == cd, "행정동코드8"]
        if len(hit):
            return amap.get(str(hit.iloc[0]), np.nan)
    return np.nan
