from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from app.config import (
    DATA_ROOT,
    DOMAIN_ROOT,
    USER_INPUT_ROOT,
    USER_INPUT_SUBDIR,
    domain_paths,
    user_domain_paths,
)
from app.core.data_pipeline.statute_parser import extract_doc_meta, parse_statute
from app.services.gam2_doc_extract import EXTRACTORS, TEXT_EXT, extract_text
from app.services.gam2_ordinance_select import (
    has_siting_provision,
    is_regulatory,
    split_articles,
)
from app.services.gam2_profile import DATA_EXTENSIONS, list_dataset_files
from app.services import user_input_pruner
from app.services.pipeline_runner import (
    MODE_FULL,
    _validate_domain,
    fixture_blocker,
    RunRequestError,
)

logger = logging.getLogger("uvicorn.error")

LAW_EXTENSIONS: tuple[str, ...] = tuple(TEXT_EXT) + tuple(EXTRACTORS)
LAW_REJECT_HINT = {
    ".hwp": "한글 파일은 .hwpx 로 저장하거나 PDF 로 변환해 올려주세요(.hwp 는 추출기가 없습니다).",
    ".doc": ".docx 로 저장해 올려주세요(.doc 는 추출기가 없습니다).",
}

SIDECAR_EXTENSIONS = (".dbf", ".shx", ".prj", ".cpg", ".qpj", ".sbn", ".sbx")
DATA_UPLOAD_EXTENSIONS: tuple[str, ...] = tuple(DATA_EXTENSIONS) + SIDECAR_EXTENSIONS

STREAM_CHUNK = 1024 * 1024
_REDIS_KEY = "omnisite:upload:{domain}:data"
_REDIS_TTL_SEC = 30 * 24 * 3600
_LEDGER_NAME = ".upload_ledger.json"


def _dirs(domain: str, create: bool = False) -> dict:
    if not domain or Path(domain).name != domain or domain in (".", ".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"도메인 이름이 잘못됐습니다: {domain!r}",
        )

    root = Path(str(USER_INPUT_ROOT)) / domain
    if create and not root.is_dir():
        for sub in ("data", "law"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        logger.info(f"[upload] 새 도메인 폴더 생성: {root}")

    try:
        _validate_domain(domain, MODE_FULL)
    except RunRequestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{e} · 새 도메인을 만들려면 create_domain=true 로 보내세요. "
                f"현재 도메인: {_known_domains()}"
            ),
        )

    paths = user_domain_paths(domain)
    for key in ("data", "law"):
        os.makedirs(paths[key], exist_ok=True)
    return paths


def _known_domains() -> List[str]:
    root = Path(str(USER_INPUT_ROOT))
    if not root.is_dir():
        return []
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and ((p / "data").is_dir() or (p / "law").is_dir())
    )


def _preset_domains() -> List[str]:
    root = Path(str(DOMAIN_ROOT))
    if not root.is_dir():
        return []
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir()
        and p.name != USER_INPUT_SUBDIR
        and ((p / "data").is_dir() or (p / "law").is_dir())
    )


def _ledger_path(domain: str) -> Path:
    return Path(str(USER_INPUT_ROOT)) / domain / _LEDGER_NAME


def _ledger_read(domain: str) -> dict:
    p = _ledger_path(domain)
    try:
        if not p.is_file():
            return {"data": {}, "law": {}}
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[upload] 업로드 원장을 못 읽었습니다({p}): {e} — 빈 원장으로 봅니다")
        return {"data": {}, "law": {}}
    if not isinstance(doc, dict):
        return {"data": {}, "law": {}}
    for k in ("data", "law"):
        if not isinstance(doc.get(k), dict):
            doc[k] = {}
    return doc


def _ledger_mark(domain: str, kind: str, name: str, meta: dict) -> None:
    p = _ledger_path(domain)
    doc = _ledger_read(domain)
    bucket = doc.setdefault(kind, {})
    for k in [k for k in bucket if k != name and _nfc(k) == _nfc(name)]:
        bucket.pop(k, None)
    bucket[name] = {
        "uploaded_at": meta.get("uploaded_at"),
        "sha256": meta.get("sha256"),
        "size": meta.get("size"),
    }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        logger.warning(f"[upload] 업로드 원장 기록 실패({p}): {e} — '{name}' 은 삭제가 막힙니다")


def _ledger_names(domain: str, kind: str) -> set:
    return {_nfc(k) for k in (_ledger_read(domain).get(kind) or {})}


