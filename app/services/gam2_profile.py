# -*- coding: utf-8 -*-
"""
OmniSite 감리 AI — 데이터 프로파일러 (profile)
==============================================
목적: 실제 데이터 파일(csv/xlsx/xls/shp/json)을 읽어, 감리 AI 가 판정에 쓰는
      "프로파일 요약" dict 를 자동 생성. (audit_judgment_test 의 손 FIXTURES 대체)

설계 원칙
  - 도메인 무관: 흡연·EV·음식물함 어떤 데이터든 파일만 읽어 프로파일. 시설명/조례 모름.
  - 조례 주입 안 함: 조례는 audit_judgment_test 가 (a)안대로 전 데이터셋에 붙인다.
  - 목적에 비례하는 정밀도: 감리 AI 는 스키마+샘플+좌표/주소 신호만 필요.
    → 대용량 파일은 상위 N행만 표본으로 읽고(schema·sample·null·dup 추정),
      행수만 값싸게 별도 집계. (완벽 파싱에 매달리지 않음)

출력 dict 필드 (build_prompt / run_harness 가 기대하는 계약)
  dataset_id, filename, extension, columns, row_count, null_coords,
  has_coord_col, has_addr_col, addr_cols, coord_cols, dup_estimate,
  sample_rows, value_dist(저카디널리티 컬럼 값 분포), sampled(bool, 표본 추정 여부)

dataset_id 결정
  폴더의 데이터 파일을 '파일명 가나다순'으로 정렬해 '01','02'… 두 자리 번호를 부여한다.
  (OS 나열 순서에 의존하지 않도록 정렬을 고정. 별도 매핑 파일은 쓰지 않는다.)
  ⚠ 파일을 추가/삭제하면 뒤 번호가 밀린다 → 재프로파일 → 재감리가 원칙.

사용
  from app.services.gam2_profile import profile_folder
  profiles = profile_folder("데이터셋_흡연/")   # {dataset_id: profile_dict}
  python profile.py 데이터셋_흡연/               # 단독 실행(요약 출력)
"""

from __future__ import annotations

import glob
import json
import os
import unicodedata

import pandas as pd

from app.config import CSV_ENCODINGS, COORD_COL_CANDIDATES


# ── 프로파일 파라미터 (추후 config 로 이동 가능) ──
PROFILE_MAX_ROWS = 50000  # 대용량 파일은 이만큼만 표본으로 읽어 프로파일
_SKIP_PREFIXES = (
    "_",
    ".",
)  # '_'·'.' 로 시작하는 파일은 데이터가 아님(부속·숨김·macOS 잔재)
ADDR_COL_KEYWORDS = ("주소", "소재지", "상세위치", "설치위치")  # 실주소 텍스트 컬럼
ADDR_COL_EXCLUDE = ("홈페이지", "이메일", "전자우편", "url", "코드")  # 오탐 제외
DATA_EXTENSIONS = (".csv", ".xlsx", ".xls", ".shp", ".json")
_NON_VALUE_COLS = ("geometry",)  # 샘플/중복에서 제외(shp geometry 등)

# 값 분포(value_dist) 파라미터 — 저카디널리티 컬럼만 값 목록을 통째로 싣는다.
#   sample_rows(2행)로는 카테고리 컬럼의 값 집합을 알 수 없다. 감리 AI 가
#   filter_by_value 의 allowed 를 '본 값'으로만 채워 조용히 행을 잃는다.
CATEGORY_MAX_UNIQUE = (
    30  # 고유값이 이보다 많으면 카테고리로 보지 않는다(자유 텍스트·ID)
)
CATEGORY_MAX_COLS = 25  # 프롬프트 비대화 방지
CATEGORY_VAL_MAXLEN = 40  # 값 하나라도 이보다 길면 그 컬럼은 싣지 않는다(자유 텍스트)


