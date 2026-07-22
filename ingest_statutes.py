# -*- coding: utf-8 -*-
"""시드 조례 일괄 적재 — OmniSite 데이터팀
사용: 레포 루트에서 (statute_parser.py는 루트 또는 app/core/data_pipeline/에 배치)
  python ingest_statutes.py             # seeds/ 폴더의 *.txt, *.pdf 전체: 클린 → 파싱 → 적재 → 검증 질의
  python ingest_statutes.py --dry-run   # 적재 없이 파싱·길이 리포트만 (원문 견고성 점검용)
  python ingest_statutes.py --no-clean  # 클린 생략 (권장 안 함 — 재적재 규율)
seeds/ 파일명 규칙: `조례명.txt` 또는 `조례명.pdf` — 첫 줄(또는 파일명)이 조례명.

[버그수정 이력]
  기존 storage.statutes_store.delete_collection()은 콜렉션 "행 자체"까지
  langchain_pg_collection에서 지워버려서, 재적재 시 "Collection not found"로
  전량 실패하는 문제가 있었음(재현 확인됨: clean 모드 2회 연속 재현).
  → _safe_clean_collection()으로 교체: 콜렉션 레코드는 유지하고
    langchain_pg_embedding 안의 데이터(임베딩 행)만 지움.
"""

import asyncio
import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.data_pipeline.statute_parser import (  # noqa: E402
    parse_statute,
    length_report,
    extract_doc_meta,
)
from app.core.sim_ai.vector_db import RagVectorStorage  # noqa: E402
from app.core.sim_ai.document_loader import StatuteDocumentLoader  # noqa: E402

SEED_DIR = Path("seeds")
STATUTES_COLLECTION_NAME = "statutes_collection"

# ⚠️ 적재 태그 = 검색 필터 값. StreamRequest.facility_type과 '정확히' 일치해야 한다.
#    (simulations.py가 filter={"facility_type": ...} 정확일치로 검색 → 한 글자만 달라도 전량 0건)
#
#    "흡연부스"는 전 브랜치 조사로 확인된 값이다 (test_api_client.py, run_ai_console.py).
#    혼동 주의 — 같은 이름의 다른 필드가 둘 더 있으며, 아래 값을 여기 쓰면 안 된다:
#      · audit_data.results[].roles[].facility_type → 배제 근거가 되는 '주변' 시설
#        ("학교", "버스정류소", "지하철역", "어린이집", "어린이보호구역")
#      · smoking_area_polygons 테이블 컬럼 → CSV '시설종류' 매핑값 ("스마트흡연부스")
SEED_FACILITY_TYPE = "흡연부스"

# 시드 문서별 facility_type 매핑. 키가 '파일명 또는 조례명'에 부분일치하면 그 값으로 태깅한다.
#   위에서부터 먼저 맞는 것을 쓰므로, 좁은 조건을 위에 둘 것.
#   미매칭 시 SEED_FACILITY_TYPE 기본값.
# ⚠️ 값은 StreamRequest.facility_type과 정확일치해야 검색 필터가 걸린다(신규 추가 시 프론트 값 확인).
SEED_FACILITY_MAP = {
    "금연": "흡연부스",
    "간접흡연": "흡연부스",
    "국민건강증진": "흡연부스",
    "교육환경": "흡연부스",  # 학교보호구역 — 흡연부스 이격거리 근거
    "전기차": "전기차충전소",
    "친환경자동차": "전기차충전소",
    "환경친화적 자동차": "전기차충전소",  # 법령 정식 표기 (서울시 조례 제9659호)
    "충전시설": "전기차충전소",
}

TEST_QUERIES = [
    "버스정류소 근처에 흡연부스를 설치해도 되나?",
    "학교 주변 금연 관련 규정",
    "전기차 충전시설 설치 의무",  # 대조군(EV 조례) 검증용
]

_loader = StatuteDocumentLoader()


def _load_txt(p: Path) -> tuple:
    text = p.read_text(encoding="utf-8")
    first = text.strip().splitlines()[0].strip()
    title = first if ("조례" in first or "법" in first) else p.stem
    return title, text, p.name


