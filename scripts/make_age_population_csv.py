# -*- coding: utf-8 -*-
"""행정안전부 「연령별(만)인구현황(기관별)」 교차표 → 행정동 1행짜리 CSV.

왜 필요한가
  `gam2_weight_model.attach_layers()` 는 admin 지표에서 **행정동 키 컬럼 1개 +
  수치 컬럼 1개**를 찾아 `groupby(키)[값].mean()` 한다. 그런데 행정안전부가
  내려주는 원본은 **교차표**다 — 제목 5줄이 앞에 붙고, 행정동이 **컬럼 방향**으로
  펼쳐지며(동마다 인구수·구성비·성비 3칸), 행 방향은 연령이다.
  즉 **행정동 컬럼이 아예 없다.** 헤더 줄만 맞춰 읽어도 조인 키가 안 생긴다.

  실측(그늘막 r_20260813_025): `[01] 행정동 조인 키(코드 또는 이름)를 찾지
  못했습니다. 컬럼: ['Unnamed: 0', ..., 'Unnamed: 68']` 로 STEP3-2 가 멈췄다.

🔴 값을 지어내지 않는다(원칙 2·5)
  - 연령 하한(65세 등)은 **파일에 있는 연령 라벨의 최솟값**에서 읽는다. 파일명이나
    상수로 정하지 않는다 — 같은 양식으로 60세/70세 자료가 내려온다.
  - 시군구 총계 컬럼은 「첫 컬럼이니까」로 버리지 않는다. **나머지 합과 같은지**
    실제로 대조하고, 안 맞으면 `SystemExit` 한다.
  - 행정동명은 원본 표기 그대로 옮긴다(정규화는 크로스워크 매칭 쪽 몫이다).

⚠ 출력 파일명은 원본과 **같은 이름**(확장자만 .csv)이 기본이다.
   `dataset_id` 가 파일명 가나다순이라 이름이 바뀌면 뒤 번호가 전부 밀린다.
   변환 후 원본 xlsx 는 `data/` 밖으로 옮긴다 — 둘 다 두면 데이터셋이 하나 는다.

사용:
  python scripts/make_age_population_csv.py <원본.xlsx>
  python scripts/make_age_population_csv.py <원본.xlsx> --out <경로.csv>
"""
import argparse
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

import pandas as pd

_SUB_VALUE = "인구수"  # 동마다 인구수/구성비/성비 3칸 — 우리가 쓰는 건 인구수뿐
_TOTAL_LABEL = "합계"  # 연령 라벨 중 전 연령 합계 행 (원본은 '합   계' 처럼 공백이 섞인다)
_BOTH_SEX = "계"  # 성별 칸의 남/여/계 중 계


def _sq(x) -> str:
    """공백을 전부 지운 문자열. 원본은 '합   계' 처럼 칸 맞춤 공백이 들어 있다."""
    return re.sub(r"\s+", "", str(x)) if pd.notna(x) else ""


def _find_header(raw: pd.DataFrame) -> tuple:
    """'연령' 이 적힌 칸을 찾는다 → (행, 열). 못 찾으면 SystemExit."""
    for r in range(min(len(raw), 30)):
        for c in range(raw.shape[1]):
            if _sq(raw.iat[r, c]) == "연령":
                return r, c
    raise SystemExit(
        "[중단] '연령' 헤더 칸을 찾지 못했습니다 — 이 양식이 아닙니다.\n"
        "  앞 10행: "
        + repr([[_sq(v) for v in raw.iloc[r, :6]] for r in range(min(10, len(raw)))])
    )


def _region_columns(raw: pd.DataFrame, hrow: int, hcol: int) -> list:
    """(행정동명, 컬럼index) 목록. 헤더는 병합셀이라 앞으로 채워 읽는다."""
    out, cur = [], None
    for c in range(hcol + 1, raw.shape[1]):
        name = _sq(raw.iat[hrow, c])
        if name:
            cur = name
        if _sq(raw.iat[hrow + 1, c]) == _SUB_VALUE and cur:
            out.append((cur, c))
    if not out:
        raise SystemExit(f"[중단] '{_SUB_VALUE}' 하위 헤더가 한 칸도 없습니다.")
    return out


