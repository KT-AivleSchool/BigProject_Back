# -*- coding: utf-8 -*-
"""업로드 API — 조례 문서 · 감리용 데이터 파일.

2026-08-09 재작성. 이전 판은 파일을 `uploads/regulations/` 에 모아두었는데
**파이프라인이 읽는 곳이 아니다**. 그래서 올려도 STEP1 감리가 못 봤고,
벡터 DB 에는 `{"source","filename"}` 두 키만 붙어 시설 구분도 안 됐다.
경로 조작도 열려 있었다(`os.path.join(UPLOAD_DIR, file.filename)`).

지금 규약
  조례   → `data_임시/<도메인>/law/`   ... `load_ordinance()` 가 읽는 바로 그 폴더
  데이터 → `data_임시/<도메인>/data/`  ... `profile_folder()` 가 읽는 바로 그 폴더
  **도메인은 필수 인자다.** 도메인이 없으면 어느 파이프라인의 입력인지 정할 수 없다.
  "엔진은 그대로, 데이터만 바꾼다" 는 데이터가 도메인별로 갈려 있어야 성립한다.

여러 파일
  두 종류 다 **여러 개**를 넣는 게 정상이다. 조례는 `load_ordinance()` 가
  폴더 안 `*.txt`·`*.md` 를 전부 합쳐서 쓰고(실측: EV 5개 · 흡연 3개 · 재활용 2개),
  데이터는 파일 하나가 dataset 하나가 된다. 덮어쓰지 않는다.

Redis
  **메타데이터만** 올린다(파일 목록·크기·해시·검증결과·업로드 시각).
  파일 본문은 안 올린다 — 흡연 도메인 `data/` 만 537MB 이고 단일 파일 최대 279MB 다.
  정본은 디스크이고 Redis 는 조회용 색인이다. 그래서 조회 시 디스크와 대조해
  어긋나면 Redis 쪽을 고친다 — 색인이 사실과 다르면 없는 것만 못하다(원칙 4).

벡터 DB 적재
  조례는 `statute_parser.parse_statute()` 로 조(條) 단위 청킹해
  `statutes_collection` 에 넣는다. 데이터팀 시드(`ingest_statutes.py`)와 **같은 파서**다.
  다른 파서를 쓰면 같은 조례가 청크 모양이 달라져 검색 결과가 적재 경로에 따라 갈린다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import redis.asyncio as aioredis
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from app.api.deps import get_redis
from app.config import DOMAIN_ROOT, domain_paths
from app.core.data_pipeline.statute_parser import extract_doc_meta, parse_statute
from app.core.sim_ai.vector_db import get_vector_db
from app.services.gam2_doc_extract import EXTRACTORS, TEXT_EXT, extract_text
from app.services.gam2_ordinance_select import (
    has_siting_provision,
    is_regulatory,
    split_articles,
)
from app.services.gam2_profile import DATA_EXTENSIONS, list_dataset_files

# 도메인 이름 검증은 파이프라인 러너와 **같은 함수**를 쓴다. 여기서 다시 짜면
# 한쪽만 고쳐졌을 때 업로드는 통과하는데 실행은 400 이 되는 상태가 생긴다.
from app.services.pipeline_runner import (
    _validate_domain,
    fixture_blocker,
    RunRequestError,
)

logger = logging.getLogger("uvicorn.error")

router = APIRouter()

# ── 확장자 정책 ─────────────────────────────────────────────────────────────
# 조례: 텍스트로 **읽을 수 있는 것만** 받는다. 목록은 gam2_doc_extract 에서 가져온다.
#   .hwp(구 한글 바이너리)·.doc 는 여기 없다 — 추출기가 없어서 받아도 0청크다.
#   예전 판은 둘 다 허용해놓고 조용히 아무것도 적재하지 않았다(원칙 1).
LAW_EXTENSIONS: tuple[str, ...] = tuple(TEXT_EXT) + tuple(EXTRACTORS)
LAW_REJECT_HINT = {
    ".hwp": "한글 파일은 .hwpx 로 저장하거나 PDF 로 변환해 올려주세요(.hwp 는 추출기가 없습니다).",
    ".doc": ".docx 로 저장해 올려주세요(.doc 는 추출기가 없습니다).",
}

# 데이터: 프로파일러가 dataset 으로 세는 확장자 + shapefile 부속 파일.
#   부속은 그 자체로 dataset 이 아니지만 .shp 는 부속 없이는 못 읽는다.
SIDECAR_EXTENSIONS = (".dbf", ".shx", ".prj", ".cpg", ".qpj", ".sbn", ".sbx")
DATA_UPLOAD_EXTENSIONS: tuple[str, ...] = tuple(DATA_EXTENSIONS) + SIDECAR_EXTENSIONS

STREAM_CHUNK = 1024 * 1024  # 1MB. 279MB 짜리가 실재하므로 통째로 read() 하지 않는다

# Redis 키 — 도메인별 해시 1개. 필드 = 파일명, 값 = 메타 JSON.
_REDIS_KEY = "omnisite:upload:{domain}:data"


# ══════════════════════════════════════════════════════════════════════
# 공용
# ══════════════════════════════════════════════════════════════════════
def _dirs(domain: str, create: bool = False) -> dict:
    """도메인 폴더를 확인(필요하면 생성)하고 경로 dict 를 돌려준다."""
    if not domain or Path(domain).name != domain or domain in (".", ".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"도메인 이름이 잘못됐습니다: {domain!r}",
        )

    root = Path(str(DOMAIN_ROOT)) / domain
    if create and not root.is_dir():
        for sub in ("data", "law"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        logger.info(f"[upload] 새 도메인 폴더 생성: {root}")

    try:
        _validate_domain(domain)
    except RunRequestError as e:
        # 오타 하나로 새 도메인이 조용히 생기면 안 된다 — 생성은 명시적 의사표시로만.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{e} · 새 도메인을 만들려면 create_domain=true 로 보내세요. "
                f"현재 도메인: {_known_domains()}"
            ),
        )

    paths = domain_paths(domain)
    for key in ("data", "law"):
        os.makedirs(paths[key], exist_ok=True)
    return paths


def _known_domains() -> List[str]:
    """`data_임시/` 아래 실제 도메인 폴더(= data/ 또는 law/ 를 가진 것)."""
    root = Path(str(DOMAIN_ROOT))
    if not root.is_dir():
        return []
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and ((p / "data").is_dir() or (p / "law").is_dir())
    )


def _safe_name(filename: Optional[str]) -> str:
    """업로드 파일명에서 **경로 성분을 제거**한다.

    `os.path.join(dir, file.filename)` 은 `../` 나 절대경로를 그대로 받아들인다.
    파일명은 이름이지 경로가 아니다.
    """
    raw = (filename or "").strip()
    name = os.path.basename(raw.replace("\\", "/"))
    if not name or name in (".", ".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"파일명이 잘못됐습니다: {filename!r}",
        )
    return name


async def _save_stream(upload: UploadFile, dest: Path) -> tuple[int, str]:
    """업로드를 디스크로 흘려 쓰면서 크기·sha256 을 같이 낸다. 반환 (bytes, sha256)."""
    h = hashlib.sha256()
    size = 0
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with open(tmp, "wb") as f:
            while True:
                block = await upload.read(STREAM_CHUNK)
                if not block:
                    break
                f.write(block)
                h.update(block)
                size += len(block)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return size, h.hexdigest()


def _resolve_facility_type(domain: str, explicit: Optional[str]) -> tuple[str, str]:
    """벡터 DB 태깅에 쓸 `facility_type` 과 그 **출처**를 정한다.

    하드코딩하지 않는다. `parse_statute` 의 기본값이 `"흡연부스"` 라 그대로 두면
    어느 도메인을 올려도 흡연부스 조례가 된다 — 안 터지고 값만 틀린다.

    우선순위
      1) 요청이 직접 준 값                     source=request
      2) STEP1 감리 확정본의 facility_inference source=audit_reviewed
      둘 다 없으면 400. 추측해서 태깅하지 않는다(원칙 1·5).
    """
    if explicit and explicit.strip():
        return explicit.strip(), "request"

    reviewed = (
        Path(str(DOMAIN_ROOT)) / "step1_output" / f"{domain}_audit_result_reviewed.json"
    )
    if reviewed.is_file():
        try:
            doc = json.loads(reviewed.read_text(encoding="utf-8"))
            fac = (doc.get("facility_inference") or {}).get("facility")
            if fac:
                return str(fac), "audit_reviewed"
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"[upload] {reviewed} 읽기 실패: {e}")

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"도메인 '{domain}' 의 시설 종류를 알 수 없습니다. "
            f"STEP1 감리(`{reviewed.name}`)가 아직 없으면 facility_type 을 함께 보내세요. "
            f"이 값은 토론 단계의 조례 검색 필터와 정확히 일치해야 합니다."
        ),
    )


def _is_extract_cache(name: str) -> bool:
    """`원본.pdf.txt` 처럼 gam2_doc_extract 가 만든 캐시인가."""
    if not name.endswith(".txt"):
        return False
    stem_ext = os.path.splitext(name[: -len(".txt")])[1].lower()
    return stem_ext in EXTRACTORS


# ══════════════════════════════════════════════════════════════════════
# 1. 도메인 목록 — 프런트가 '필수 인자' 를 채울 수 있게
# ══════════════════════════════════════════════════════════════════════
class DomainItem(BaseModel):
    domain: str
    law_files: int
    data_files: int
    has_audit_reviewed: bool = Field(
        ..., description="STEP1 감리 확정본이 있는가(= facility_type 자동 판별 가능)"
    )
    has_fixture: bool = Field(
        ...,
        description="mode=fixture·hitl 로 돌릴 수 있는가(= <도메인>_FIX 픽스처가 온전한가). "
        "false 면 그 두 모드는 400 이다. mode=full 은 픽스처와 무관하다",
    )


@router.get("/domains", response_model=List[DomainItem])
async def list_domains():
    """업로드 대상 도메인 목록. 업로드 API 는 전부 domain 이 필수다.

    `has_fixture` 는 **러너 자신의 사전검사**로 판정한다
    (`pipeline_runner.fixture_blocker` → `build_commands` → `_load_fixture`).
    여기서 `Path(f"{name}_FIX").is_dir()` 같은 자체 판정식을 쓰면 안 된다 —
    실제 조건은 폴더가 아니라 **파일 둘**이라, 폴더만 보면 "가능" 이라 답해놓고
    실행이 400 으로 죽는다(프런트가 카드 단계에서 막지 못한다).

    막힌 **이유**는 응답에 넣지 않는다. 이유 문자열에 저장소 절대경로가 들어가는데,
    `GET /pipeline/runs/{id}/log` 는 그 경로를 `<repo>` 로 마스킹해서 내보낸다 —
    한쪽만 원문으로 내보내면 마스킹이 무의미해진다.
    """
    out = []
    for name in _known_domains():
        p = domain_paths(name)
        law = [
            f
            for f in os.listdir(p["law"])
            if os.path.splitext(f)[1].lower() in LAW_EXTENSIONS
            and not _is_extract_cache(f)
        ] if os.path.isdir(p["law"]) else []
        data = list_dataset_files(p["data"]) if os.path.isdir(p["data"]) else []
        reviewed = (
            Path(str(DOMAIN_ROOT))
            / "step1_output"
            / f"{name}_audit_result_reviewed.json"
        )
        out.append(
            DomainItem(
                domain=name,
                law_files=len(law),
                data_files=len(data),
                has_audit_reviewed=reviewed.is_file(),
                has_fixture=fixture_blocker(name) is None,
            )
        )
    return out


# ══════════════════════════════════════════════════════════════════════
# 2. 조례 업로드
# ══════════════════════════════════════════════════════════════════════
class RegulationItem(BaseModel):
    filename: str
    size: int
    text_ready: bool = Field(..., description="텍스트 추출이 끝나 STEP1 이 읽을 수 있는가")
    chunks_in_vector_db: int


@router.get("/regulations", response_model=List[RegulationItem])
async def list_regulations(domain: str = Query(..., description="도메인 (예: 흡연)")):
    """도메인의 조례 파일 목록. 파일 하나가 여러 청크가 되므로 청크 수도 같이 준다."""
    paths = _dirs(domain)
    law_dir = Path(paths["law"])

    counts: dict[str, int] = {}
    try:
        vector_db = get_vector_db()
        counts = await asyncio.to_thread(_chunk_counts, vector_db, domain)
    except Exception as e:  # 벡터 DB 가 죽어도 파일 목록은 줄 수 있다
        logger.warning(f"[upload] 청크 수 조회 실패: {e}")

    items = []
    for name in sorted(os.listdir(law_dir)):
        if _is_extract_cache(name):
            continue
        path = law_dir / name
        if not path.is_file():
            continue
        if os.path.splitext(name)[1].lower() not in LAW_EXTENSIONS:
            continue
        ready = (
            os.path.splitext(name)[1].lower() in TEXT_EXT
            or (law_dir / (name + ".txt")).is_file()
        )
        items.append(
            RegulationItem(
                filename=name,
                size=path.stat().st_size,
                text_ready=ready,
                chunks_in_vector_db=counts.get(name, 0),
            )
        )
    return items


def _chunk_counts(vector_db, domain: str) -> dict[str, int]:
    """이 도메인에서 올린 조례의 파일별 청크 수 (동기 — to_thread 로 부른다)."""
    from sqlalchemy import create_engine, text as sql_text

    engine = create_engine(vector_db.connection_string)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sql_text(
                    "SELECT e.cmetadata->>'upload_filename' AS fn, count(*) "
                    "FROM langchain_pg_embedding e "
                    "JOIN langchain_pg_collection c ON c.uuid = e.collection_id "
                    "WHERE c.name = 'statutes_collection' "
                    "  AND e.cmetadata->>'domain' = :d "
                    "GROUP BY 1"
                ),
                {"d": domain},
            ).fetchall()
        return {r[0]: r[1] for r in rows if r[0]}
    finally:
        engine.dispose()


@router.post("/regulation")
async def upload_regulation(
    domain: str = Form(..., description="도메인 (예: 흡연). 필수."),
    files: List[UploadFile] = File(...),
    facility_type: Optional[str] = Form(
        None, description="벡터 DB 태깅용 시설 종류. 생략하면 STEP1 감리 확정본에서 읽는다."
    ),
    create_domain: bool = Form(False, description="도메인 폴더가 없으면 만든다"),
    ingest: bool = Form(True, description="벡터 DB(statutes_collection) 적재 여부"),
):
    """조례 문서 다중 업로드 → `data_임시/<도메인>/law/` 저장 + 벡터 DB 적재.

    한 번에 여러 개를 올릴 수 있고 기존 파일도 지워지지 않는다.
    STEP1 감리는 이 폴더의 텍스트를 **전부 합쳐서** 조례 근거로 쓴다.

    같은 이름을 다시 올리면 파일은 덮어쓰고 **그 파일의 옛 청크는 지운 뒤** 다시 넣는다.
    안 지우면 같은 조문이 두 번 인용된다.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="업로드할 파일이 없습니다."
        )

    paths = _dirs(domain, create=create_domain)
    law_dir = Path(paths["law"])
    fac_type, fac_source = _resolve_facility_type(domain, facility_type)

    vector_db = get_vector_db() if ingest else None
    now = datetime.now().isoformat(timespec="seconds")
    reports: List[dict] = []

    for up in files:
        name = _safe_name(up.filename)
        ext = os.path.splitext(name)[1].lower()

        if ext not in LAW_EXTENSIONS:
            hint = LAW_REJECT_HINT.get(ext, f"허용 확장자: {', '.join(LAW_EXTENSIONS)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{name}' 은 조례로 읽을 수 없습니다. {hint}",
            )
        if _is_extract_cache(name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"'{name}' 은 추출 캐시 이름 규약(<원본>{ext})과 겹칩니다. "
                    f"원본을 올리면 텍스트는 자동으로 만들어집니다."
                ),
            )

        dest = law_dir / name
        replaced = dest.exists()
        size, sha = await _save_stream(up, dest)

        warnings: List[str] = []

        # ── 텍스트 확보: 정본 추출기(gam2_doc_extract)가 <원본>.txt 를 옆에 만든다.
        #    STEP1 `load_ordinance()` 가 그 .txt 를 글롭하므로 여기서 만들어두면
        #    파이프라인 쪽은 손댈 게 없다.
        if ext in TEXT_EXT:
            text = dest.read_text(encoding="utf-8", errors="replace")
        else:
            text = await asyncio.to_thread(extract_text, str(dest), True, False)
            if not text:
                warnings.append(
                    "텍스트를 뽑지 못했습니다 — 스캔본(이미지 PDF)이거나 추출 패키지가 "
                    "없습니다. 파일은 저장했지만 감리·검색에는 쓰이지 않습니다."
                )

        entry = {
            "filename": name,
            "size": size,
            "sha256": sha,
            "replaced": replaced,
            "text_chars": len(text or ""),
            "articles": 0,
            "regulatory_articles": 0,
            "has_siting_provision": False,
            "siting_signals": [],
            "chunks": 0,
            "deleted_old_chunks": 0,
            "ingested": False,
            "warnings": warnings,
        }

        if text:
            # ── 조문 구조 점검 (LLM 0회). 파일이 '읽혔다' 와 '쓸모가 있다' 는 다르다.
            arts = [a for a in split_articles(text) if a.get("no")]
            entry["articles"] = len(arts)
            entry["regulatory_articles"] = sum(1 for a in arts if is_regulatory(a)[0])
            siting, signals = has_siting_provision(text)
            entry["has_siting_provision"] = siting
            entry["siting_signals"] = signals
            if not arts:
                warnings.append(
                    "조문(제N조)을 하나도 못 찾았습니다 — 조판(들여쓰기·단 구성)을 확인하세요."
                )
            elif not siting:
                # 근거 없이 '규정 없음' 을 확정하지 않는다(원칙 4·5).
                warnings.append(
                    "이격거리·설치금지 규정이 이 문서에는 없습니다. "
                    "상위법에는 있을 수 있습니다."
                )

        if text and ingest:
            try:
                doc_meta = extract_doc_meta(text)
                title = doc_meta.get("doc_title_detected") or os.path.splitext(name)[0]
                chunks = parse_statute(
                    text, title, facility_type=fac_type, doc_meta=doc_meta
                )
                for c in chunks:
                    # 🔴 parse_statute 는 `source="official_seed"` 를 박아서 낸다.
                    #    사용자 업로드분에 그대로 두면 "공식 시드" 라고 거짓말한다.
                    c.metadata["source"] = "user_upload"
                    c.metadata["domain"] = domain
                    c.metadata["upload_filename"] = name
                    c.metadata["uploaded_at"] = now
                    c.metadata["facility_type_source"] = fac_source

                removed = await asyncio.to_thread(
                    vector_db.delete_statute_chunks,
                    domain=domain,
                    upload_filename=name,
                )
                entry["deleted_old_chunks"] = removed

                if chunks:
                    await vector_db.add_statute_chunks(
                        [c.text for c in chunks], metadatas=[c.metadata for c in chunks]
                    )
                    entry["chunks"] = len(chunks)
                    entry["ingested"] = True
                else:
                    warnings.append("청크 0개 — 벡터 DB 에 넣을 조문이 없습니다.")
            except Exception as e:
                # 파일은 이미 저장됐다. 그 사실을 숨기지 않고 어디까지 됐는지 남긴다.
                logger.error(f"[upload] {name} 벡터 적재 실패: {e}")
                warnings.append(f"벡터 DB 적재 실패: {e}")

        reports.append(entry)

    ingested = [r for r in reports if r["ingested"]]
    if ingest and not ingested:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "파일은 저장했지만 벡터 DB 에 적재된 조문이 하나도 없습니다.",
                "domain": domain,
                "saved_to": str(law_dir),
                "files": reports,
            },
        )

    return {
        "ok": all(r["ingested"] for r in reports) if ingest else True,
        "domain": domain,
        "saved_to": str(law_dir),
        "facility_type": fac_type,
        "facility_type_source": fac_source,
        "law_files_total": len(
            [
                f
                for f in os.listdir(law_dir)
                if os.path.splitext(f)[1].lower() in LAW_EXTENSIONS
                and not _is_extract_cache(f)
            ]
        ),
        "files": reports,
    }