# ══════════════════════════════════════════════════════════════════
# 1. 파일 읽기 — 모두 str 로 읽어 값 원형 보존. 대용량은 nrows 표본.
# ══════════════════════════════════════════════════════════════════
def _read_csv(path: str, nrows: int | None = None) -> pd.DataFrame:
    """config.CSV_ENCODINGS 순차 시도(한국 공공데이터 인코딩 혼재 대응).
    index_col=False 필수: 헤더보다 데이터 필드가 많으면(행 끝 여분 콤마 등) pandas 가
    첫 컬럼을 index 로 삼아 컬럼이 통째로 한 칸씩 밀린다 → 감리가 틀린 스키마를 본다."""
    last_err = None
    for enc in CSV_ENCODINGS:
        try:
            return _fix_csv_header(
                pd.read_csv(
                    path,
                    encoding=enc,
                    dtype=str,
                    index_col=False,
                    keep_default_na=True,
                    nrows=nrows,
                    low_memory=False,
                ),
                path,
                nrows,
            )
        except pd.errors.ParserError:
            # 제목 행의 콤마 수가 데이터 행보다 적으면 pandas 가 컬럼 수를 적게 잡고
            # 데이터 행에서 파싱 에러를 낸다(예: 1행 '○○구 통계' / 3행 '동명,인구').
            # → 컬럼 수를 파일에서 직접 세어 header 없이 읽은 뒤 헤더를 교정한다.
            try:
                return _fix_csv_header(
                    _read_csv_headerless(path, enc, nrows), path, nrows
                )
            except (UnicodeDecodeError, LookupError):
                continue
        except (UnicodeDecodeError, LookupError) as e:
            last_err = e
            continue
    # 최후 폴백: 원본에 깨진 바이트가 소수 섞인 경우 → cp949+replace 로 읽어 프로파일은 확보.
    print(
        f"[profile] ⚠ 인코딩 폴백 소진 → cp949+replace: {os.path.basename(path)} ({last_err})"
    )
    return _fix_csv_header(
        pd.read_csv(
            path,
            encoding="cp949",
            encoding_errors="replace",
            dtype=str,
            index_col=False,
            keep_default_na=True,
            nrows=nrows,
            low_memory=False,
        ),
        path,
        nrows,
    )


def _read_csv_headerless(path: str, enc: str, nrows: int | None) -> pd.DataFrame:
    """행마다 필드 수가 다른 CSV 대응. 파일에서 최대 필드 수를 직접 세어
    header 없이(names 지정) 읽는다. 헤더 지정은 _fix_csv_header 가 이어서 한다."""
    import csv as _csv

    ncol = 0
    with open(path, encoding=enc, errors="replace", newline="") as f:
        for i, row in enumerate(_csv.reader(f)):
            ncol = max(ncol, len(row))
            if i >= 50:  # 앞부분만 봐도 충분
                break
    ncol = max(ncol, 1)
    return pd.read_csv(
        path,
        encoding=enc,
        dtype=str,
        header=None,
        names=list(range(ncol)),
        index_col=False,
        keep_default_na=True,
        nrows=nrows,
        engine="python",
        on_bad_lines="skip",
    )


def _fix_csv_header(df: pd.DataFrame, path: str, nrows: int | None) -> pd.DataFrame:
    """CSV 상단 장식 행(제목·작성기준) 대응. 엑셀에만 있던 헤더 교정을 CSV 에도 적용한다.
    (예: 1행 '○○구 인구 및 세대현황' / 2행 '행정기관,세대,인구…' → 그대로 읽으면
     제목이 컬럼명이 되고 나머지가 Unnamed 로 잡혀, 감리가 지정한 컬럼과 어긋난다)
    정상 파일은 건드리지 않는다(Unnamed 가 절반 미만이면 그대로 반환)."""
    if not _header_looks_broken(df.columns):
        return df
    hdr = _header_row_from_rows(df.head(8))  # 이미 읽은 앞부분으로 판별
    if hdr < 0 or hdr >= len(df):  # hdr==0 도 유효(첫 데이터 행이 진짜 헤더)
        return df
    new_cols = [
        str(v).strip() if pd.notna(v) and str(v).strip() else f"col_{i}"
        for i, v in enumerate(df.iloc[hdr].tolist())
    ]
    out = df.iloc[hdr + 1 :].reset_index(drop=True)
    out.columns = new_cols
    print(f"[profile] 헤더 교정: {os.path.basename(path)} — {hdr}행을 헤더로 사용")
    return out.head(nrows) if nrows else out


