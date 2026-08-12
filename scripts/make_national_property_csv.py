# -*- coding: utf-8 -*-
"""국유부동산 원본(xls/xlsx/csv) → `국유부동산_위경도_<지자체>.csv` 로 정규화한다.

왜 필요한가
  `make_parcel_candidates.attach_ownership()` 는 **CSV** 를 읽고 **경도·위도**
  컬럼을 요구한다. 지자체가 내려주는 원본에는 좌표가 없다(주소만 있다).
  용산 `국유부동산_위경도_v2.csv` 는 누군가 한 번 지오코딩해 만든 파일이고
  만든 방법이 남아 있지 않았다 — 그래서 성동구를 붙이려니 다시 만들 수가 없었다.
  이 스크립트가 그 절차다.

🔴 원본 .xls 는 OLE2 이고 문자열이 cp949 바이트로 들어 있다(latin1 로 올라온다).
   `pd.read_csv` 는 세 인코딩 모두 UnicodeDecodeError 로 실패하고, 호출부는
   경고 한 줄 남기고 **지분 태그를 통째로 생략**한다 — 안 터지고 값만 빠진다.

출력 컬럼은 용산 파일과 **같다**: 소재지(지번) · 지목(공부) · 대장면적(단위:㎡) · 경도 · 위도
못 찾은 주소는 **버리지 않고 좌표만 빈 칸**으로 남기고 개수를 출력한다
(지운 것은 안 지웠다고 말할 수 없다 — 원칙 4).

사용:
  python scripts/make_national_property_csv.py <원본> --region "서울특별시 성동구"
  python scripts/make_national_property_csv.py <원본> --region "..." --out <경로>
"""
import argparse
import csv
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import REGION_DATA_DIR, VWORLD_KEY  # noqa: E402

# 🔴 지오코더를 여기서 다시 구현하지 않는다. 처음엔 카카오 로컬 API 로 짰는데
#    (ⓐ 두 벌이 되고 ⓑ 우리 카카오 앱은 OPEN_MAP_AND_LOCAL 이 꺼져 있어 1,756건
#    전부 403 이었다 — 그런데 스크립트는 "못 찾음 1,769"로만 보고해 **주소가 나쁜
#    것처럼** 보였다). 정본은 VWorld 이고 주소 변형 폴백·토큰버킷·재시도가
#    이미 그 안에 있다(S6).
from app.services.gam2_audit_ops_catalog import _geocode_addr  # noqa: E402


def _demojibake(x):
    """xlrd 가 cp949 바이트를 latin1 로 올려준 것을 되돌린다."""
    if isinstance(x, str):
        try:
            return x.encode("latin1").decode("cp949")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return x
    return x


def read_source(path: str) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        for enc in ("utf-8-sig", "cp949", "utf-8"):
            try:
                return pd.read_csv(path, encoding=enc)
            except UnicodeDecodeError:
                continue
        raise SystemExit(f"[중단] CSV 인코딩을 못 정했다: {path}")

    # .xls(OLE2) 는 xlrd, .xlsx 는 openpyxl. 헤더 줄 위치는 찾아서 정한다.
    engine = "xlrd" if ext == ".xls" else None
    raw = pd.read_excel(path, engine=engine, header=None)
    raw = raw.map(_demojibake)
    hdr = None
    for i in range(min(10, len(raw))):
        row = [str(v) for v in raw.iloc[i].tolist()]
        if any("소재지" in v for v in row):
            hdr = i
            break
    if hdr is None:
        raise SystemExit(f"[중단] '소재지' 가 들어간 헤더 줄을 못 찾았다: {path}")
    d = raw.iloc[hdr + 1 :].reset_index(drop=True)
    d.columns = [str(v) for v in raw.iloc[hdr].tolist()]
    return d


def _to_num(v):
    """면적을 숫자로. 🔴 성동 원본 .xls 는 `'112.00'` 처럼 **작은따옴표로 감싼
    문자열**이다(엑셀 텍스트 서식). 그대로 옮겨 적었더니 소비쪽
    `attach_ownership` 에서 `grp[ar].sum()` 이 **문자열 연결**이 되고
    `국유_지분면적 / 면적` 이 `TypeError` 로 터졌다(r_20260812_012).
    용산 파일은 float64 였다 — 원본이 달라서 생긴 차이지 코드 회귀가 아니다.
    못 읽는 값은 추측하지 않고 빈 칸으로 둔다(원칙 1·4)."""
    if v is None:
        return ""
    s = str(v).strip().strip("'\"").replace(",", "")
    if not s:
        return ""
    try:
        return float(s)
    except ValueError:
        return ""


