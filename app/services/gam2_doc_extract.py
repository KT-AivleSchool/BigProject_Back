# -*- coding: utf-8 -*-
"""
OmniSite 문서 텍스트 추출 (조례·법령 원문)
==========================================
바이너리 문서에서 **텍스트만** 뽑아 `.txt` 로 옆에 캐시한다.

왜 분리했나
  조례는 지자체마다 배포 형식이 다르다(txt·md·pdf·hwp·docx).
  파이프라인 본체는 "텍스트를 읽는다"만 알면 되고, 어떤 형식을 어떻게 여는지는
  이 모듈이 안다. 새 형식 지원은 EXTRACTORS 에 한 줄 추가로 끝난다.

  ※ 추출은 **부가 기능**이다. 의존 패키지가 없으면 그 형식만 건너뛰고
    파이프라인은 그대로 돈다(설치 강제 금지).

캐시 규약
  <원본>.pdf  ->  <원본>.pdf.txt
  원본보다 최신이면 재사용. 원본이 바뀌면 자동 재추출(mtime 비교).
  .txt 로 떨어지므로 load_ordinance 의 기존 수집 경로에 그대로 걸린다.

단독 실행
  python gam2_doc_extract.py <폴더 또는 파일> [--force]
"""

from __future__ import annotations

import glob
import os

# 확장자 -> 추출 함수명. 지원 형식을 늘리려면 여기에 추가한다.
EXTRACTORS: dict[str, str] = {
    ".pdf": "_from_pdf",
    ".docx": "_from_docx",
    ".hwpx": "_from_hwpx",
}

TEXT_EXT = (".txt", ".md")  # 변환 없이 바로 읽히는 형식
CACHE_SUFFIX = ".txt"  # <원본>.pdf.txt


# =========================================================
# 형식별 추출기 — 의존 패키지가 없으면 ImportError 를 그대로 올린다.
#   호출부(extract_text)가 잡아서 '건너뜀' 으로 처리한다.
# =========================================================
def _from_pdf(path: str) -> str:
    """PDF 텍스트 추출 (PyMuPDF).

    🔴 예전엔 pdfplumber → pypdf 순으로 시도했는데 **둘 다 requirements.txt 에
       없었다**(2026-08-23). 로컬엔 손으로 깔려 있어 안 보였고, 배포 컨테이너에서만
       `ModuleNotFoundError: pypdf` 로 조례 PDF 가 통째로 텍스트 0자가 됐다.
       업로드는 그걸 "스캔본이거나 패키지가 없습니다" 로 알려서 스캔본으로 읽혔다.
       엔진을 늘리지 않고 **줄여서** 고친다 — 이 저장소의 다른 PDF 추출기 둘
       (`sim_ai/document_loader.py`·`audit_ai/parser.py`)은 처음부터 PyMuPDF 를
       썼다. 한 벌로 합쳐야 로컬과 서버가 같은 텍스트를 낸다.

    `sort=True` 는 필수다. 기본값은 PDF 내부 저장 순서라 조판에 따라 제목·머리말이
    본문 뒤로 밀린다(실측: EV 시행령 PDF 에서 제목 2줄이 맨 끝으로 갔다).
    같은 이유가 `sim_ai/document_loader.py:28` 에도 적혀 있다 — 용산구 조례에서
    조문 첫 줄이 앞 조문 꼬리에 붙어 파싱이 전량 오정렬됐다.

    스캔본(이미지 PDF)은 텍스트가 안 나온다 — 빈 문자열이 반환되며
    호출부가 경고한다. OCR 은 범위 밖이다(별도 파이프라인).
    """
    import pymupdf

    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text("text", sort=True) for page in doc)


def _from_docx(path: str) -> str:
    import docx

    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:  # 조례는 표로 된 별표가 많다
        for row in t.rows:
            parts.append("\t".join(c.text for c in row.cells))
    return "\n".join(parts)