def _header_row_from_rows(raw: pd.DataFrame) -> int:
    """상단 장식 행을 건너뛴 '진짜 헤더' 행 번호. 파일 형식과 무관한 공통 판별.
    (CSV·엑셀 모두 공공기관 자료에 제목/작성기준 행이 붙는 패턴이 흔하다)

    헤더 행의 특징(결정론적):
      · 채워진 셀이 2개 이상 (장식 행은 보통 1칸)
      · 값이 텍스트 위주 (데이터 행은 숫자가 대부분 — 인구수·세대수 등)
    → 위 둘을 만족하는 '첫' 행 번호. 없으면 -1.
      (0 = '첫 행이 헤더'와 '못 찾음'을 구분해야 하므로 -1 을 쓴다)
    """
    if raw is None or raw.empty or raw.shape[1] < 2:
        return -1

    def _is_num(v) -> bool:
        t = str(v).strip().replace(",", "").replace("-", "").replace(".", "")
        return t.isdigit()

    for i in range(len(raw)):
        cells = [v for v in raw.iloc[i].tolist() if pd.notna(v) and str(v).strip()]
        if len(cells) < 2:
            continue  # 장식 행(제목·주석)은 1칸만 채워짐
        n_num = sum(1 for v in cells if _is_num(v))
        if n_num <= len(cells) / 2:  # 숫자가 절반 이하 → 헤더로 판단
            return i
    return -1


def _header_looks_broken(cols) -> bool:
    """읽어온 컬럼이 '제목 행을 헤더로 잡은' 모양인지. Unnamed 가 절반 이상이면 그렇다."""
    cols = list(cols)
    if len(cols) < 2:
        return False
    unnamed = sum(1 for c in cols if str(c).startswith("Unnamed") or str(c).isdigit())
    return unnamed >= len(cols) / 2


def _detect_header_row(path: str, max_scan: int = 8) -> int:
    """공공기관 엑셀은 상단에 제목·작성기준·주석 같은 '장식 행'이 붙는 경우가 많다.
    (예: row0 '성동구 인구 및 세대현황' / row1 '작성기준 : …' / row3 부터 진짜 헤더)
    그대로 읽으면 제목이 컬럼명이 되고 나머지가 Unnamed 로 잡혀 감리가 컬럼을 못 본다.

    헤더 행의 특징(결정론적 판별):
      · 채워진 셀이 2개 이상 (장식 행은 보통 1칸)
      · 값이 '텍스트' 위주 (데이터 행은 숫자가 대부분 — 인구수·세대수 등)
    → 위 둘을 만족하는 '첫' 행을 헤더로 본다. 없으면 0.
    """
    try:
        raw = pd.read_excel(path, header=None, nrows=max_scan, dtype=str)
    except Exception:
        return 0
    return max(0, _header_row_from_rows(raw))


