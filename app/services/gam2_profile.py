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
  sample_rows, sampled(bool, 표본 추정 여부)

dataset_id 결정 우선순위
  1) 폴더의 _manifest.json (파일명↔ID 매핑) — 원본명 그대로 써도 ID 부여 (권장)
  2) 파일명 프리픽스(첫 '_' 앞) — 'A1_...' → 'A1'
  → REFERENCE(A1·B2…) 채점이 매칭되려면 둘 중 하나로 ID 가 A1·B2… 여야 함.

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
MANIFEST_NAME = "_manifest.json"  # 폴더 내 파일명↔dataset_id 매핑(선택)
ADDR_COL_KEYWORDS = ("주소", "소재지", "상세위치", "설치위치")  # 실주소 텍스트 컬럼
ADDR_COL_EXCLUDE = ("홈페이지", "이메일", "전자우편", "url", "코드")  # 오탐 제외
DATA_EXTENSIONS = (".csv", ".xlsx", ".xls", ".shp", ".json")
_NON_VALUE_COLS = ("geometry",)  # 샘플/중복에서 제외(shp geometry 등)


# ══════════════════════════════════════════════════════════════════
# 1. 파일 읽기 — 모두 str 로 읽어 값 원형 보존. 대용량은 nrows 표본.
# ══════════════════════════════════════════════════════════════════
def _read_csv(path: str, nrows: int | None = None) -> pd.DataFrame:
    """config.CSV_ENCODINGS 순차 시도(한국 공공데이터 인코딩 혼재 대응)."""
    last_err = None
    for enc in CSV_ENCODINGS:
        try:
            return pd.read_csv(
                path,
                encoding=enc,
                dtype=str,
                keep_default_na=True,
                nrows=nrows,
                low_memory=False,
            )
        except (UnicodeDecodeError, LookupError) as e:
            last_err = e
            continue
    # 최후 폴백: 원본에 깨진 바이트가 소수 섞인 경우 → cp949+replace 로 읽어 프로파일은 확보.
    print(
        f"[profile] ⚠ 인코딩 폴백 소진 → cp949+replace: {os.path.basename(path)} ({last_err})"
    )
    return pd.read_csv(
        path,
        encoding="cp949",
        encoding_errors="replace",
        dtype=str,
        keep_default_na=True,
        nrows=nrows,
        low_memory=False,
    )


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
    if raw.empty or raw.shape[1] < 2:
        return 0

    def _is_num(v) -> bool:
        s = str(v).strip().replace(",", "").replace("-", "").replace(".", "")
        return s.isdigit()

    for i in range(len(raw)):
        cells = [v for v in raw.iloc[i].tolist() if pd.notna(v) and str(v).strip()]
        if len(cells) < 2:
            continue  # 장식 행(제목·주석)은 1칸만 채워짐
        n_num = sum(1 for v in cells if _is_num(v))
        if n_num <= len(cells) / 2:  # 숫자가 절반 이하 → 헤더로 판단
            return i
    return 0


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


# ══════════════════════════════════════════════════════════════════
# 3. dataset_id 결정 (manifest 우선, 없으면 파일명 프리픽스)
# ══════════════════════════════════════════════════════════════════
def _load_manifest(folder: str) -> dict:
    path = os.path.join(folder, MANIFEST_NAME)
    if not os.path.isfile(path):
        return {}
    try:
        m = json.load(open(path, encoding="utf-8"))
        return m.get("map", m) if isinstance(m, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"[profile] ⚠ manifest 읽기 실패({e}) — 파일명 프리픽스로 폴백")
        return {}


def _assign_id(filename: str, manifest: dict) -> str:
    fn = unicodedata.normalize("NFC", filename)  # macOS zip NFD → NFC
    stem = os.path.splitext(fn)[0]
    if manifest:
        if fn in manifest:  # 정확한 파일명 키
            return manifest[fn]
        if stem in manifest:  # 확장자 뗀 키
            return manifest[stem]
        for key in sorted(manifest, key=len, reverse=True):  # 부분문자열(긴 키 우선)
            if unicodedata.normalize("NFC", key) in fn:
                return manifest[key]
    return stem.split("_")[0]  # 폴백: 'A1_...' → 'A1'


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
        dataset_id=dataset_id or _assign_id(os.path.basename(path), {}),
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
        sampled=(len(df) >= max_rows),  # 표본 추정이면 True
    )


def profile_folder(folder: str, max_rows: int = PROFILE_MAX_ROWS) -> dict:
    """데이터셋 폴더 → {dataset_id: profile}. _manifest.json 있으면 ID 매핑에 사용.
    txt/md(조례)·_manifest.json 은 제외. 실패 파일은 건너뛰고 경고."""
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"데이터셋 폴더 없음: {folder}")
    manifest = _load_manifest(folder)
    paths = []
    for ext in DATA_EXTENSIONS:
        paths += glob.glob(os.path.join(folder, f"*{ext}"))
    profiles: dict[str, dict] = {}
    for path in sorted(set(paths)):
        fname = os.path.basename(path)
        if fname == MANIFEST_NAME or fname.startswith("._"):  # 매핑파일·macOS 파편 제외
            continue
        did = _assign_id(fname, manifest)
        try:
            p = profile_file(path, dataset_id=did, max_rows=max_rows)
        except Exception as e:  # noqa: BLE001
            print(f"[profile] 건너뜀 {fname}: {e}")
            continue
        if did in profiles:
            print(f"[profile] ⚠ dataset_id 중복 '{did}' — 뒤 파일이 덮어씀: {fname}")
        profiles[did] = p
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
