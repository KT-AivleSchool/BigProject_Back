# -*- coding: utf-8 -*-
"""조례 조(條) 단위 파서 v3 — OmniSite 데이터팀
레포 배치 시 파일명은 statute_parser.py 유지 (ingest가 이 이름으로 import).

v1 → v2 (A등급 고도화, 실원문 제1081호 검증 반영):
  A1. 조 위치 탐색을 find() → finditer 오프셋 순회로 교체 (동일 앞머리 조문 오판 원천 제거)
  A2. 개정 태그 <신설/개정 yyyy.mm.dd> strip + 메타 `amended` 보존 (임베딩 날짜 오염 방지)
  A3. 삭제 조항("제n조 삭제") 스킵
  A4. 문서 메타 자동 추출: extract_doc_meta() — [시행 …] [… 제n호 …] → 시행일·조례번호
  A5. 페이지 노이즈 제거 (법제처/국가법령정보센터 꼬리, 반복되는 조례명 줄)
  A7. 입지 무관 조항(벌칙/과태료/보칙) 적재 제외 — 넓은 법 통짜 적재 시 검색 오염 방지
       (유사도로는 관련/무관 점수대가 겹쳐 분리 불가 → 적재 단계에서 제거)
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

CLAUSE_SPLIT_THRESHOLD = 500
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"
CLAUSE_NO = {c: str(i + 1) for i, c in enumerate(CIRCLED)}

# A6: 줄머리 선행 공백 허용. PDF 조판에 따라 조문이 들여쓰기된 문서가 있고,
#     `^제\d+조`로만 잡으면 그런 문서는 매치 0건 → 청크 0개가 된다
#     (실제 사례: 서울시교육청 교육환경평가서 규칙 — 조문 12개 전부 앞에 공백 1칸).
#     부칙(^부 칙)도 같은 이유로 놓쳐, 부칙 조문이 본문 조문으로 섞여 들어갔다.
CHAPTER_RE = re.compile(r"^[ \t]*제(\d+)장\s+(.+?)\s*$", re.M)
ARTICLE_START_RE = re.compile(r"^[ \t]*제\d+조(?:의\d+)?[\s(]", re.M)  # A1: finditer용
ARTICLE_HEAD_RE = re.compile(r"^제(\d+조(?:의\d+)?)(?:\(([^)]+)\))?\s*")
ANNEX_RE = re.compile(r"(?=^[ \t]*\[?별표\s*\d*\]?)", re.M)
ADDENDA_RE = re.compile(r"^[ \t]*부\s?칙", re.M)
CLAUSE_RE = re.compile(f"(?=[{CIRCLED}])")
AMEND_TAG_RE = re.compile(r"\s*<(신설|개정|전문개정|일부개정)[^>]*>")  # A2
DELETED_RE = re.compile(r"^삭제\s*(?:<[^>]*>)?\s*$")  # A3
DOC_META_RE = re.compile(
    r"\[시행\s*([\d\.\s]+?)\.?\]\s*\[[^\]]*?제(\d+)호[^\]]*?\]"
)  # A4
NOISE_LINE_RES = [  # A5
    re.compile(r"^법제처\s*\d*\s*(국가법령정보센터)?\s*$"),
    re.compile(r"^국가법령정보센터\s*$"),
    re.compile(r"^\d+\s*$"),  # 페이지 번호 단독 줄
]

# A7: 입지 판단과 무관한 처벌·행정 조항 키워드. 장(章)·조(條) 제목에 걸리면 청크에서 제외.
#     범위 넓은 법(예: 국민건강증진법)을 통짜 적재하면 담배판매·광고 벌칙 등 흡연부스 입지와
#     무관한 조문이 검색 상위를 오염시킨다. 유사도로는 못 거른다(관련 0.36 / 무관 0.44 점수대
#     중첩 → 임계치를 올리면 관련 조문이 먼저 죽음) → 적재 단계 제거가 유일한 근본 해법.
EXCLUDE_CHAPTER_KEYS = ("벌칙", "보칙")  # 장(章) 제목에 포함되면 그 장의 조문 전체 제외
EXCLUDE_ARTICLE_KEYS = (
    "벌칙",
    "과태료",
    "양벌규정",
)  # 조(條) 제목에 포함되면 해당 조 제외


@dataclass
class StatuteChunk:
    text: str
    metadata: dict = field(default_factory=dict)


# ── A4: 문서 메타 추출 ───────────────────────────────────────────────────────
def extract_doc_meta(text: str) -> dict:
    """첫 부분의 [시행 2014. 12. 26.] [조례 제1081호, …] → 시행일·조례번호.
    ordinance_documents INSERT 재료 + 현행본 검증용."""
    meta = {}
    m = DOC_META_RE.search(text[:800])
    if m:
        date_digits = re.findall(r"\d+", m.group(1))
        if len(date_digits) >= 3:
            y, mo, d = date_digits[:3]
            meta["enforcement_date"] = f"{y}-{int(mo):02d}-{int(d):02d}"
        meta["doc_no"] = f"제{m.group(2)}호"
    # 노이즈 줄(법제처 꼬리 등)을 건너뛰고 첫 유효 줄에서 제목 탐지
    first = ""
    for line in text.splitlines():
        s = line.strip()
        if not s or any(rx.match(s) for rx in NOISE_LINE_RES):
            continue
        first = s
        break
    if (
        "조례" in first or "법률" in first or first.endswith("법")
    ) and "법제처" not in first:
        meta["doc_title_detected"] = first
    return meta


# ── A5: 노이즈 제거 ─────────────────────────────────────────────────────────
def _strip_noise(text: str, doc_title: str) -> str:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if any(rx.match(s) for rx in NOISE_LINE_RES):
            continue
        if s == doc_title:  # 페이지마다 반복되는 조례명 헤더
            continue
        out.append(line)
    return "\n".join(out)


def _split_addenda(text: str):
    m = ADDENDA_RE.search(text)
    return (text[: m.start()], text[m.start() :]) if m else (text, None)


def _chapter_of(text: str, pos: int) -> Optional[str]:
    last = None
    for m in CHAPTER_RE.finditer(text, 0, pos):
        last = f"제{m.group(1)}장 {m.group(2)}"
    return last


# ── A2: 개정 태그 분리 ──────────────────────────────────────────────────────
def _extract_amend_tags(text: str):
    tags = [m.group(0).strip().strip("<>").strip() for m in AMEND_TAG_RE.finditer(text)]
    tags = list(dict.fromkeys(tags))  # 중복 제거(순서 보존)
    cleaned = AMEND_TAG_RE.sub("", text)
    return cleaned, ("; ".join(tags) or None)


# ── A7: 입지 무관 조항 제외 ──────────────────────────────────────────────────
def _is_offtopic_article(chapter: Optional[str], art_title: Optional[str]) -> bool:
    """벌칙/과태료/보칙 등 입지 판단과 무관한 조항이면 True → 적재에서 제외."""
    if chapter and any(k in chapter for k in EXCLUDE_CHAPTER_KEYS):
        return True
    if art_title and any(k in art_title for k in EXCLUDE_ARTICLE_KEYS):
        return True
    return False


def parse_statute(
    text: str,
    doc_title: str,
    *,
    facility_type: str = "흡연부스",
    document_id: Optional[int] = None,
    district_id: Optional[int] = None,
    doc_meta: Optional[dict] = None,
) -> List[StatuteChunk]:
    """doc_meta: extract_doc_meta() 결과(enforcement_date·doc_no)를 넘기면
    전 청크 메타데이터에 병합된다. 조례 개정 시 '어느 판(版)의 조문인지' 식별용."""
    chunks: List[StatuteChunk] = []
    text = _strip_noise(text, doc_title)  # A5
    body, addenda = _split_addenda(text)

    annex_parts = ANNEX_RE.split(body)
    main = annex_parts[0]
    annexes = [p for p in annex_parts[1:] if p.strip()]
    if addenda:
        add_parts = ANNEX_RE.split(addenda)
        addenda = add_parts[0]
        annexes += [p for p in add_parts[1:] if p.strip()]

    article_titles: dict = {}

    def base_meta(**kw) -> dict:
        m = {
            "document_title": doc_title,
            "facility_type": facility_type,
            "source": "official_seed",
        }
        # 시행일·조례번호 등 문서 단위 메타 (enforcement_date, doc_no)
        for k, v in (doc_meta or {}).items():
            if k != "doc_title_detected" and v is not None:
                m[k] = v
        if document_id is not None:
            m["document_id"] = document_id
        if district_id is not None:
            m["district_id"] = district_id
        m.update(kw)
        return m

    def emit(hdr_parts: List[str], body_text: str, meta: dict):
        hdr = "[" + " > ".join(p for p in hdr_parts if p) + "]"
        chunks.append(StatuteChunk(text=f"{hdr}\n{body_text.strip()}", metadata=meta))

    # ── A1: finditer 오프셋 순회 — 위치를 직접 들고 돌아 재탐색·오판 제거 ──
    starts = list(ARTICLE_START_RE.finditer(main))
    for i, sm in enumerate(starts):
        start = sm.start()
        end = starts[i + 1].start() if i + 1 < len(starts) else len(main)
        art = main[start:end].strip()

        h = ARTICLE_HEAD_RE.match(art)
        if not h:
            continue
        art_no, art_title = h.group(1), h.group(2)
        body_text = art[h.end() :].strip()

        if DELETED_RE.match(body_text):  # A3
            continue
        if not body_text:
            continue

        body_text, amended = _extract_amend_tags(body_text)  # A2
        if art_title and "조의" not in art_no:
            article_titles[art_no.replace("조", "")] = art_title

        chapter = _chapter_of(main, start)  # A1: 오프셋 직접 사용

        if _is_offtopic_article(chapter, art_title):  # A7: 벌칙/과태료/보칙 제외
            continue

        art_label = f"제{art_no}" + (f"({art_title})" if art_title else "")
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
            meta = base_meta(
                article_no=art_no,
                clause_no=clause_no,
                chapter=chapter,
                article_title=art_title,
            )
            if amended:
                meta["amended"] = amended
            emit(hdr, piece, meta)

    for i, annex in enumerate(annexes, 1):
        first = annex.strip().splitlines()[0][:20]
        no_m = re.search(r"별표\s*(\d+)", first)
        label = f"별표{no_m.group(1)}" if no_m else f"별표{i}"
        emit([doc_title, label], annex, base_meta(article_no=label, clause_no=None))

    if addenda and addenda.strip():
        add_clean, add_amended = _extract_amend_tags(addenda)
        meta = base_meta(article_no="부칙", clause_no=None)
        if add_amended:
            meta["amended"] = add_amended
        emit([doc_title, "부칙"], add_clean, meta)

    return chunks


def length_report(chunks: List[StatuteChunk]) -> str:
    if not chunks:
        return (
            "⚠️ 청크 0개 — 조문을 하나도 못 찾았습니다. 조판(들여쓰기·단 구성) 확인 필요"
        )
    lens = sorted(len(c.text) for c in chunks)
    over = [n for n in lens if n > 1000]
    lines = [
        f"청크 {len(lens)}개 | 최소 {lens[0]} / 중앙값 {lens[len(lens) // 2]} / 최대 {lens[-1]}자",
        f"1000자 초과: {len(over)}개 {over if over else ''}"
        + (
            " → splitter 재분할 대상 있음, 해당 조 확인 필요"
            if over
            else " → splitter 미발동 확인"
        ),
    ]
    return "\n".join(lines)