def _read_excel(path: str, nrows: int | None = None) -> pd.DataFrame:
    """엑셀 로드. 상단 장식 행(제목·주석)을 건너뛰고 진짜 헤더부터 읽는다.
    2단(병합) 헤더면 하위 헤더 행을 합쳐 컬럼명을 만든다."""
    hdr = _detect_header_row(path)
    df = pd.read_excel(
        path, dtype=str, nrows=nrows, header=hdr
    )  # xlsx=openpyxl, xls=xlrd
    df.columns = [str(c).strip() for c in df.columns]

    # 2단 병합 헤더 처리: 상위 헤더가 병합되면 하위 칸이 Unnamed 로 남고,
    #   실제 소제목(계/남/여 등)은 '첫 데이터 행'에 들어온다.
    #   → 첫 행이 숫자 없이 텍스트뿐이고 Unnamed 컬럼이 있으면, 그 행을 헤더 2단으로 결합.
    if len(df) and any(c.startswith("Unnamed") for c in df.columns):
        first = df.iloc[0]
        vals = [v for v in first.tolist() if pd.notna(v) and str(v).strip()]

        def is_num(v):
            return str(v).strip().replace(",", "").replace(".", "").isdigit()

        if vals and not any(is_num(v) for v in vals):  # 첫 행이 전부 텍스트 → 하위 헤더
            new_cols, parent = [], ""
            for c, sub in zip(df.columns, first.tolist()):
                if not c.startswith("Unnamed"):
                    parent = c  # 상위 헤더 갱신
                sub = str(sub).strip() if pd.notna(sub) else ""
                if sub and c.startswith("Unnamed"):
                    new_cols.append(f"{parent}_{sub}" if parent else sub)
                elif sub and parent:
                    new_cols.append(f"{parent}_{sub}")  # 상위+하위 (예: 인구수_계)
                else:
                    new_cols.append(c)
            df.columns = new_cols
            df = df.iloc[1:].reset_index(drop=True)  # 하위 헤더 행 제거
    return df


def _read_shp(path: str, nrows: int | None = None) -> pd.DataFrame:
    import geopandas as gpd

    # 한국 공간데이터(.dbf)는 cp949 가 흔함(.cpg 미인식 대비) → 인코딩 폴백.
    last = None
    for enc in (None, "cp949", "euc-kr"):
        try:
            kw: dict = {}
            if nrows:
                kw["rows"] = nrows
            if enc:
                kw["encoding"] = enc
            return gpd.read_file(path, **kw)
        except TypeError:  # 구버전: rows 미지원
            kw.pop("rows", None)
            try:
                return gpd.read_file(path, **kw)
            except (UnicodeDecodeError, Exception) as e:  # noqa: BLE001
                last = e
        except UnicodeDecodeError as e:
            last = e
            continue
    raise RuntimeError(f"SHP 읽기 실패(enc 폴백 소진): {last}")


def _shp_feature_count(path: str) -> int | None:
    """필지 수를 메타만 읽어 값싸게 집계(geometry 미로딩). 실패 시 None."""
    try:
        import pyogrio

        return int(pyogrio.read_info(path)["features"])
    except Exception:  # noqa: BLE001
        return None


def _read_json(path: str, nrows: int | None = None) -> pd.DataFrame:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        df = pd.DataFrame(data, dtype=str)
    elif isinstance(data, dict):
        df = None
        for v in data.values():  # {..:[레코드]} 흔한 형태
            if isinstance(v, list) and v and isinstance(v[0], dict):
                df = pd.DataFrame(v, dtype=str)
                break
        if df is None:
            df = pd.json_normalize(data).astype(str)
    else:
        df = pd.DataFrame()
    return df.head(nrows) if nrows else df


def _read_sample(path: str, ext: str, nrows: int | None) -> pd.DataFrame:
    if ext == ".csv":
        return _read_csv(path, nrows)
    if ext in (".xlsx", ".xls"):
        return _read_excel(path, nrows)
    if ext == ".shp":
        return _read_shp(path, nrows)
    if ext == ".json":
        return _read_json(path, nrows)
    raise ValueError(f"지원하지 않는 확장자: {ext}")


def _linecount_csv(path: str) -> int:
    """CSV 총 행수(헤더 제외)를 값싸게 집계. 개행 바이트만 세므로 인코딩 무관.
    (따옴표 내 개행이 있으면 약간 과대 — 프로파일 용도라 근사 허용)"""
    cnt = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            cnt += chunk.count(b"\n")
    return max(cnt - 1, 0)