def _ledger_unmark(domain: str, kind: str, name: str) -> None:
    p = _ledger_path(domain)
    doc = _ledger_read(domain)
    bucket = doc.get(kind) or {}
    hits = [k for k in bucket if _nfc(k) == _nfc(name)]
    if not hits:
        return
    for k in hits:
        bucket.pop(k, None)
    try:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        logger.warning(f"[upload] 업로드 원장 갱신 실패({p}): {e}")


def _guard_preexisting(domain: str, kind: str, name: str, force: bool) -> None:
    if force:
        return
    if _nfc(name) in _ledger_names(domain, kind):
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"'{name}' 은 이 세션에서 업로드한 파일이 아니라 "
            f"'{domain}' 폴더에 원래 있던 배포 원본입니다. "
            f"정말 지우려면 force=true 로 다시 보내세요."
        ),
    )


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def _disk_match(d: Path, name: str) -> Optional[Path]:
    p = d / name
    if p.is_file():
        return p
    if not d.is_dir():
        return None
    for f in d.iterdir():
        if f.is_file() and _nfc(f.name) == name:
            return f
    return None


def _safe_name(filename: Optional[str]) -> str:
    raw = (filename or "").strip()
    name = _nfc(os.path.basename(raw.replace("\\", "/")))
    if not name or name in (".", ".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"파일명이 잘못됐습니다: {filename!r}",
        )
    return name


async def _save_stream(upload: UploadFile, dest: Path) -> tuple[int, str]:
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
    if explicit and explicit.strip():
        return explicit.strip(), "request"

    reviewed = (
        Path(str(DATA_ROOT)) / "step1_output" / f"{domain}_audit_result_reviewed.json"
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
            f"STEP1 감리(`{reviewed.name}`)가 아직 없으면 facility_type 을 함께 보내세요."
        ),
    )


def _is_extract_cache(name: str) -> bool:
    if not name.endswith(".txt"):
        return False
    stem_ext = os.path.splitext(name[: -len(".txt")])[1].lower()
    return stem_ext in EXTRACTORS


class DomainItem(BaseModel):
    domain: str
    root: str = Field("upload")
    law_files: int
    data_files: int
    has_audit_reviewed: bool
    has_fixture: bool
    preexisting_files: int = 0


class RegulationItem(BaseModel):
    filename: str
    size: int
    chunks_in_vector_db: int
    text_ready: bool
    source: str = "upload"
    deletable: bool = True


def _domain_item(name: str, p: dict, root: str) -> DomainItem:
    try:
        law = (
            [
                f
                for f in os.listdir(p["law"])
                if os.path.splitext(f)[1].lower() in LAW_EXTENSIONS
                and not _is_extract_cache(f)
            ]
            if os.path.isdir(p.get("law", ""))
            else []
        )
        data = list_dataset_files(p["data"]) if os.path.isdir(p.get("data", "")) else []
        reviewed = (
            Path(str(DATA_ROOT)) / "step1_output" / f"{name}_audit_result_reviewed.json"
        )
        mine_data = _ledger_names(name, "data") if root == "upload" else set()
        mine_law = _ledger_names(name, "law") if root == "upload" else set()
        has_fix = False
        try:
            has_fix = fixture_blocker(name) is None
        except Exception:
            has_fix = False

        return DomainItem(
            domain=name,
            root=root,
            law_files=len(law),
            data_files=len(data),
            has_audit_reviewed=reviewed.is_file(),
            has_fixture=has_fix,
            preexisting_files=(
                sum(1 for f in data if _nfc(os.path.basename(f)) not in mine_data)
                + sum(1 for f in law if _nfc(f) not in mine_law)
            ),
        )
    except Exception as e:
        logger.exception(f"[upload] _domain_item 처리 실패 ({name}): {e}")
        return DomainItem(
            domain=name,
            root=root,
            law_files=0,
            data_files=0,
            has_audit_reviewed=False,
            has_fixture=False,
            preexisting_files=0,
        )


def _chunk_counts(vector_db, domain: str) -> dict[str, int]:
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


def _dataset_map(data_dir: str) -> dict[str, str]:
    return {
        f"{i:02d}": os.path.basename(p)
        for i, p in enumerate(list_dataset_files(data_dir), 1)
    }


def _renumbered(
    before: dict[str, str], after: dict[str, str]
) -> list[dict[str, str]]:
    return [
        {"dataset_id": did, "before": before[did], "after": after[did]}
        for did in sorted(after)
        if did in before and before[did] != after[did]
    ]


async def _redis_put(redis: aioredis.Redis, domain: str, name: str, meta: dict) -> None:
    key = _REDIS_KEY.format(domain=domain)
    await redis.hset(key, name, json.dumps(meta, ensure_ascii=False))
    await redis.expire(key, _REDIS_TTL_SEC)
