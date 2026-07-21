# -*- coding: utf-8 -*-
"""시드 조례 일괄 적재 — OmniSite 데이터팀
사용: 레포 루트에서 (statute_parser.py는 루트 또는 app/core/data_pipeline/에 배치)
  python ingest_statutes.py             # seeds/ 폴더의 *.txt, *.pdf 전체: 클린 → 파싱 → 적재 → 검증 질의
  python ingest_statutes.py --dry-run   # 적재 없이 파싱·길이 리포트만 (원문 견고성 점검용)
  python ingest_statutes.py --no-clean  # 클린 생략 (권장 안 함 — 재적재 규율)
seeds/ 파일명 규칙: `조례명.txt` 또는 `조례명.pdf` — 첫 줄(또는 파일명)이 조례명.
"""
import asyncio
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.data_pipeline.statute_parser import parse_statute, length_report  # noqa: E402  # noqa: E402
from app.core.sim_ai.vector_db import RagVectorStorage   # noqa: E402
from app.core.sim_ai.document_loader import StatuteDocumentLoader  # noqa: E402

SEED_DIR = Path("seeds")
TEST_QUERIES = [
    "버스정류소 근처에 흡연부스를 설치해도 되나?",
    "학교 주변 금연 관련 규정",
    "전기차 충전시설 설치 의무",   # 대조군(EV 조례) 검증용
]

_loader = StatuteDocumentLoader()


def _load_txt(p: Path) -> tuple:
    text = p.read_text(encoding="utf-8")
    first = text.strip().splitlines()[0].strip()
    title = first if ("조례" in first or "법" in first) else p.stem
    return title, text


def _load_pdf(p: Path) -> tuple:
    file_bytes = p.read_bytes()
    text = _loader.extract_text_from_pdf(file_bytes)

    # law.go.kr PDF는 "법제처 ... 국가법령정보센터" 머리글이 매 페이지 반복됨 → 그 줄은 건너뛰고 조례명 찾기
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    title = p.stem
    for ln in lines[:5]:  # 앞쪽 몇 줄만 훑어서 조례명 후보 찾기
        if "법제처" in ln or "국가법령정보센터" in ln:
            continue
        if "조례" in ln or "법" in ln:
            title = ln
            break

    return title, text


def load_seeds():
    if not SEED_DIR.is_dir():
        sys.exit("seeds/ 폴더가 없음 — law.go.kr 원문(txt 또는 pdf)을 seeds/에 넣고 재실행")

    docs = []
    for p in sorted(SEED_DIR.glob("*.txt")):
        docs.append(_load_txt(p))
    for p in sorted(SEED_DIR.glob("*.pdf")):
        docs.append(_load_pdf(p))

    if not docs:
        sys.exit("seeds/에 txt 또는 pdf 파일이 없음")
    return docs


async def main():
    dry = "--dry-run" in sys.argv
    docs = load_seeds()

    all_chunks = []
    for title, text in docs:
        chunks = parse_statute(text, title)  # facility_type 기본 '흡연부스' — enum 확정 시 조정
        print(f"\n== {title}: {len(chunks)}청크")
        print(length_report(chunks))
        for c in chunks[:2]:
            print("  예시:", c.text.splitlines()[0])
        all_chunks += chunks

    print(f"\n총 {len(all_chunks)}청크")
    if dry:
        print("(dry-run — 적재 생략)")
        return

    storage = RagVectorStorage()

    if "--no-clean" not in sys.argv:
        try:
            storage.statutes_store.delete_collection()
            print("클린 완료 (재적재 규율: 기존 콜렉션 삭제 후 전량 적재)")
        except Exception as e:
            print(f"클린 스킵(콜렉션 없음 등): {e}")

    texts = [c.text for c in all_chunks]
    metas = [c.metadata for c in all_chunks]
    sig = inspect.signature(storage.add_statute_chunks)
    if "metadatas" in sig.parameters:
        await storage.add_statute_chunks(texts, metadatas=metas)
        print("적재 완료 (metadatas 포함)")
    else:
        await storage.add_statute_chunks(texts)
        print("적재 완료 (구 시그니처 — 메타 없이)")

    print("\n== 검증 질의 ==")
    for q in TEST_QUERIES:
        res = await storage.retrieve_similar_statutes(q, top_k=3)
        top1 = res[0].replace("\n", " ")[:90] if res else "(없음)"
        print(f"[{q}]\n  → {top1}...")


if __name__ == "__main__":
    asyncio.run(main())