# ══════════════════════════════════════════════════════════════════
# 2. 프로파일 계산 헬퍼
# ══════════════════════════════════════════════════════════════════
def _detect_coord_cols(columns) -> list[str]:
    return [c for c in columns if c in COORD_COL_CANDIDATES]


def _detect_addr_cols(columns) -> list[str]:
    out = []
    for c in columns:
        name = str(c)
        if any(k in name for k in ADDR_COL_KEYWORDS) and not any(
            x in name.lower() for x in ADDR_COL_EXCLUDE
        ):
            out.append(c)
    return out


def _count_null_coords(df, coord_cols, has_addr, row_count) -> int:
    """좌표 결측 행 수(FIXTURES 규칙):
    - 좌표컬럼 있으면: 표본의 결측률 × row_count 로 추정(표본이 전체면 정확)
    - 좌표없고 주소만: 전 행이 지오코딩 대상 → row_count
    - 좌표도 주소도 없으면(통계·공간): 0
    """
    if coord_cols:
        sub = df[coord_cols]
        blank = sub.apply(lambda s: s.astype(str).str.strip().isin(["", "nan", "None"]))
        null_in_sample = int((sub.isna().any(axis=1) | blank.any(axis=1)).sum())
        n = len(df)
        if n and row_count > n:
            return int(round(null_in_sample / n * row_count))
        return null_in_sample
    if has_addr:
        return row_count
    return 0


def _dup_estimate(df) -> int:
    value_cols = [c for c in df.columns if c not in _NON_VALUE_COLS]
    if not value_cols:
        return 0
    try:
        return int(df[value_cols].duplicated().sum())  # 표본 기준(근사)
    except TypeError:
        return 0


def _sample_rows(df, n: int = 2) -> list[dict]:
    cols = [c for c in df.columns if c not in _NON_VALUE_COLS]
    out = []
    for _, row in df[cols].head(n).iterrows():
        out.append({c: ("" if pd.isna(row[c]) else str(row[c])) for c in cols})
    return out


def _is_numericish(vals: list) -> bool:
    """값 대부분이 숫자로 파싱되면 카테고리가 아니다(연도·개수·코드 등)."""
    if not vals:
        return True
    ok = 0
    for v in vals:
        try:
            float(str(v).strip().replace(",", ""))
            ok += 1
        except ValueError:
            pass
    return ok / len(vals) >= 0.8


def _value_dist(df, max_unique: int = CATEGORY_MAX_UNIQUE) -> dict:
    """고유값이 적은 컬럼의 **값 분포 전체**. {컬럼: {고유수, 값:{값:건수}, 결측}}

    왜 필요한가 (2026-08-03 실측)
      sample_rows 는 앞 2행뿐이다. 어린이집 `운영현황` 은 9,483행 중
      정상 3,835 / 폐지 5,504 / 재개 75 / 휴지 66 인데, 앞 2행이 둘 다 '정상'이고
      '재개' 는 30번째 행에 처음 나온다. 감리 AI 가 볼 수 없으니
      filter_by_value(allowed=['정상']) 를 냈고, **운영 중인 시설이 조용히
      배제에서 빠졌다.** 행 수는 그럴듯해서 자동 검증으로는 안 잡힌다.

      고유값이 적은 컬럼은 값 목록을 통째로 실어도 토큰이 거의 안 든다.
      '추측해서 열거' 를 '보고 고르기' 로 바꾸는 것이 요점이다.

    ⚠ 대용량 파일은 상위 N행 표본이므로 건수는 근사다(profile.sampled=True).
       값 **집합**은 표본에서도 대체로 보존되지만, 극히 드문 값은 빠질 수 있다.
    """
    out: dict = {}
    for c in df.columns:
        if c in _NON_VALUE_COLS or len(out) >= CATEGORY_MAX_COLS:
            continue
        s = df[c]
        try:
            nun = int(s.nunique(dropna=True))
        except TypeError:  # 해시 불가 타입(list 등)
            continue
        if nun == 0 or nun > max_unique:
            continue
        uniq = [str(v) for v in s.dropna().unique()]
        if _is_numericish(uniq):
            continue
        # 값 하나라도 길면 자유 텍스트다. 자르면 allowed 에 잘린 값이 들어가
        #   0행이 되므로 **자르지 말고 컬럼 자체를 뺀다.**
        if any(len(v) > CATEGORY_VAL_MAXLEN for v in uniq):
            continue
        vc = s.value_counts(dropna=True)
        out[c] = {
            "고유수": nun,
            "값": {str(k): int(v) for k, v in vc.items()},
            "결측": int(s.isna().sum()),
        }
    return out


