# -*- coding: utf-8 -*-
"""
PoC: 조례 조(條) 단위 파싱 + 위계 헤더 주입 → 동현님 RagVectorStorage로 적재·검색 E2E
사용법 (레포 루트 C:/Users/User/Projects/BigProject_Back 에 저장 후):
  python poc_statute_ingest.py                # 내장 예시 조례로 실행
  python poc_statute_ingest.py statute.txt    # law.go.kr에서 복사한 실제 원문 파일로 실행
  python poc_statute_ingest.py --clean        # PoC 적재분(statutes_collection) 전체 삭제
주의: PoC 적재는 현재 기본 임베딩 모델로 들어감 → 3-small 전환 확정 시 --clean 후 재적재 1회 필요
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.sim_ai.vector_db import RagVectorStorage  # noqa: E402

# ── 설정 ──────────────────────────────────────────────────────────────────────
DOC_TITLE = "서울특별시 용산구 금연구역 지정 및 간접흡연피해방지 조례"
CLAUSE_SPLIT_THRESHOLD = 500  # 이 글자수 넘고 항 마커 있으면 항 단위 분할

TEST_QUERIES = [
    "버스정류소 근처에 흡연부스를 설치해도 되나?",
    "금연구역에서 흡연하면 과태료가 얼마인가?",
    "금연구역 표지판은 누가 설치하나?",
]

# 내장 예시 (구조 시연용 — 실측정은 law.go.kr 원문 파일 권장)
SAMPLE_TEXT = """제1조(목적) 이 조례는 「국민건강증진법」에 따라 금연구역 지정과 간접흡연 피해 방지에 필요한 사항을 규정함을 목적으로 한다.
제4조(금연구역의 지정) ① 구청장은 다수인이 모이거나 오고가는 다음 각 호의 장소를 금연구역으로 지정할 수 있다. 1. 가로변 버스정류소 주변 10미터 이내 2. 도시공원 및 어린이놀이터 3. 학교 출입문으로부터 50미터 이내의 통학로 ② 구청장은 제1항에 따라 금연구역을 지정한 때에는 해당 구역에 금연구역임을 알리는 표지판을 설치하여야 한다.
제10조(과태료) ① 제4조에 따라 지정된 금연구역에서 흡연을 한 사람에게는 10만원 이하의 과태료를 부과한다.
"""

ARTICLE_RE = re.compile(r"(?=제\d+조(?:의\d+)?\()")
ARTICLE_HEAD_RE = re.compile(r"^제(\d+조(?:의\d+)?)\(([^)]+)\)\s*")
CLAUSE_RE = re.compile(r"(?=[①②③④⑤⑥⑦⑧⑨⑩])")
CLAUSE_NO = {c: str(i + 1) for i, c in enumerate("①②③④⑤⑥⑦⑧⑨⑩")}


def parse_statute(text: str, doc_title: str):
    """조 단위 분리 → 길고 항 마커 있으면 항 분할 → 위계 헤더 주입. [(chunk_text, meta)] 반환"""
    chunks = []
    for art in filter(None, (a.strip() for a in ARTICLE_RE.split(text))):
        m = ARTICLE_HEAD_RE.match(art)
        if not m:
            continue
        art_no, art_title = m.group(1), m.group(2)
        body = art[m.end() :].strip()
        base_hdr = f"[{doc_title} > 제{art_no}({art_title})"
        parts = CLAUSE_RE.split(body)
        if len(body) > CLAUSE_SPLIT_THRESHOLD and len(parts) > 1:
            pieces = [(CLAUSE_NO.get(p[0]), p.strip()) for p in parts if p.strip()]
        else:
            pieces = [(None, body)]
        for clause_no, piece in pieces:
            hdr = base_hdr + (f" > 제{clause_no}항]" if clause_no else "]")
            chunks.append(
                (
                    f"{hdr}\n{piece}",
                    {
                        "document_title": doc_title,
                        "article_no": art_no,
                        "clause_no": clause_no,
                        "source": "poc_seed",
                    },
                )
            )
    return chunks


async def run_clean(storage: RagVectorStorage):
    storage.statutes_store.delete_collection()
    print("statutes_collection 삭제 완료 (PoC 적재분 정리됨)")


async def main():
    if "--clean" in sys.argv:
        await run_clean(RagVectorStorage())
        return

    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        text, src = open(sys.argv[1], encoding="utf-8").read(), sys.argv[1]
    else:
        text, src = SAMPLE_TEXT, "(내장 예시)"

    chunks = parse_statute(text, DOC_TITLE)
    print(f"원문: {src} → 조 단위 청크 {len(chunks)}개")
    for c, m in chunks:
        print(
            f"  - 제{m['article_no']}"
            + (f" 제{m['clause_no']}항" if m["clause_no"] else "")
            + f" ({len(c)}자)"
        )

    storage = RagVectorStorage()

    # 적재 — 동현님 함수 그대로 호출 (현 시그니처가 metadatas 미수용이라 텍스트만 전달.
    #        업그레이드 4번 승인 시 아래 한 줄을 metadatas 포함 호출로 교체)
    await storage.add_statute_chunks([c for c, _ in chunks])

    print("\n=== PoC 결과 (발제문 기입용) ===")
    print(f"적재 청크 수: {len(chunks)}")
    for q in TEST_QUERIES:
        results = await storage.retrieve_similar_statutes(q, top_k=3)
        top1 = results[0].replace("\n", " ")[:90] if results else "(결과 없음)"
        print(f"[질의] {q}\n  → top1: {top1}...")
    print("\n검증 SQL(DBeaver): SELECT count(*) FROM langchain_pg_embedding;")


if __name__ == "__main__":
    asyncio.run(main())
