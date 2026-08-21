import logging
import shutil
from pathlib import Path
from typing import List

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_redis
from app.config import USER_INPUT_ROOT, domain_paths, user_domain_paths
from app.services import user_input_pruner
from app.api.v1.upload.services import (
    DomainItem,
    _domain_item,
    _known_domains,
    _preset_domains,
    _REDIS_KEY,
)

logger = logging.getLogger("uvicorn.error")

router = APIRouter()


@router.get("/domains", response_model=List[DomainItem])
async def list_domains(
    root: str = Query("all"),
):
    if root not in ("all", "upload", "preset"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"root 는 all·upload·preset 중 하나여야 합니다: {root!r}",
        )

    uploads = _known_domains()
    out: List[DomainItem] = []
    if root in ("all", "upload"):
        out += [_domain_item(n, user_domain_paths(n), "upload") for n in uploads]
    if root in ("all", "preset"):
        seen = set(uploads) if root == "all" else set()
        out += [
            _domain_item(n, domain_paths(n), "preset")
            for n in _preset_domains()
            if n not in seen
        ]
    return out


@router.delete("/domains/{domain}")
async def reset_domain(
    domain: str,
    redis: aioredis.Redis = Depends(get_redis),
):
    if not domain or Path(domain).name != domain or domain in (".", ".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"도메인 이름이 잘못됐습니다: {domain!r}",
        )
    folder = Path(str(USER_INPUT_ROOT)) / domain
    if not folder.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"업로드 도메인이 없습니다: {domain} · 현재 도메인: {_known_domains()}",
        )

    live, _ = user_input_pruner._run_facts(domain)
    if live:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{domain}' 으로 진행 중인 run 이 있습니다(queued·running·awaiting_hitl)."
            ),
        )

    files = [f for f in folder.rglob("*") if f.is_file()]
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            pass

    try:
        chunks = user_input_pruner.drop_vector_chunks(domain)
    except Exception as e:
        logger.exception(f"[upload] {domain} 벡터 청크 삭제 실패")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"'{domain}' 의 벡터 청크를 지우지 못했습니다: {e}",
        )
    try:
        shutil.rmtree(folder)
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"'{domain}' 폴더를 지우지 못했습니다: {e}",
        )

    redis_removed = bool(await redis.delete(_REDIS_KEY.format(domain=domain)))

    return {
        "status": "success",
        "domain": domain,
        "files_removed": len(files),
        "bytes_removed": total,
        "vector_chunks_removed": chunks,
        "redis_index_removed": redis_removed,
    }
