# -*- coding: utf-8 -*-
"""
행정동 코드 크로스워크 생성 (전국)
====================================
  python make_admin_crosswalk.py 국가데이터처_법정동_연계정보_20250602.csv

왜 필요한가
  경계 SHP(통계청 행정구역분류코드)와 생활인구(행자부 행정동코드)는
  **같은 동에 다른 번호**를 붙인다. 뒤 3자리가 우연히 같은 동이 많아서
  휴리스틱으로 12/16 이 붙었고, 나머지 4 개(청파·원효로1·한강로·한남)는
  조용히 0 이 됐다 — 후보 39.3% 가 최대 가중치 지표를 못 받았다.

      통계청 11030730  =  한강로동  =  행자부 11170625   (뒤3자리 730 ≠ 625)

  이 스크립트는 국가데이터처 '법정동 연계정보' 에서 **양쪽 코드가 함께 실린
  행**만 추려 작은 참조표를 만든다. 지목 표준 28종과 같은 성격의 참조
  데이터이며, 특정 지역을 코드에 박지 않는다.

출력
  행정동_크로스워크.csv        행정구역코드(8) -> 행정동코드(10) + 이름
  행정동_크로스워크_충돌.csv    1:N 인 코드 (있을 때만)

원본 주의사항
  · 엑셀 한계(1,048,575행)에서 잘려 있다. 날짜 내림차순이라 잘린 건 과거분이다.
  · 분기별 스냅샷이 누적돼 있고, 과거분에는 7자리 행정구역코드가 섞여 있다.
  → **최신 개정일자만** 사용하면 둘 다 해결된다.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

SRC_ENC = "cp949"
OUT_NAME = "행정동_크로스워크.csv"
CONFLICT_NAME = "행정동_크로스워크_충돌.csv"

# 원본 컬럼 — 없으면 즉시 중단한다(포맷이 바뀌면 조용히 틀린 표가 나온다).
NEED = ["시도명", "시군구명", "행정동명", "행정구역코드", "행정동코드", "개정일자"]


def build(src: str, out_dir: str | None = None) -> str:
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    out_dir = out_dir or os.path.dirname(os.path.abspath(src))

    df = pd.read_csv(src, encoding=SRC_ENC, dtype=str)
    miss = [c for c in NEED if c not in df.columns]
    if miss:
        raise ValueError(f"원본 컬럼 없음: {miss}\n  실제: {df.columns.tolist()}")
    print(f"[원본] {len(df):,}행  {os.path.basename(src)}")
    if len(df) >= 1_048_575:
        print("  ⚠ 엑셀 행 한계에서 잘린 파일 — 과거 스냅샷 일부가 없습니다"
              " (최신분만 쓰므로 결과엔 영향 없음)")

    # ① 최신 스냅샷만 — 자릿수 혼재·이력 중복이 여기서 사라진다
    latest = df["개정일자"].max()
    d = df[df["개정일자"] == latest].copy()
    print(f"[최신] 개정일자 {latest}  {len(d):,}행")

    # ② 동 단위만 (8자리). 5자리=시군구, 2자리=시도 는 집계행이다.
    d = d[d["행정구역코드"].str.len() == 8]
    d["행정동코드8"] = d["행정동코드"].str.slice(0, 8)

    cw = (d[["행정구역코드", "행정동코드", "행정동코드8",
             "행정동명", "시도명", "시군구명"]]
          .drop_duplicates()
          .sort_values("행정구역코드"))
    print(f"[동 단위] {len(cw):,}행")

    # ③ 1:N 은 조회표로 못 쓴다 — 분리해 남기고 본표에서 뺀다
    n_map = cw.groupby("행정구역코드")["행정동코드8"].transform("nunique")
    conflict = cw[n_map > 1]
    cw = cw[n_map == 1].reset_index(drop=True)

    cw["기준일자"] = latest
    out = os.path.join(out_dir, OUT_NAME)
    cw.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"[저장] {out}  ({len(cw):,}행, {os.path.getsize(out)/1024:.0f} KB)")

    if len(conflict):
        cpath = os.path.join(out_dir, CONFLICT_NAME)
        conflict.to_csv(cpath, index=False, encoding="utf-8-sig")
        print(f"\n  ⚠ 1:N 충돌 {conflict['행정구역코드'].nunique()}개 코드"
              f" — 본표에서 제외하고 별도 저장: {cpath}")
        for code, grp in conflict.groupby("행정구역코드"):
            names = " / ".join(grp["행정동코드8"])
            print(f"     {code}  {grp['행정동명'].iloc[0]}"
                  f"({grp['시군구명'].iloc[0]})  ->  {names}")
        print("     해당 지역을 분석할 때 미매칭으로 중단되므로 그때 드러납니다.")

    # ④ 요약
    print(f"\n[요약] 시도 {cw['시도명'].nunique()}  "
          f"시군구 {cw['시군구명'].nunique()}  행정동 {len(cw):,}")
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