# ══════════════════════════════════════════════════════════════════
# 3. dataset_id 결정 (폴더 단위: 가나다순 번호 / 단독 호출: 파일명 프리픽스)
# ══════════════════════════════════════════════════════════════════
def _assign_id(filename: str) -> str:
    """단일 파일을 profile_file 로 직접 부를 때만 쓰는 폴백 ID.
    폴더 단위(profile_folder)는 가나다순 seq 번호를 쓰므로 여기로 오지 않는다."""
    fn = unicodedata.normalize("NFC", filename)  # macOS zip NFD → NFC
    stem = os.path.splitext(fn)[0]
    return stem.split("_")[0]  # 'A1_...' → 'A1'


# 지역 접두어·출처기관·접미어는 '이름 정리용 불용어'다(지역 하드코딩 아님).
_GEO_PREFIX = ["서울특별시", "서울시", "전국", "경기도", "인천광역시", "부산광역시"]
_STOP_TAIL = [
    "표준데이터",
    "표준 데이터",
    "위치정보",
    "기본정보",
    "세대현황",
    "현황",
    "정보",
    "데이터",
    "서울",
    "마스터",
]


# ══════════════════════════════════════════════════════════════════
# 4. 공개 API
# ══════════════════════════════════════════════════════════════════
def profile_file(
    path: str, dataset_id: str | None = None, max_rows: int = PROFILE_MAX_ROWS
) -> dict:
    """단일 파일 → 프로파일 dict. (조례는 상위에서 주입)"""
    ext = os.path.splitext(path)[1].lower()
    df = _read_sample(path, ext, max_rows)
    columns = list(df.columns)
    if ext == ".csv":
        row_count = _linecount_csv(path)
    elif ext == ".shp":
        row_count = _shp_feature_count(path) or int(len(df))  # 메타로 실제 필지수
    else:
        row_count = int(len(df))
    if row_count < len(df):  # 방어(따옴표 개행 등)
        row_count = int(len(df))
    coord_cols = _detect_coord_cols(columns)
    addr_cols = _detect_addr_cols(columns)
    return dict(
        dataset_id=dataset_id or _assign_id(os.path.basename(path)),
        filename=unicodedata.normalize("NFC", os.path.basename(path)),
        extension=ext.lstrip("."),
        columns=columns,
        row_count=row_count,
        null_coords=_count_null_coords(df, coord_cols, bool(addr_cols), row_count),
        has_coord_col=bool(coord_cols),
        has_addr_col=bool(addr_cols),
        addr_cols=addr_cols,
        coord_cols=coord_cols,
        dup_estimate=_dup_estimate(df),
        sample_rows=_sample_rows(df),
        value_dist=_value_dist(df),
        sampled=(len(df) >= max_rows),  # 표본 추정이면 True
    )