@router.delete("/regulations/{filename}")
async def delete_regulation(
    filename: str, domain: str = Query(..., description="도메인 (예: 흡연)")
):
    """조례 파일 + 추출 캐시 + 그 파일에서 나온 벡터 청크를 함께 지운다.

    셋 중 하나만 지우면 남은 쪽이 계속 검색에 잡힌다.
    """
    paths = _dirs(domain)
    law_dir = Path(paths["law"])
    name = _safe_name(urllib.parse.unquote(filename))
    target = law_dir / name

    if not target.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{domain}' 에 조례 파일이 없습니다: {name}",
        )

    target.unlink()
    cache = law_dir / (name + ".txt")
    cache_removed = False
    if cache.is_file():
        cache.unlink()
        cache_removed = True

    removed = 0
    try:
        removed = await asyncio.to_thread(
            get_vector_db().delete_statute_chunks, domain=domain, upload_filename=name
        )
    except Exception as e:
        logger.error(f"[upload] {name} 청크 삭제 실패: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"파일은 지웠지만 벡터 청크 삭제에 실패했습니다: {e} "
                f"— 검색에는 계속 잡힙니다."
            ),
        )

    return {
        "status": "success",
        "domain": domain,
        "filename": name,
        "extract_cache_removed": cache_removed,
        "vector_chunks_removed": removed,
    }