def _total_row(raw: pd.DataFrame, hrow: int, hcol: int) -> int:
    """전 연령 합계 · 남녀 계 행. 성별 칸은 연령 라벨 바로 오른쪽이다."""
    for r in range(hrow + 2, len(raw)):
        if _sq(raw.iat[r, hcol]) == _TOTAL_LABEL:
            if _sq(raw.iat[r, hcol + 1]) != _BOTH_SEX:
                raise SystemExit(
                    f"[중단] 합계 행({r})의 성별 칸이 '{_BOTH_SEX}' 가 아닙니다: "
                    f"{_sq(raw.iat[r, hcol + 1])!r}"
                )
            return r
    raise SystemExit(f"[중단] '{_TOTAL_LABEL}' 행을 찾지 못했습니다.")


def _age_floor(raw: pd.DataFrame, hrow: int, hcol: int) -> int:
    """연령 라벨에서 하한을 읽는다. '65세'·'110세 이상' → 65."""
    ages = []
    for r in range(hrow + 2, len(raw)):
        m = re.match(r"^(\d+)세", _sq(raw.iat[r, hcol]))
        if m:
            ages.append(int(m.group(1)))
    if not ages:
        raise SystemExit("[중단] '<숫자>세' 연령 라벨이 한 줄도 없습니다.")
    return min(ages)


def _num(x, where: str) -> int:
    s = _sq(x).replace(",", "")
    if not re.fullmatch(r"-?\d+", s):
        raise SystemExit(f"[중단] {where} 값이 정수가 아닙니다: {_sq(x)!r}")
    return int(s)


def convert(src: Path, out: Path) -> None:
    raw = pd.read_excel(src, header=None) if src.suffix.lower() in (
        ".xls",
        ".xlsx",
    ) else pd.read_csv(src, header=None, dtype=str)

    hrow, hcol = _find_header(raw)
    regions = _region_columns(raw, hrow, hcol)
    trow = _total_row(raw, hrow, hcol)
    floor = _age_floor(raw, hrow, hcol)
    print(f"  헤더 ({hrow},{hcol}) · 합계행 {trow} · 연령하한 {floor}세 · 컬럼 {len(regions)}개")

    vals = [(nm, _num(raw.iat[trow, c], f"[{nm}]")) for nm, c in regions]

    # 🔴 첫 칸이 시군구 총계인지 **대조해서** 판정한다. 순서로 단정하지 않는다.
    head, rest = vals[0], vals[1:]
    s = sum(v for _, v in rest)
    if head[1] != s:
        raise SystemExit(
            f"[중단] 첫 컬럼 '{head[0]}'({head[1]:,}) 이 나머지 {len(rest)}개 합"
            f"({s:,})과 다릅니다 — 시군구 총계인지 확정할 수 없습니다."
        )
    print(f"  총계 컬럼 '{head[0]}' {head[1]:,} = 하위 {len(rest)}개 합 — 제외")

    col = f"{floor}세이상_인구수"
    df = pd.DataFrame(rest, columns=["행정동", col])
    if df["행정동"].duplicated().any():
        dup = sorted(df.loc[df["행정동"].duplicated(), "행정동"])
        raise SystemExit(f"[중단] 행정동명이 중복입니다: {dup}")

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"✅ {out}  ({len(df)}행 · 합 {df[col].sum():,})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    src = Path(a.src).resolve()
    if not src.exists():
        raise SystemExit(f"[중단] 원본이 없습니다: {src}")
    out = Path(a.out).resolve() if a.out else src.with_suffix(".csv")
    if out == src:
        raise SystemExit("[중단] 출력이 원본과 같은 경로입니다.")
    convert(src, out)


if __name__ == "__main__":
    main()
