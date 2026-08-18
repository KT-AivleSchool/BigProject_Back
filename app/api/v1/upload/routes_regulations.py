import asyncio
import logging
import os
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status

from app.core.data_pipeline.statute_parser import extract_doc_meta, parse_statute
from app.core.sim_ai.vector_db import get_vector_db
from app.services.gam2_doc_extract import EXTRACTORS, TEXT_EXT, extract_text
from app.services.gam2_ordinance_select import (
    has_siting_provision,
    is_regulatory,
    split_articles,
)
from app.api.v1.upload.services import (
    RegulationItem,
    LAW_EXTENSIONS,
    LAW_REJECT_HINT,
    _dirs,
    _disk_match,
    _guard_preexisting,
    _is_extract_cache,
    _ledger_mark,
    _ledger_names,
    _ledger_unmark,
    _nfc,
    _resolve_facility_type,
    _safe_name,
    _save_stream,
    _chunk_counts,
)

logger = logging.getLogger("uvicorn.error")

router = APIRouter()


@router.get("/regulations", response_model=List[RegulationItem])
async def list_regulations(domain: str = Query(..., description="도메인 (예: 흡연)")):
    paths = _dirs(domain)
    law_dir = Path(paths["law"])

    counts: dict[str, int] = {}
    try:
        vector_db = get_vector_db()
        counts = await asyncio.to_thread(_chunk_counts, vector_db, domain)
    except Exception as e:
        logger.warning(f"[upload] 청크 수 조회 실패: {e}")

    ledger_law = _ledger_names(domain, "law")
    items = []
    for raw in sorted(os.listdir(law_dir)):
        if _is_extract_cache(raw):
            continue
        path = law_dir / raw
        if not path.is_file():
            continue
        if os.path.splitext(raw)[1].lower() not in LAW_EXTENSIONS:
            continue
        name = _nfc(raw)
        ready = (
            os.path.splitext(raw)[1].lower() in TEXT_EXT
            or _disk_match(law_dir, raw + ".txt") is not None
        )
        items.append(
            RegulationItem(
                filename=name,
                size=path.stat().st_size,
                chunks_in_vector_db=counts.get(raw) or counts.get(name) or 0,
                text_ready=ready,
                source="upload" if name in ledger_law else "preexisting",
                deletable=name in ledger_law,
            )
        )
    return items


@router.post("/regulation")
async def upload_regulation(
    domain: str = Form(...),
    files: List[UploadFile] = File(...),
    facility_type: Optional[str] = Form(None),
    create_domain: bool = Form(False),
    ingest: bool = Form(True),
):
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
                detail=f"'{name}' 은 추출 캐시 이름 규약과 겹칩니다.",
            )

        twin = _disk_match(law_dir, name)
        dest = law_dir / name
        replaced = twin is not None
        size, sha = await _save_stream(up, dest)

        warnings: List[str] = []
        if twin is not None and twin.name != name:
            twin.unlink()
            twin_cache = _disk_match(law_dir, twin.name + ".txt")
            if twin_cache is not None:
                twin_cache.unlink()

        if ext in TEXT_EXT:
            text = dest.read_text(encoding="utf-8", errors="replace")
        else:
            text = await asyncio.to_thread(extract_text, str(dest), True, False)
            if not text:
                warnings.append("텍스트를 뽑지 못했습니다.")

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
        _ledger_mark(domain, "law", name, {"uploaded_at": now, "sha256": sha, "size": size})

        if text:
            arts = [a for a in split_articles(text) if a.get("no")]
            entry["articles"] = len(arts)
            entry["regulatory_articles"] = sum(1 for a in arts if is_regulatory(a)[0])
            siting, signals = has_siting_provision(text)
            entry["has_siting_provision"] = siting
            entry["siting_signals"] = signals

        if text and ingest:
            try:
                doc_meta = extract_doc_meta(text)
                title = doc_meta.get("doc_title_detected") or os.path.splitext(name)[0]
                chunks = parse_statute(
                    text, title, facility_type=fac_type, doc_meta=doc_meta
                )
                for c in chunks:
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
                if twin is not None and twin.name != name:
                    removed += await asyncio.to_thread(
                        vector_db.delete_statute_chunks,
                        domain=domain,
                        upload_filename=twin.name,
                    )
                entry["deleted_old_chunks"] = removed

                if chunks:
                    await vector_db.add_statute_chunks(
                        [c.text for c in chunks], metadatas=[c.metadata for c in chunks]
                    )
                    entry["chunks"] = len(chunks)
                    entry["ingested"] = True
            except Exception as e:
                logger.error(f"[upload] {name} 벡터 적재 실패: {e}")
                warnings.append(f"벡터 DB 적재 실패: {e}")

        reports.append(entry)

    return {
        "ok": all(r["ingested"] for r in reports) if ingest else True,
        "domain": domain,
        "saved_to": str(law_dir),
        "facility_type": fac_type,
        "facility_type_source": fac_source,
        "files": reports,
    }


@router.delete("/regulations/{filename}")
async def delete_regulation(
    filename: str,
    domain: str = Query(...),
    force: bool = Query(False),
):
    paths = _dirs(domain)
    law_dir = Path(paths["law"])
    name = _safe_name(urllib.parse.unquote(filename))
    target = _disk_match(law_dir, name)

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{domain}' 에 조례 파일이 없습니다: {name}",
        )

    _guard_preexisting(domain, "law", name, force)
    target.unlink()
    _ledger_unmark(domain, "law", name)
    cache = _disk_match(law_dir, name + ".txt") or _disk_match(law_dir, target.name + ".txt")
    cache_removed = False
    if cache is not None:
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
            detail=f"파일은 지웠지만 벡터 청크 삭제에 실패했습니다: {e}",
        )

    return {
        "status": "success",
        "domain": domain,
        "filename": name,
        "extract_cache_removed": cache_removed,
        "vector_chunks_removed": removed,
    }