def _from_hwpx(path: str) -> str:
    """HWPX(한글 2014+ XML 포맷). 구형 .hwp 바이너리는 지원하지 않는다."""
    import re
    import zipfile

    parts = []
    with zipfile.ZipFile(path) as z:
        for n in sorted(x for x in z.namelist() if x.endswith(".xml")):
            if "section" not in n.lower():
                continue
            xml = z.read(n).decode("utf-8", errors="replace")
            xml = re.sub(r"<[^>]+>", "\n", xml)
            parts.append(xml)
    return re.sub(r"\n{2,}", "\n", "\n".join(parts))


# =========================================================
# 캐시 · 공개 API
# =========================================================
def cache_path_of(path: str) -> str:
    return path + CACHE_SUFFIX


def _is_fresh(src: str, cache: str) -> bool:
    """캐시가 원본보다 최신인가. 원본이 갱신되면 재추출한다."""
    return os.path.isfile(cache) and os.path.getmtime(cache) >= os.path.getmtime(src)


def extract_text(path: str, force: bool = False, verbose: bool = True) -> str | None:
    """단일 파일 -> 텍스트. 변환 불필요/불가면 None.

    반환 None 의 의미
      · 이미 텍스트 형식(.txt/.md)      — 호출부가 원래 경로로 읽으면 된다
      · 지원하지 않는 확장자
      · 의존 패키지 없음                — 경고만 남기고 건너뛴다
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXT:
        return None
    fn = EXTRACTORS.get(ext)
    if not fn:
        return None

    cache = cache_path_of(path)
    if not force and _is_fresh(path, cache):
        try:
            with open(cache, encoding="utf-8") as f:
                return f.read()
        except OSError:
            pass

    try:
        text = globals()[fn](path)
    except ImportError as e:
        if verbose:
            pkg = {
                "_from_pdf": "pymupdf",
                "_from_docx": "python-docx",
                "_from_hwpx": "(내장)",
            }.get(fn, "")
            print(f"  ⚠ {os.path.basename(path)} 추출 불가 — {pkg} 미설치 ({e})")
        return None
    except Exception as e:
        if verbose:
            print(f"  ⚠ {os.path.basename(path)} 추출 실패 ({e})")
        return None

    text = (text or "").strip()
    if not text:
        if verbose:
            print(
                f"  ⚠ {os.path.basename(path)} 에서 텍스트 0자 — "
                f"스캔본(이미지 PDF)일 수 있습니다. OCR 이 필요합니다."
            )
        return None

    try:
        with open(cache, "w", encoding="utf-8") as f:
            f.write(text)
        if verbose:
            print(
                f"  ⓘ {os.path.basename(path)} → {os.path.basename(cache)} "
                f"({len(text):,}자)"
            )
    except OSError as e:
        if verbose:
            print(f"  ⚠ 캐시 저장 실패 ({e}) — 이번 실행에만 사용합니다")
    return text


def ensure_text_files(
    folder: str, force: bool = False, verbose: bool = True
) -> list[str]:
    """폴더 안의 바이너리 문서를 전부 `.txt` 로 변환. 생성/갱신된 경로 목록 반환.

    load_ordinance 가 `*.txt` 를 수집하므로, 이 함수를 먼저 부르면
    PDF 만 있어도 조례가 읽힌다. **호출부는 이 함수만 알면 된다.**
    """
    if not os.path.isdir(folder):
        return []
    made = []
    for ext in EXTRACTORS:
        for p in sorted(glob.glob(os.path.join(folder, f"*{ext}"))):
            if extract_text(p, force=force, verbose=verbose) is not None:
                made.append(cache_path_of(p))
    return made


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="문서 → 텍스트 추출")
    ap.add_argument("target", help="폴더 또는 파일 경로")
    ap.add_argument("--force", action="store_true", help="캐시 무시하고 재추출")
    a = ap.parse_args()

    if os.path.isdir(a.target):
        out = ensure_text_files(a.target, force=a.force)
        print(f"\n변환/갱신 {len(out)}건")
        for p in out:
            print(f"  {p}")
    else:
        t = extract_text(a.target, force=a.force)
        print(f"\n{'추출 완료 ' + str(len(t)) + '자' if t else '변환 대상 아님/실패'}")
