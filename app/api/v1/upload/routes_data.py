import json
import os
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import List

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.api.deps import get_redis
from app.services.gam2_profile import DATA_EXTENSIONS
from app.api.v1.upload.services import (
    DATA_UPLOAD_EXTENSIONS,
    _dirs,
    _disk_match,
    _guard_preexisting,
    _ledger_mark,
    _ledger_names,
    _ledger_read,
    _ledger_unmark,
    _nfc,
    _redis_put,
    _renumbered,
    _safe_name,
    _save_stream,
    _dataset_map,
    _REDIS_KEY,
)

router = APIRouter()


@router.post("/data")
async def upload_data(
    domain: str = Form(...),
    files: List[UploadFile] = File(...),
    create_domain: bool = Form(False),
    redis: aioredis.Redis = Depends(get_redis),
):
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
                detail=f"'{name}' 은 감리 대상 데이터가 아닙니다. 허용: {', '.join(DATA_UPLOAD_EXTENSIONS)}",
            )
        if name.startswith(("_", ".")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{name}' 처럼 '_'·'.' 로 시작하는 파일은 허용되지 않습니다.",
            )

        twin = _disk_match(data_dir, name)
        dest = data_dir / name
        replaced = twin is not None
        size, sha = await _save_stream(up, dest)
        old_encoding_removed = False
        if twin is not None and twin.name != name:
            twin.unlink()
            await redis.hdel(_REDIS_KEY.format(domain=domain), twin.name)
            old_encoding_removed = True

        meta = {
            "filename": name,
            "domain": domain,
            "ext": ext,
            "size": size,
            "sha256": sha,
            "is_dataset": ext in DATA_EXTENSIONS,
            "replaced": replaced,
            "old_encoding_removed": old_encoding_removed,
            "uploaded_at": now,
            "path": str(dest),
        }
        await _redis_put(redis, domain, name, meta)
        _ledger_mark(domain, "data", name, meta)
        reports.append(meta)

    after = _dataset_map(str(data_dir))
    renumbered = _renumbered(before, after)

    by_name = {v: k for k, v in after.items()}
    key = _REDIS_KEY.format(domain=domain)
    for name, raw in (await redis.hgetall(key)).items():
        try:
            m = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if m.get("dataset_id") != by_name.get(name):
            m["dataset_id"] = by_name.get(name)
            await _redis_put(redis, domain, name, m)

    return {
        "ok": True,
        "domain": domain,
        "saved_to": str(data_dir),
        "files": reports,
        "dataset_count": len(after),
        "dataset_map": after,
        "renumbered": renumbered,
        "warning": (
            "dataset_id 가 바뀌었습니다. 이미 감리·정제를 돌렸다면 재프로파일 → 재감리가 필요합니다."
        )
        if renumbered
        else None,
    }


@router.get("/data")
async def list_data(
    domain: str = Query(...),
    redis: aioredis.Redis = Depends(get_redis),
):
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
        _nfc(p.name): p
        for p in data_dir.iterdir()
        if p.is_file() and os.path.splitext(p.name)[1].lower() in DATA_UPLOAD_EXTENSIONS
    }
    dataset_map = {k: _nfc(v) for k, v in _dataset_map(str(data_dir)).items()}
    by_name = {v: k for k, v in dataset_map.items()}
    ledger_data = {_nfc(k): v for k, v in (_ledger_read(domain).get("data") or {}).items()}

    stale = [n for n in cached if _nfc(n) not in on_disk]
    legacy = [n for n in cached if n not in stale and _nfc(n) != n]
    if stale or legacy:
        await redis.hdel(key, *stale, *legacy)
    cached = {_nfc(n): m for n, m in cached.items() if n not in stale}

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
        if name in ledger_data:
            m["source"] = "upload"
            m.setdefault("sha256", ledger_data[name].get("sha256"))
            m.setdefault("uploaded_at", ledger_data[name].get("uploaded_at"))
        else:
            m["sha256"] = m.get("sha256")
            m["uploaded_at"] = m.get("uploaded_at")
            m["source"] = "preexisting"
        m["deletable"] = m["source"] == "upload"
        items.append(m)
        await _redis_put(redis, domain, name, m)

    return {
        "domain": domain,
        "data_dir": str(data_dir),
        "dataset_count": len(dataset_map),
        "dataset_map": dataset_map,
        "redis_key": key,
        "redis_stale_removed": stale,
        "redis_legacy_encoding_rewritten": legacy,
        "files": items,
    }


@router.delete("/data/{filename}")
async def delete_data(
    filename: str,
    domain: str = Query(...),
    force: bool = Query(False),
    redis: aioredis.Redis = Depends(get_redis),
):
    paths = _dirs(domain)
    data_dir = Path(paths["data"])
    name = _safe_name(urllib.parse.unquote(filename))
    target = _disk_match(data_dir, name)

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{domain}' 에 데이터 파일이 없습니다: {name}",
        )

    _guard_preexisting(domain, "data", name, force)
    before = _dataset_map(str(data_dir))
    target.unlink()
    _ledger_unmark(domain, "data", name)
    after = _dataset_map(str(data_dir))
    await redis.hdel(_REDIS_KEY.format(domain=domain), name, target.name)

    renumbered = _renumbered(before, after)
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