def list_dataset_files(folder: str) -> list[str]:
    """dataset_id 가 붙을 데이터 파일 경로를 **부여 순서 그대로** 돌려준다.

    `profile_folder` 가 쓰는 규칙 자체다. 별도 함수로 뺀 이유는 프로파일을
    돌리지 않고도 "이 폴더에 파일을 넣으면 번호가 어떻게 밀리는지" 를 물어야 하는
    곳(업로드 API)이 있기 때문이다. 규칙을 그쪽에 다시 쓰면 사본이 되고,
    사본은 갈라져도 안 터진다 — 번호만 조용히 달라진다.
    """
    paths = []
    for ext in DATA_EXTENSIONS:
        paths += glob.glob(os.path.join(folder, f"*{ext}"))
    # 데이터 파일만, 파일명 가나다순으로 확정(실행 간 번호 안정성 — OS 나열 순서 의존 X)
    return sorted(
        pp for pp in set(paths) if not os.path.basename(pp).startswith(_SKIP_PREFIXES)
    )


def profile_folder(folder: str, max_rows: int = PROFILE_MAX_ROWS) -> dict:
    """데이터셋 폴더 → {dataset_id: profile}.
    dataset_id 는 파일명 가나다순 '01','02'… (별도 매핑 파일 없음).
    txt/md(조례)·'_'·'.' 로 시작하는 부속 파일은 제외. 실패 파일은 건너뛰고 경고."""
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"데이터셋 폴더 없음: {folder}")
    data_paths = list_dataset_files(folder)

    print("[profile] dataset_id — 파일명 가나다순으로 01,02… 부여")
    print(
        "           ⚠ 파일을 추가/삭제하면 뒤 번호가 밀립니다. "
        "이미 감리·정제를 돌렸다면 step1_output 의 reviewed.json·캐시와 "
        "번호가 어긋나므로 재프로파일 → 재감리하세요."
    )

    profiles: dict[str, dict] = {}
    for seq, path in enumerate(data_paths, 1):
        fname = os.path.basename(path)
        did = f"{seq:02d}"  # 가나다순 2자리 번호
        try:
            prof = profile_file(path, dataset_id=did, max_rows=max_rows)
        except Exception as e:  # noqa: BLE001
            print(f"[profile] 건너뜀 {fname}: {e}")
            continue
        if did in profiles:
            print(f"[profile] ⚠ dataset_id 중복 '{did}' — 뒤 파일이 덮어씀: {fname}")
        profiles[did] = prof
        print(f"  [{did}] {fname}")
    return profiles


def save_profiles(profiles: dict, out_path: str) -> str:
    """프로파일 dict → JSON 저장(간소화 fixture). 폴더 없으면 생성."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)
    return out_path


# ══════════════════════════════════════════════════════════════════
# 5. 단독 실행 — 도메인 폴더 → data/ 프로파일 → fixture/profiles.json
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    from app.config import domain_paths

    if len(sys.argv) < 2:
        print(
            "사용법: python profile.py <도메인폴더>   예: python profile.py EV_데이터셋"
        )
        sys.exit(1)
    domain_dir = sys.argv[1]
    p = domain_paths(domain_dir)
    profs = profile_folder(p["data"])
    save_profiles(profs, p["profiles"])
    print(f"\n[profile] '{p['data']}' → {len(profs)}개 데이터셋  →  {p['profiles']}\n")
    for did, prof in sorted(profs.items()):
        flags = []
        if prof["has_coord_col"]:
            flags.append(f"좌표{prof['coord_cols']}")
        if prof["has_addr_col"]:
            flags.append(f"주소{prof['addr_cols']}")
        if prof["null_coords"]:
            flags.append(f"좌표결측 {prof['null_coords']}")
        if prof["dup_estimate"]:
            flags.append(f"중복 {prof['dup_estimate']}")
        if prof["sampled"]:
            flags.append("※표본추정")
        print(
            f"  {did:4s} {prof['filename']}  ({prof['extension']}, {prof['row_count']:,}행)"
        )
        print(f"       {' | '.join(flags) if flags else '(좌표/주소/중복 없음)'}")