def _load_pdf(p: Path) -> tuple:
    file_bytes = p.read_bytes()
    text = _loader.extract_text_from_pdf(file_bytes)

    # law.go.kr PDF는 "법제처 ... 국가법령정보센터" 머리글이 매 페이지 반복됨 → 그 줄은 건너뛰고 조례명 찾기
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    title = p.stem
    for ln in lines[:5]:
        if "법제처" in ln or "국가법령정보센터" in ln:
            continue
        # 조문 첫 줄을 제목으로 오인하지 않도록 제외 (본문에 「…법률」 인용이 있어 '법'에 걸림)
        if re.match(r"제\d+조", ln):
            continue
        if "조례" in ln or "규칙" in ln or "법" in ln:
            title = ln
            break

    return title, text, p.name


def resolve_facility_type(filename: str, title: str) -> tuple:
    """(facility_type, 매칭된 키 또는 None) 반환.
    파일명·조례명에 SEED_FACILITY_MAP 키가 부분일치하면 그 값, 없으면 기본값.
    seeds/에 여러 시설의 조례를 섞어 넣어도 문서별로 다르게 태깅된다
    (전 문서를 한 값으로 태깅하면 facility_type 필터가 아무것도 구분하지 못함)."""
    haystack = f"{filename} {title}"
    for key, value in SEED_FACILITY_MAP.items():
        if key in haystack:
            return value, key
    return SEED_FACILITY_TYPE, None


def load_seeds():
    if not SEED_DIR.is_dir():
        sys.exit(
            "seeds/ 폴더가 없음 — law.go.kr 원문(txt 또는 pdf)을 seeds/에 넣고 재실행"
        )

    docs = []
    for p in sorted(SEED_DIR.glob("*.txt")):
        docs.append(_load_txt(p))
    for p in sorted(SEED_DIR.glob("*.pdf")):
        docs.append(_load_pdf(p))

    if not docs:
        sys.exit("seeds/에 txt 또는 pdf 파일이 없음")
    return docs


def _safe_clean_collection(storage: RagVectorStorage, collection_name: str) -> None:
    """
    콜렉션 '행'은 유지하고, 그 안의 임베딩 데이터만 지운다.
    (storage.statutes_store.delete_collection()은 콜렉션 행 자체를 지워버려서
     재적재 시 "Collection not found"가 나는 버그가 있어 사용하지 않음)
    콜렉션이 아직 한 번도 안 만들어진 상태(최초 실행)면 조용히 넘어간다
    — 이후 add_statute_chunks()가 처음 호출될 때 자동 생성됨.
    """
    from sqlalchemy import create_engine, text as sql_text

    engine = create_engine(storage.connection_string)
    with engine.begin() as conn:
        row = conn.execute(
            sql_text("SELECT uuid FROM langchain_pg_collection WHERE name = :name"),
            {"name": collection_name},
        ).fetchone()

        if row is None:
            print(
                f"클린 스킵 — '{collection_name}' 콜렉션이 아직 없음(최초 실행으로 판단)"
            )
            return

        collection_uuid = row[0]
        result = conn.execute(
            sql_text("DELETE FROM langchain_pg_embedding WHERE collection_id = :cid"),
            {"cid": collection_uuid},
        )
        print(
            f"클린 완료 — '{collection_name}' 콜렉션 유지, 임베딩 {result.rowcount}건 삭제"
        )

    engine.dispose()