def pick(cols, *keys):
    for k in keys:
        for c in cols:
            if k in str(c):
                return c
    return None


def geocode_one(addr: str):
    """(경도, 위도) 또는 None. 지번 주소이므로 parcel 우선."""
    res, _used, _vi = _geocode_addr(addr, ["parcel", "road"])
    if not res:
        return None
    lat, lng, _matched = res
    return float(lng), float(lat)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="국유부동산 원본 파일")
    ap.add_argument("--region", required=True, help="예: '서울특별시 성동구'")
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    gu = args.region.split()[-1]
    out = args.out or str(Path(REGION_DATA_DIR) / gu / f"국유부동산_위경도_{gu}.csv")
    cache_path = Path(REGION_DATA_DIR) / gu / f".geocode_cache_{gu}.json"

    d = read_source(args.src)
    c_addr = pick(d.columns, "소재지(지번)", "소재지")
    c_jimok = pick(d.columns, "지목")
    c_area = pick(d.columns, "면적")
    if not c_addr:
        raise SystemExit(f"[중단] 소재지 컬럼 없음: {list(d.columns)}")
    print(f"원본 {len(d):,}행  주소={c_addr!r} 지목={c_jimok!r} 면적={c_area!r}")

    d = d[d[c_addr].notna()].copy()
    d[c_addr] = d[c_addr].astype(str).str.strip()

    # 🔴 다른 구 주소가 섞여 있으면 좌표는 붙지만 **필지에 안 떨어진다**.
    #    조용히 0건 조인이 되므로 여기서 세어 알린다.
    off = (~d[c_addr].str.contains(gu, na=False)).sum()
    if off:
        print(f"  ⚠ '{gu}' 가 없는 주소 {off}행 — 그대로 두되 조인에서 빠질 수 있다")

    cache = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    # 🔴 실패분(None)은 캐시로 치지 않는다. 첫 판(카카오 403)이 1,756건을 전부
    #    None 으로 적어놨는데, 그걸 캐시로 인정하면 지오코더를 고쳐도 **한 건도
    #    다시 안 부른다** — 조용히 0건인 채로 끝난다.
    cache = {k: v for k, v in cache.items() if v}
    if not VWORLD_KEY:
        raise SystemExit("[중단] VWORLD_API_KEY 없음")

    todo = sorted({a for a in d[c_addr] if a not in cache})
    print(f"지오코딩 대상 {len(todo):,}건 (캐시 {len(cache):,}건)")
    if todo:
        t0 = time.time()

        def work(a):
            return a, geocode_one(a)

        ok = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for i, (a, xy) in enumerate(ex.map(work, todo), 1):
                if xy:
                    cache[a] = xy
                    ok += 1
                if i % 200 == 0:
                    print(
                        f"  {i:,}/{len(todo):,}  성공 {ok:,}  {time.time() - t0:.0f}s"
                    )
        cache_path.write_text(
            json.dumps(cache, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  완료 {time.time() - t0:.0f}s  성공 {ok:,}/{len(todo):,}")
        # 🔴 전멸이면 "주소가 나쁘다"가 아니라 **지오코더가 안 돈다**. 여기서
        #    멈추지 않으면 좌표 0건짜리 CSV 가 정상 산출물처럼 남는다(원칙 1).
        if ok == 0:
            raise SystemExit(
                "[중단] 지오코딩 성공 0건 — 키·서비스 상태를 먼저 확인할 것"
            )

    rows = []
    miss = 0
    bad_area = 0
    for _, r in d.iterrows():
        a = r[c_addr]
        xy = cache.get(a)
        if not xy:
            miss += 1
        area = _to_num(r[c_area]) if c_area else ""
        if c_area and area == "":
            bad_area += 1
        rows.append(
            {
                "소재지(지번)": a,
                "지목(공부)": r[c_jimok] if c_jimok else "",
                "대장면적(단위:㎡)": area,
                "경도": xy[0] if xy else "",
                "위도": xy[1] if xy else "",
            }
        )

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["소재지(지번)", "지목(공부)", "대장면적(단위:㎡)", "경도", "위도"]
        )
        w.writeheader()
        w.writerows(rows)
    print(f"\n=== 결과 === {out}")
    print(f"  {len(rows):,}행 · 좌표 있음 {len(rows) - miss:,} · 못 찾음 {miss:,}")
    if bad_area:
        print(f"  ⚠ 면적을 숫자로 못 읽음 {bad_area:,}행 — 빈 칸으로 뒀다")


if __name__ == "__main__":
    main()