# ══════════════════════════════════════════════════════════════════════
# 3. 감리용 데이터 파일 업로드 (+ Redis 메타데이터)
# ══════════════════════════════════════════════════════════════════════
def _dataset_map(data_dir: str) -> dict[str, str]:
    """현재 폴더 상태의 dataset_id → 파일명. 규칙은 `gam2_profile` 것을 그대로 쓴다."""
    return {
        f"{i:02d}": os.path.basename(p)
        for i, p in enumerate(list_dataset_files(data_dir), 1)
    }


async def _redis_put(redis: aioredis.Redis, domain: str, name: str, meta: dict) -> None:
    await redis.hset(_REDIS_KEY.format(domain=domain), name, json.dumps(meta, ensure_ascii=False))


@router.post("/data")
async def upload_data(
    domain: str = Form(..., description="도메인 (예: 흡연). 필수."),
    files: List[UploadFile] = File(...),
    create_domain: bool = Form(False),
    redis: aioredis.Redis = Depends(get_redis),
):
    """감리(STEP1)에 쓸 데이터 파일 다중 업로드 → `data_임시/<도메인>/data/`.

    🔴 **dataset_id 는 파일명 가나다순으로 다시 매겨진다.** 파일을 하나 끼워 넣으면
       뒤 번호가 전부 밀리고, 이미 돌린 `<도메인>_audit_result_reviewed.json` ·
       정제 캐시의 번호와 어긋난다. 그래서 응답에 **번호 변화(renumbered)** 를 담는다.
       조용히 밀리면 감리 결과가 엉뚱한 파일에 붙는다 — 안 터지고 값만 틀린다.

    Redis 에는 **메타데이터만** 넣는다(파일 본문 아님). 정본은 디스크다.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="업로드할 파일이 없습니다."
        )

    paths = _dirs(domain, create=create_domain)
    data_dir = Path(paths["data"])
    before = _dataset_map(str(data_dir))

    now = datetime.now().isoformat(timespec="seconds")
    reports: List[dict] = []

    for up in files:
        name = _safe_name(up.filename)
        ext = os.path.splitext(name)[1].lower()

        if ext not in DATA_UPLOAD_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"'{name}' 은 감리 대상 데이터가 아닙니다. "
                    f"허용: {', '.join(DATA_UPLOAD_EXTENSIONS)}"
                ),
            )
        if name.startswith(("_", ".")):
            # 프로파일러가 부속·숨김으로 보고 건너뛴다. 받아놓고 안 쓰면 거짓말이다.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"'{name}' 처럼 '_'·'.' 로 시작하는 파일은 프로파일러가 "
                    f"부속 파일로 보고 건너뜁니다. 이름을 바꿔 올려주세요."
                ),
            )

        dest = data_dir / name
        replaced = dest.exists()
        size, sha = await _save_stream(up, dest)

        meta = {
            "filename": name,
            "domain": domain,
            "ext": ext,
            "size": size,
            "sha256": sha,
            "is_dataset": ext in DATA_EXTENSIONS,  # 부속(.dbf 등)은 dataset 이 아니다
            "replaced": replaced,
            "uploaded_at": now,
            "path": str(dest),
        }
        await _redis_put(redis, domain, name, meta)
        reports.append(meta)

    after = _dataset_map(str(data_dir))
    renumbered = [
        {"dataset_id": did, "before": before.get(did), "after": after[did]}
        for did in sorted(after)
        if before.get(did) != after[did]
    ]

    # 번호가 밀리면 Redis 색인에도 최신 번호를 반영한다(색인이 사실과 달라지지 않게).
    by_name = {v: k for k, v in after.items()}
    key = _REDIS_KEY.format(domain=domain)
    for name, raw in (await redis.hgetall(key)).items():
        try:
            m = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if m.get("dataset_id") != by_name.get(name):
            m["dataset_id"] = by_name.get(name)
            await redis.hset(key, name, json.dumps(m, ensure_ascii=False))

    return {
        "ok": True,
        "domain": domain,
        "saved_to": str(data_dir),
        "files": reports,
        "dataset_count": len(after),
        "dataset_map": after,
        "renumbered": renumbered,
        "warning": (
            "dataset_id 가 바뀌었습니다. 이미 감리·정제를 돌렸다면 "
            "재프로파일 → 재감리가 필요합니다."
        )
        if renumbered
        else None,
    }


@router.get("/data")
async def list_data(
    domain: str = Query(..., description="도메인 (예: 흡연)"),
    redis: aioredis.Redis = Depends(get_redis),
):
    """데이터 파일 목록. **디스크가 정본**이고 Redis 는 색인이라 매번 대조한다.

    대조 결과(`redis_synced`)를 숨기지 않는다 — 색인만 보고 답하면
    지워진 파일이 계속 있는 것처럼 보인다.
    """
    paths = _dirs(domain)
    data_dir = Path(paths["data"])
    key = _REDIS_KEY.format(domain=domain)

    cached = {}
    for name, raw in (await redis.hgetall(key)).items():
        try:
            cached[name] = json.loads(raw)
        except json.JSONDecodeError:
            cached[name] = {}

    on_disk = {
        p.name: p
        for p in data_dir.iterdir()
        if p.is_file() and os.path.splitext(p.name)[1].lower() in DATA_UPLOAD_EXTENSIONS
    }
    dataset_map = _dataset_map(str(data_dir))
    by_name = {v: k for k, v in dataset_map.items()}

    stale = [n for n in cached if n not in on_disk]
    if stale:
        await redis.hdel(key, *stale)

    items = []
    for name, p in sorted(on_disk.items()):
        m = dict(cached.get(name) or {})
        st = p.stat()
        m.update(
            {
                "filename": name,
                "domain": domain,
                "ext": os.path.splitext(name)[1].lower(),
                "size": st.st_size,
                "dataset_id": by_name.get(name),
                "is_dataset": name in by_name,
                "path": str(p),
            }
        )
        if "sha256" not in m:
            # 업로드를 거치지 않고 폴더에 직접 놓인 파일. 해시는 "모른다" 로 둔다.
            m["sha256"] = None
            m["uploaded_at"] = None
            m["source"] = "preexisting"
        else:
            m["source"] = "upload"
        items.append(m)
        await _redis_put(redis, domain, name, m)

    return {
        "domain": domain,
        "data_dir": str(data_dir),
        "dataset_count": len(dataset_map),
        "dataset_map": dataset_map,
        "redis_key": key,
        "redis_stale_removed": stale,
        "files": items,
    }


@router.delete("/data/{filename}")
async def delete_data(
    filename: str,
    domain: str = Query(..., description="도메인 (예: 흡연)"),
    redis: aioredis.Redis = Depends(get_redis),
):
    """데이터 파일 삭제. 남은 파일의 dataset_id 가 어떻게 바뀌는지 같이 알린다."""
    paths = _dirs(domain)
    data_dir = Path(paths["data"])
    name = _safe_name(urllib.parse.unquote(filename))
    target = data_dir / name

    if not target.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{domain}' 에 데이터 파일이 없습니다: {name}",
        )

    before = _dataset_map(str(data_dir))
    target.unlink()
    after = _dataset_map(str(data_dir))
    await redis.hdel(_REDIS_KEY.format(domain=domain), name)

    renumbered = [
        {"dataset_id": did, "before": before.get(did), "after": after[did]}
        for did in sorted(after)
        if before.get(did) != after[did]
    ]
    return {
        "status": "success",
        "domain": domain,
        "filename": name,
        "dataset_count": len(after),
        "dataset_map": after,
        "renumbered": renumbered,
        "warning": (
            "dataset_id 가 바뀌었습니다. 재프로파일 → 재감리가 필요합니다."
        )
        if renumbered
        else None,
    }