async def main():
    dry = "--dry-run" in sys.argv
    docs = load_seeds()

    all_chunks = []
    for title, text, filename in docs:
        # A4: 시행일·조례번호를 원문에서 추출해 전 청크 메타에 실어보낸다.
        #     (조례 개정 시 '어느 판의 조문인지' 식별 — 발제 B-4 문서 단위 관리의 최소 요건)
        doc_meta = extract_doc_meta(text)
        # 문서별 facility_type — 전 문서 동일값으로 태깅하면 필터가 무의미해진다.
        facility_type, matched_key = resolve_facility_type(filename, title)
        chunks = parse_statute(
            text, title, facility_type=facility_type, doc_meta=doc_meta
        )
        origin = f"'{matched_key}' 매칭" if matched_key else "미매칭 → 기본값"
        print(f"\n== {title}: {len(chunks)}청크")
        print(f"   facility_type: '{facility_type}' ({origin})")
        print(f"   문서메타: {doc_meta or '(추출 실패 — 원문 머리말 형식 확인 필요)'}")
        print(length_report(chunks))
        for c in chunks[:2]:
            print("  예시:", c.text.splitlines()[0])
        all_chunks += chunks

    tagged = sorted({c.metadata["facility_type"] for c in all_chunks})
    print(f"\n총 {len(all_chunks)}청크 | 태깅된 facility_type: {tagged}")
    if len(tagged) == 1 and len(docs) > 1:
        print(
            "   ℹ️ 전 문서가 한 값으로 태깅됨 — 대조군 검증을 하려면 "
            "SEED_FACILITY_MAP에 다른 시설 조례를 추가하세요."
        )
    if dry:
        print("(dry-run — 적재 생략)")
        return

    storage = RagVectorStorage()

    if "--no-clean" not in sys.argv:
        try:
            _safe_clean_collection(storage, STATUTES_COLLECTION_NAME)
        except Exception as e:
            print(f"클린 실패(콜렉션 유지한 채 진행): {e}")

    texts = [c.text for c in all_chunks]
    metas = [c.metadata for c in all_chunks]
    # PR #100 머지 전(metadatas 미지원)과 후를 모두 지원하는 겸용 호출
    sig = inspect.signature(storage.add_statute_chunks)
    if "metadatas" in sig.parameters:
        await storage.add_statute_chunks(texts, metadatas=metas)
        print("적재 완료 (metadatas 포함 — PR #100 시그니처)")
    else:
        await storage.add_statute_chunks(texts)
        print("적재 완료 (구 시그니처 — 메타 없이)")

    # 검증은 반드시 '필터 없이'와 '필터 걸고' 둘 다 돌린다.
    #   실제 서비스(simulations.py)는 항상 facility_type 필터를 걸기 때문에,
    #   필터 없이만 검증하면 태그 값이 틀려도 초록불이 뜨고 서비스에서만 0건이 난다.
    print("\n== 검증 질의 (필터 없음 — 적재·임베딩 자체 확인) ==")
    for q in TEST_QUERIES:
        res = await storage.retrieve_similar_statutes(q, top_k=3)
        top1 = res[0].replace("\n", " ")[:90] if res else "(없음)"
        print(f"[{q}]\n  → {top1}...")

    # 태깅된 facility_type 전부에 대해 필터 검증 — 값이 여럿이면 대조군 효과도 여기서 드러난다.
    empty = 0
    total = 0
    for ft in tagged:
        print(f"\n== 검증 질의 (filter facility_type='{ft}' — 서비스 경로 재현) ==")
        for q in TEST_QUERIES:
            res = await storage.retrieve_similar_statutes(q, top_k=3, facility_type=ft)
            total += 1
            if not res:
                empty += 1
            top1 = res[0].replace("\n", " ")[:90] if res else "(없음)"
            print(f"[{q}]\n  → {top1}...")

    if total and empty == total:
        print(
            f"\n⚠️ 필터 적용 시 전 질의 0건 — 적재 태그({tagged})와 검색 필터가 "
            f"맞물리지 않았습니다. 다음을 확인하세요:\n"
            f"   1) vector_db.py가 filter= 를 쓰는 신버전인지 "
            f"(구버전은 쿼리 prefix라 메타 없이도 결과가 나옴 → 이 경고가 안 뜸)\n"
            f"   2) 적재가 metadatas 포함으로 됐는지 (위 '적재 완료' 메시지 확인)\n"
            f"   3) 프론트가 보내는 StreamRequest.facility_type 값과 일치하는지"
        )


if __name__ == "__main__":
    asyncio.run(main())
