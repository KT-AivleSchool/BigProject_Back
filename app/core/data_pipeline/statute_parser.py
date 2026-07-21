# -*- coding: utf-8 -*-
"""조례 조(條) 단위 파서 — OmniSite 데이터팀
위계 헤더 주입: [조례명 > 제n장 장제목 > 제n조(제목) > 제n항]
대응: 장(章) 계층 / 제n조의m(본조 제목 병기) / 괄호 없는 조 제목 / 부칙 / 별표 / 항(①~⑮) 분할
배치 위치 제안: app/core/data_pipeline/statute_parser.py
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

CLAUSE_SPLIT_THRESHOLD = 500   # 이 글자수 초과 + 항 마커 존재 시 항 단위 분할
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"
CLAUSE_NO = {c: str(i + 1) for i, c in enumerate(CIRCLED)}

CHAPTER_RE = re.compile(r"^제(\d+)장\s+(.+?)\s*$", re.M)
ARTICLE_SPLIT_RE = re.compile(r"(?=^제\d+조(?:의\d+)?[\s(])", re.M)
ARTICLE_HEAD_RE = re.compile(r"^제(\d+조(?:의\d+)?)(?:\(([^)]+)\))?\s*")
ANNEX_RE = re.compile(r"(?=^\[?별표\s*\d*\]?)", re.M)          # [별표 1] / 별표1
ADDENDA_RE = re.compile(r"^부\s?칙", re.M)                      # 부칙
CLAUSE_RE = re.compile(f"(?=[{CIRCLED}])")


@dataclass
class StatuteChunk:
    text: str                      # 헤더 포함, 임베딩·적재 대상
    metadata: dict = field(default_factory=dict)


def _split_addenda(text: str):
    """본문과 부칙 분리. 부칙은 검색 가치 낮아 별도 처리(기본: 단일 청크)."""
    m = ADDENDA_RE.search(text)
    return (text[: m.start()], text[m.start():]) if m else (text, None)


def _chapter_of(text: str, pos: int) -> Optional[str]:
    """위치 pos 이전의 가장 가까운 장 제목."""
    last = None
    for m in CHAPTER_RE.finditer(text, 0, pos):
        last = f"제{m.group(1)}장 {m.group(2)}"
    return last


def parse_statute(
    text: str,
    doc_title: str,
    *,
    facility_type: str = "흡연부스",   # PR #100 filter 정확일치 값 — enum 합의 후 조정
    document_id: Optional[int] = None,
    district_id: Optional[int] = None,
) -> List[StatuteChunk]:
    chunks: List[StatuteChunk] = []
    body, addenda = _split_addenda(text)

    # 별표를 본문에서 분리
    annex_parts = ANNEX_RE.split(body)
    main = annex_parts[0]
    annexes = [p for p in annex_parts[1:] if p.strip()]
    if addenda:
        add_parts = ANNEX_RE.split(addenda)
        addenda = add_parts[0]
        annexes += [p for p in add_parts[1:] if p.strip()]

    article_titles: dict = {}  # "4" -> "금연구역의 지정" (조의n 본조 병기용)

    def base_meta(**kw) -> dict:
        m = {"document_title": doc_title, "facility_type": facility_type,
             "source": "official_seed"}
        if document_id is not None:
            m["document_id"] = document_id
        if district_id is not None:
            m["district_id"] = district_id
        m.update(kw)
        return m

    def emit(hdr_parts: List[str], body_text: str, meta: dict):
        hdr = "[" + " > ".join(p for p in hdr_parts if p) + "]"
        chunks.append(StatuteChunk(text=f"{hdr}\n{body_text.strip()}", metadata=meta))

    # ── 조 단위 파싱 ──
    for m_art in ARTICLE_SPLIT_RE.split(main):
        art = m_art.strip()
        h = ARTICLE_HEAD_RE.match(art)
        if not h:
            continue
        art_no, art_title = h.group(1), h.group(2)  # 괄호 없는 제목이면 art_title=None
        body_text = art[h.end():].strip()
        if not body_text:
            continue
        if art_title and "조의" not in art_no:
            article_titles[art_no.replace("조", "")] = art_title

        pos = main.find(art[:20])
        chapter = _chapter_of(main, pos if pos >= 0 else 0)

        art_label = f"제{art_no}" + (f"({art_title})" if art_title else "")
        # 조의n → 본조 제목 병기
        if "조의" in art_no:
            base_no = art_no.split("조의")[0]
            if base_no in article_titles:
                art_label += f" — 본조: 제{base_no}조({article_titles[base_no]})"

        parts = CLAUSE_RE.split(body_text)
        if len(body_text) > CLAUSE_SPLIT_THRESHOLD and len(parts) > 1:
            pieces = [(CLAUSE_NO.get(p[0]), p.strip()) for p in parts if p.strip()]
        else:
            pieces = [(None, body_text)]

        for clause_no, piece in pieces:
            hdr = [doc_title, chapter, art_label]
            if clause_no:
                hdr.append(f"제{clause_no}항")
            emit(hdr, piece, base_meta(article_no=art_no, clause_no=clause_no,
                                       chapter=chapter, article_title=art_title))

    # ── 별표 ──
    for i, annex in enumerate(annexes, 1):
        first = annex.strip().splitlines()[0][:20]
        no_m = re.search(r"별표\s*(\d+)", first)
        label = f"별표{no_m.group(1)}" if no_m else f"별표{i}"
        emit([doc_title, label], annex, base_meta(article_no=label, clause_no=None))

    # ── 부칙 (단일 청크) ──
    if addenda and addenda.strip():
        emit([doc_title, "부칙"], addenda, base_meta(article_no="부칙", clause_no=None))

    return chunks


def length_report(chunks: List[StatuteChunk]) -> str:
    """조별 길이 분포 — 'splitter(1000자) 미발동' 가정의 실측 검증용."""
    lens = sorted(len(c.text) for c in chunks)
    over = [l for l in lens if l > 1000]
    lines = [
        f"청크 {len(lens)}개 | 최소 {lens[0]} / 중앙값 {lens[len(lens)//2]} / 최대 {lens[-1]}자",
        f"1000자 초과: {len(over)}개 {over if over else ''}"
        + (" → splitter 재분할 대상 있음, 해당 조 확인 필요" if over else " → splitter 미발동 확인"),
    ]
    return "\n".join(lines)
