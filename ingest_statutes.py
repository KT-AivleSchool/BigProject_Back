# -*- coding: utf-8 -*-
"""시드 조례 일괄 적재 - OmniSite 데이터팀

사용: 레포 루트에서 — **`--seeds` 로 도메인 폴더를 지목한다**(아래 🔴 두 번째 항목)
  python ingest_statutes.py --domain 흡연  --seeds seeds/흡연    # 클린 → 파싱 → 적재 → 검증
  python ingest_statutes.py --domain 흡연  --seeds seeds/흡연 --dry-run   # 파싱·길이 리포트만
  python ingest_statutes.py --domain 흡연  --seeds seeds/흡연 --no-clean  # 권장 안 함
  python ingest_statutes.py --domain 전기차 --seeds seeds/전기차
파일명 규칙: `조례명.txt` 또는 `조례명.pdf` - 첫 줄(또는 파일명)이 조례명.

🔴 `--domain` 은 **필수**다(2026-08-24). 조례 벡터 콜렉션이 도메인마다 따로이고
   (`statutes_<도메인>`) 이 스크립트가 그중 어느 칸에 부을지를 정하는 유일한 값이다.
   기본값을 두면 빠뜨렸을 때 남의 도메인 토론에 이 조문이 인용된다 — 안 터지고
   근거만 틀린다(원칙 1·2).

🔴 **폴더 하나가 곧 도메인 하나다.** `--seeds` 로 준 폴더의 파일이 전부 그 도메인
   콜렉션으로 들어간다. 이 규칙은 새로 만든 게 아니라 조례 **파일** 쪽이 처음부터
   쓰던 것이다 — `datasets/<도메인>/law/` 폴더가 곧 격리다.
   2026-08-24 에 `seeds/` 를 그 모양으로 나눴다: `seeds/흡연/`(4) · `seeds/전기차/`(1) ·
   `seeds/_미분류/`(1). **`seeds/` 자신을 주면 0건으로 멈춘다** — 하위 폴더를 지목할 것.
   ⚠ 나누기 전 이 자리엔 「흡연 5 + 전기차 1」이라 적혀 있었는데 **실측하면 틀렸다**
     (흡연 4 + 전기차 1 + **어느 쪽도 아닌 것 1**). 개수만 세어 적어둔 값은 상한다.
   ⚠ `_미분류/` 는 판정을 **못 한** 것이지 「없는 것」이 아니다(사유는 그 폴더의
     `왜_여기_있나.md`). 추측해서 도메인에 넣으면 무관한 조문이 그 토론에 인용된다.

⚠ `--facility-type` 은 청크 메타데이터에 적히는 **부가정보**일 뿐 격리 키가 아니다.
  예전엔 콜렉션이 `statutes_collection` 하나뿐이라 이 태그가 격리를 대신했고,
  검색이 이 값과 정확일치로 걸러서 한 글자만 달라도 전량 0건이었다. 그래서 이
  파일에 파일명→시설명 사전(`SEED_FACILITY_MAP`)이 있었는데, 그런 사전은 다음
  도메인에서 반드시 틀린다(CLAUDE.md 「사전에 없는 도메인은 조용히 None 이 된다」).
  콜렉션을 나눈 지금은 격리가 **구조**라 사전이 필요 없어 지웠다 — 안 주면 비워 둔다.

[버그수정 이력]
  기존 storage.statutes_store.delete_collection()은 콜렉션 "행 자체"까지
  langchain_pg_collection에서 지워버려서, 재적재 시 "Collection not found"로
  전량 실패하는 문제가 있었음(재현 확인됨: clean 모드 2회 연속 재현).
  → 여기 있던 _safe_clean_collection()은 2026-08-24에 지웠다. 정본
    `RagVectorStorage.delete_statute_chunks(domain)` 이 같은 일을 한다
    (콜렉션 행 유지, 임베딩만 삭제). 같은 SQL 을 두 벌 두면 언젠가 한쪽만 바뀐다.
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.data_pipeline.statute_parser import (  # noqa: E402
    parse_statute,
    length_report,
    extract_doc_meta,
)
from app.core.sim_ai.vector_db import (  # noqa: E402
    RagVectorStorage,
    statutes_collection_name,
)
from app.core.sim_ai.document_loader import StatuteDocumentLoader  # noqa: E402

SEED_DIR = Path("seeds")

TEST_QUERIES = [
    "버스정류소 근처에 흡연부스를 설치해도 되나?",
    "학교 주변 금연 관련 규정",
    "전기차 충전시설 설치 의무",
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


def load_seeds(seed_dir: Path):
    if not seed_dir.is_dir():
        sys.exit(f"{seed_dir}/ 폴더가 없음 - law.go.kr 원문(txt 또는 pdf)을 넣고 재실행")

    docs = []
    for p in sorted(seed_dir.glob("*.txt")):
        docs.append(_load_txt(p))
    for p in sorted(seed_dir.glob("*.pdf")):
        docs.append(_load_pdf(p))

    if not docs:
        # 🔴 `seeds/` 는 2026-08-24 부터 도메인 하위폴더로 나뉘어 있다. 위 glob 은
        #    **비재귀**라 부모를 주면 여기로 떨어진다 — 그게 정상이다(재귀로 훑으면
        #    도메인이 섞여 들어간다). 어디를 지목해야 하는지 이름으로 말해 준다.
        subs = sorted(d.name for d in seed_dir.iterdir()
                      if d.is_dir() and not d.name.startswith("."))
        # 🔴 `_` 로 시작하는 폴더는 **도메인이 아니다**(`_미분류` = 판정 못 한 것).
        #    후보로 늘어놓으면 「그중 하나 고르라」는 안내가 곧 무관한 조문을 남의
        #    콜렉션에 붓게 만든다 — 폴더를 나눈 이유가 바로 그것이다. 있다는 사실은
        #    말하되(원칙 4) 지목 대상에서는 뺀다.
        picks = [s for s in subs if not s.startswith("_")]
        others = [s for s in subs if s.startswith("_")]
        hint = ""
        if picks:
            hint += ("\n하위 폴더가 있다 — 그중 하나를 지목할 것(폴더 하나 = 도메인 하나): "
                     + " · ".join(f"--seeds {seed_dir}/{s}" for s in picks))
        if others:
            hint += ("\n(도메인 아님, 지목 금지: " + " · ".join(others)
                     + " — 판정 못 한 조례다. 그 폴더의 `왜_여기_있나.md` 참고)")
        sys.exit(f"{seed_dir}/에 txt 또는 pdf 파일이 없음{hint}")
    return docs


def _parse_args():
    ap = argparse.ArgumentParser(description="시드 조례를 그 도메인 벡터 콜렉션에 적재")
    ap.add_argument(
        "--domain",
        required=True,
        help="적재할 도메인 (예: 흡연). 콜렉션 statutes_<도메인> 이 된다. 필수.",
    )
    ap.add_argument(
        "--seeds",
        default=str(SEED_DIR),
        help=(f"원문 폴더. 이 폴더 전체가 위 도메인으로 들어간다 — 하위 폴더는 "
              f"안 훑는다. {SEED_DIR}/<도메인> 을 지목할 것 (예: {SEED_DIR}/흡연)."),
    )
    ap.add_argument(
        "--facility-type",
        default=None,
        help="청크 메타에 적을 시설 종류(부가정보). 안 주면 비워 둔다.",
    )
    ap.add_argument("--dry-run", action="store_true", help="적재 없이 파싱 리포트만")
    ap.add_argument("--no-clean", action="store_true", help="기존 청크 삭제 생략")
    return ap.parse_args()


async def main():
    args = _parse_args()
    domain = args.domain
    collection = statutes_collection_name(domain)  # 빈 도메인이면 여기서 죽는다
    seed_dir = Path(args.seeds)
    docs = load_seeds(seed_dir)

    print(f"도메인 '{domain}' → 콜렉션 '{collection}'")
    print(f"원문 폴더 {seed_dir}/ : {len(docs)}건")

    all_chunks = []
    for title, text, filename in docs:
        # A4: 시행일·조례번호를 원문에서 추출해 전 청크 메타에 실어보낸다.
        #     (조례 개정 시 '어느 판의 조문인지' 식별 - 발제 B-4 문서 단위 관리의 최소 요건)
        doc_meta = extract_doc_meta(text)
        chunks = parse_statute(
            text, title, facility_type=args.facility_type, doc_meta=doc_meta
        )
        for c in chunks:
            c.metadata["domain"] = domain
            c.metadata["upload_filename"] = filename
        print(f"\n== {title}: {len(chunks)}청크")
        print(f"   문서메타: {doc_meta or '(추출 실패 - 원문 머리말 형식 확인 필요)'}")
        print(length_report(chunks))
        for c in chunks[:2]:
            print("  예시:", c.text.splitlines()[0])
        all_chunks += chunks

    print(f"\n총 {len(all_chunks)}청크 → {collection}")
    if args.dry_run:
        print("(dry-run - 적재 생략)")
        return

    storage = RagVectorStorage()

    if not args.no_clean:
        # 콜렉션 행은 남기고 임베딩만 지운다. 범위는 이 도메인 하나로 구조적으로 묶인다.
        removed = storage.delete_statute_chunks(domain)
        print(f"클린 완료 - '{collection}' 콜렉션 유지, 임베딩 {removed}건 삭제")

    await storage.add_statute_chunks(
        domain,
        [c.text for c in all_chunks],
        metadatas=[c.metadata for c in all_chunks],
    )
    print(f"적재 완료 - {len(all_chunks)}청크")

    # 검증. 예전엔 '필터 없이'와 '필터 걸고' 둘 다 돌렸다 - 콜렉션이 하나라 태그가
    # 틀리면 서비스에서만 0건이 났기 때문이다. 지금은 서비스도 이 함수를 필터 없이
    # 부르므로(격리가 콜렉션이다) 아래 한 벌이 곧 서비스 경로 재현이다.
    print(f"\n== 검증 질의 (서비스와 같은 경로: {collection}) ==")
    empty = 0
    for q in TEST_QUERIES:
        res = await storage.retrieve_similar_statutes(q, domain, top_k=3)
        if not res:
            empty += 1
            print(f"[{q}]\n  → (없음)")
            continue
        # retrieve_similar_statutes 는 **dict 리스트**를 돌려준다(문자열이 아니다).
        top1 = (res[0].get("text") or "").replace("\n", " ")[:90]
        print(f"[{q}]\n  → {top1}...")

    stored = storage.count_statute_chunks(domain)
    if empty == len(TEST_QUERIES):
        print(
            f"\n⚠️ 전 질의 0건 - 콜렉션 '{collection}' 에는 {stored}청크가 있습니다.\n"
            f"   적재가 아니라 **유사도**가 안 나온 것입니다"
            f"(SIMILARITY_THRESHOLD 확인). 질의어가 이 도메인 조례와 맞는지 보세요 —\n"
            f"   위 TEST_QUERIES 는 흡연·전기차용 예시라 다른 도메인에서는 안 걸립니다."
        )
    else:
        print(f"\n콜렉션 '{collection}' 총 {stored}청크")


if __name__ == "__main__":
    asyncio.run(main())
