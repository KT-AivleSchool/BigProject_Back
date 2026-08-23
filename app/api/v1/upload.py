# -*- coding: utf-8 -*-
"""업로드 API — 조례 문서 · 감리용 데이터 파일.

2026-08-09 재작성. 이전 판은 파일을 `uploads/regulations/` 에 모아두었는데
**파이프라인이 읽는 곳이 아니다**. 그래서 올려도 STEP1 감리가 못 봤고,
벡터 DB 에는 `{"source","filename"}` 두 키만 붙어 시설 구분도 안 됐다.
경로 조작도 열려 있었다(`os.path.join(UPLOAD_DIR, file.filename)`).

지금 규약
  조례   → `datasets/<도메인>/law/`   ... `load_ordinance()` 가 읽는 바로 그 폴더
  데이터 → `datasets/<도메인>/data/`  ... `profile_folder()` 가 읽는 바로 그 폴더
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
  **그 도메인 콜렉션**(`statutes_<도메인>`)에 넣는다. 데이터팀 시드
  (`ingest_statutes.py`)와 **같은 파서**다 — 다른 파서를 쓰면 같은 조례가 청크
  모양이 달라져 검색 결과가 적재 경로에 따라 갈린다.
  🔴 콜렉션이 도메인마다 따로라 **격리는 구조가 맡는다.** `facility_type` 은
     격리 키가 아니라 사람이 보는 부가정보이고, 몰라도 업로드가 막히지 않는다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import unicodedata
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
# 🔴 업로드가 쓰는 루트는 **`USER_INPUT_ROOT`**(`datasets/user_input`)다.
#    `DOMAIN_ROOT`(`datasets`)는 프리셋 자리이고, 여기서 그걸 쓰면 사용자가 「흡연」을
#    치는 순간 배포 원본이 목록에 뜨고 삭제 버튼이 붙는다 — 2026-08-13 에 실제로
#    `datasets/흡연/data` 536MB 가 그렇게 지워졌다.
#    `DATA_ROOT` 는 `step1_output` **두 자리에만** 쓴다 — 그건 도메인 폴더가 아니라
#    도메인 무관 공용 산출물이라 사용자 루트로 따라가면 안 된다.
from app.config import (
    DATA_ROOT,
    DOMAIN_ROOT,
    USER_INPUT_ROOT,
    USER_INPUT_SUBDIR,
    domain_paths,
    user_domain_paths,
)
from app.core.data_pipeline.statute_parser import extract_doc_meta, parse_statute
from app.core.sim_ai.vector_db import get_vector_db, statutes_collection_name
from app.services.gam2_doc_extract import EXTRACTORS, TEXT_EXT, extract_text
from app.services.gam2_ordinance_select import (
    has_siting_provision,
    is_regulatory,
    split_articles,
)
from app.services.gam2_profile import DATA_EXTENSIONS, list_dataset_files

# 초기화 버튼과 24시간 자동 정리는 **같은 판정·같은 삭제**를 쓴다. 여기서 따로 짜면
# 손으로 지운 것과 자동으로 지워진 것이 서로 다른 잔재를 남긴다.
from app.services import user_input_pruner

# 도메인 이름 검증은 파이프라인 러너와 **같은 함수**를 쓴다. 여기서 다시 짜면
# 한쪽만 고쳐졌을 때 업로드는 통과하는데 실행은 400 이 되는 상태가 생긴다.
from app.services.pipeline_runner import (
    MODE_FULL,
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

# 🔴 **TTL 을 반드시 준다**(2026-08-11). compose 가 `--maxmemory-policy volatile-lru` 라
#    **TTL 있는 키만** evict 대상이다 — 무TTL 로 넣으면 한도에 닿는 순간 Redis 가
#    evict 할 것을 못 찾아 **모든 쓰기를 OOM 으로 거절**한다. 그때 같이 죽는 건 이
#    색인이 아니라 **로그인(refresh 토큰·블랙리스트·잠금 카운터)과 토론 SSE 중계**다.
#    (CLAUDE.md 함정표 「무TTL 키가 캐시 정책을 죽인다」)
#
#    30일은 "이 색인이 30일치만 유효하다"는 뜻이 **아니다.** 쓸 때도 읽을 때도
#    (`list_data` 가 항목마다 `_redis_put` 을 다시 부른다) 갱신되므로 쓰이는 동안은
#    안 만료된다 — 길이보다 **evict 대상이 되는 것 자체**가 요점이다.
#    잃어도 되는 값이라서 준다: **정본은 디스크**이고 `list_data` 가 매번 대조한다.
#    다만 사라지면 `sha256`·`uploaded_at` 은 복원이 안 되므로 `None` 으로 나간다
#    (지어내지 않는다 — 원칙 4).
#
#    🔴 TTL 이 없으면 **아무도 못 닿는 키가 영구히 남는다.** 도메인 폴더를 API 밖에서
#    지우면(정리 커밋·손삭제) 색인만 남는데, `_dirs()` 가 폴더 없는 도메인에 먼저 400 을
#    내므로 `list_data` 도 `delete_data` 도 그 키에 닿지 못한다 — 지울 코드가 없고
#    evict 대상도 아닌 키다. 실제로 셋 있었다(`흡연_E2E`·`흡연_E2E2`·`흡연업로드`,
#    커밋 99b9121 이 폴더를 지웠다) → 2026-08-11 수동 삭제. TTL 이 있으면 자연히 걷힌다.
_REDIS_TTL_SEC = 30 * 24 * 3600


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

    root = Path(str(USER_INPUT_ROOT)) / domain
    if create and not root.is_dir():
        for sub in ("data", "law"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        logger.info(f"[upload] 새 도메인 폴더 생성: {root}")

    try:
        # 🔴 `MODE_FULL` 을 **명시**한다. 기본값(프리셋 루트)으로 두면 「흡연」이
        #    프리셋 폴더가 있다는 이유로 통과하고, 정작 업로드는 빈 user_input 에 쌓인다.
        _validate_domain(domain, MODE_FULL)
    except RunRequestError as e:
        # 오타 하나로 새 도메인이 조용히 생기면 안 된다 — 생성은 명시적 의사표시로만.
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
    """**업로드** 도메인 — `datasets/user_input/` 아래 (= data/ 또는 law/ 를 가진 것).

    🔴 프리셋(`datasets/흡연` 등)은 **여기 안 뜬다.** 그게 이 분리의 목적이다.
       업로드·삭제가 닿는 범위는 오직 이 목록이다.
    """
    root = Path(str(USER_INPUT_ROOT))
    if not root.is_dir():
        return []
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and ((p / "data").is_dir() or (p / "law").is_dir())
    )


def _preset_domains() -> List[str]:
    """**프리셋** 도메인 — 배포 원본이 깔린 `datasets/<도메인>`.

    🔴 목록에만 쓴다. 업로드·삭제는 이 루트에 **닿지 않는다**(`_dirs` 는
       `USER_INPUT_ROOT` 만 본다) — 이름이 보이는 것과 손이 닿는 것은 다르다.
    ⚠ `user_input` 자신은 뺀다. `USER_INPUT_ROOT` 가 `DATA_ROOT` **안**에 있어서
      한 칸 아래를 훑으면 자기 자신이 도메인처럼 걸린다.
    """
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


# ── 업로드 원장 (누가 올린 파일인지 **디스크에** 남긴다) ──────────────────
#
# 🔴 **왜 Redis 로 안 되나.** `source` 를 「Redis 색인에 sha256 이 있나」로 판정하면
#    TTL(30일)이 지나거나 `volatile-lru` 가 걷어낸 순간 **사용자가 올린 파일이
#    「폴더에 있던 것」으로 바뀐다.** 아래 삭제 문지기가 그 값을 보고 판단하므로
#    판정이 뒤집히면 문지기도 같이 뒤집힌다 — 안 터지고 **지워진다**.
#    정본은 디스크라고 이미 화면에 적혀 있다. 그러면 이 사실도 디스크에 있어야 한다.
#
# 🔴 실제로 이 구멍으로 `datasets/흡연/data` 가 통째로 지워졌다(2026-08-13 제보).
#    프리셋 도메인 이름을 업로드 화면에 적으면 그 폴더의 **배포 원본**이 목록에
#    뜨고 삭제 버튼이 그대로 붙어 있었다.
_LEDGER_NAME = ".upload_ledger.json"


def _ledger_path(domain: str) -> Path:
    return Path(str(USER_INPUT_ROOT)) / domain / _LEDGER_NAME


def _ledger_read(domain: str) -> dict:
    """`{"data": {파일명: {...}}, "law": {...}}`. 없거나 깨졌으면 빈 원장.

    깨졌을 때 `raise` 하지 않는 이유: 이 원장은 **보호를 더하는** 기록이고,
    없으면 전부 `preexisting` = **삭제가 막히는 쪽**으로 실패한다. 안전한 방향이다.
    """
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
    """업로드 성공을 원장에 적는다. 실패해도 업로드 자체는 되돌리지 않는다."""
    p = _ledger_path(domain)
    doc = _ledger_read(domain)
    bucket = doc.setdefault(kind, {})
    # 같은 이름의 옛 표기(NFD) 키는 걷어낸다 — 남겨두면 원장에 한 파일이 두 줄로
    # 적혀, 지운 뒤에도 한쪽이 남아 「올린 적 있는 파일」로 계속 읽힌다.
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
        # 원장이 안 써지면 그 파일은 다음 조회에서 `preexisting` 이 된다 —
        # 사용자에겐 「내가 올렸는데 삭제가 막힌다」로 보인다. 조용히 넘기지 않는다.
        logger.warning(f"[upload] 업로드 원장 기록 실패({p}): {e} — '{name}' 은 삭제가 막힙니다")


def _ledger_names(domain: str, kind: str) -> set:
    """원장에 적힌 이름들 — **NFC 로 맞춰서** 돌려준다.

    이 변경(2026-08-14) 전에 적힌 키는 NFD 일 수 있다. 비교를 원문으로 하면
    내가 올린 파일이 배포 원본으로 보여 삭제가 409 로 막힌다(`_nfc` 주석).
    """
    return {_nfc(k) for k in (_ledger_read(domain).get(kind) or {})}


def _ledger_unmark(domain: str, kind: str, name: str) -> None:
    p = _ledger_path(domain)
    doc = _ledger_read(domain)
    bucket = doc.get(kind) or {}
    # 옛 NFD 키도 **같이** 지운다 — 하나만 지우면 남은 쪽이 「올린 적 있는 파일」로
    # 계속 읽혀, 나중에 같은 이름의 배포 원본이 놓였을 때 삭제가 안 막힌다.
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
    """배포 원본(사용자가 안 올린 파일)은 `force=true` 없이는 못 지운다.

    🔴 「지우겠다」와 「남이 깔아둔 것을 지우겠다」는 **다른 손짓**이다
       (CLAUDE.md — `--yes` 와 `--force`). 합치면 평소 삭제와 파괴적 삭제가
       구분되지 않는다. 프리셋 도메인(`흡연`·`재활용`)의 `data/` 는 지우면
       그 도메인 자체가 못 돌고, **다시 만들 방법이 저장소에 없다**
       (`.gitignore` 대상이라 clone 에도 안 들어온다).
    """
    if force:
        return
    if _nfc(name) in _ledger_names(domain, kind):
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"'{name}' 은 이 세션에서 업로드한 파일이 아니라 "
            f"'{domain}' 폴더에 원래 있던 배포 원본입니다. "
            f"프리셋 모드가 이 파일을 읽으므로 지우면 해당 도메인이 못 돕니다. "
            f"(.gitignore 대상이라 다시 받을 수 없습니다) "
            f"정말 지우려면 force=true 로 다시 보내세요."
        ),
    )


def _nfc(s: str) -> str:
    """한글 파일명을 **NFC 로 합친다**(자모 분리 해소).

    🔴 macOS 에서 고른 파일은 브라우저가 이름을 **NFD** 로 준다 — `역` 이
       `ㅇ+ㅕ+ㄱ` 세 코드포인트다(`서울시 역사마스터 정보.csv`, 2026-08-13 실물).
       화면에는 똑같이 보이는데 Windows·Linux 는 **다른 파일명**으로 친다.
       그래서 나는 증상이 전부 「안 터지고 값만 틀린다」다:
         · 같은 파일을 다시 올리면 목록에 **두 줄**이 되고, 한쪽 이름으로 지우면
           다른 쪽이 남는다(덮어쓰기가 안 걸린다).
         · 원장 키와 디스크 이름이 갈려 내가 올린 파일이 **배포 원본**으로 보이고
           삭제가 409 로 막힌다.
         · 파이프라인 쪽이 제일 나쁘다 — 감리가 부르는 이름과 프로파일 키가 갈려
           그 데이터셋이 **조용히 빠진다**(`no_profile`).
       비교는 전부 NFC 로 하고, **디스크에 새로 쓰는 이름도 NFC** 로 고정한다.
    """
    return unicodedata.normalize("NFC", s or "")


def _disk_match(d: Path, name: str) -> Optional[Path]:
    """`name`(NFC)에 해당하는 **디스크의 실물**. 없으면 `None`.

    🔴 정규화한 이름으로 곧바로 `unlink` 하면 **이 변경 전에 올라간 NFD 파일을
       못 지운다** — 목록엔 뜨는데 삭제가 404 다. 이름은 NFC 로 **비교**하되
       손대는 것은 디스크에 실제로 있는 그 이름이어야 한다.
    """
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
    """업로드 파일명에서 **경로 성분을 제거**하고 NFC 로 맞춘다.

    `os.path.join(dir, file.filename)` 은 `../` 나 절대경로를 그대로 받아들인다.
    파일명은 이름이지 경로가 아니다.

    🔴 정규화를 **여기 한 곳**에서 한다. 업로드·삭제·목록이 전부 이 함수를 지나므로
       저장하는 이름과 지우는 이름이 갈릴 수가 없다. 저장 쪽만 고치면 옛 이름으로
       들어온 삭제가 404 가 된다(`_nfc` 주석 참조).
    """
    raw = (filename or "").strip()
    name = _nfc(os.path.basename(raw.replace("\\", "/")))
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


def _resolve_facility_type(
    domain: str, explicit: Optional[str]
) -> tuple[Optional[str], str]:
    """청크 메타데이터에 적을 `facility_type` 과 그 **출처**를 정한다.

    하드코딩하지 않는다. `parse_statute` 의 기본값이 `"흡연부스"` 라 그대로 두면
    어느 도메인을 올려도 흡연부스 조례가 된다 — 안 터지고 값만 틀린다.

    우선순위
      1) 요청이 직접 준 값                     source=request
      2) STEP1 감리 확정본의 facility_inference source=audit_reviewed
      둘 다 없으면 `(None, "unknown")`. **추측해서 채우지 않는다**(원칙 1·5).

    🔴 2026-08-24. 예전엔 둘 다 없으면 **400 으로 업로드를 막았다.** 조례 콜렉션이
       `statutes_collection` 하나뿐이라 이 태그가 곧 격리 키였고, 검색이 이 값과
       **정확일치**로 걸러서 한 글자만 달라도 근거가 전량 0건이 됐기 때문이다.
       콜렉션을 도메인마다 나눈 뒤로 격리는 **구조**가 맡는다 — 이 태그는
       사람이 보는 부가정보로 강등됐고, 몰라서 업로드를 막을 이유가 없어졌다.
       화면1 의 「시설 유형」 칸이 필수였던 이유가 이것뿐이었다.
    """
    if explicit and explicit.strip():
        return explicit.strip(), "request"

    # `step1_output` 은 도메인 폴더가 아니라 공용 산출물이다 → `DATA_ROOT` 아래.
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

    return None, "unknown"


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
    root: str = Field(
        "upload",
        description=(
            "이 도메인이 어느 루트에 있는가. "
            "`upload` = `datasets/user_input/<도메인>` — 업로드·삭제가 닿는 곳. "
            "`preset` = `datasets/<도메인>` 배포 원본 — **목록에만 뜬다.** "
            "프리셋 행에 업로드하려면 같은 이름의 업로드 도메인을 새로 만들어야 하고"
            "(`create_domain=true`), 그 둘은 **서로 다른 폴더**다"
        ),
    )
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
    preexisting_files: int = Field(
        0,
        description=(
            "업로드 원장에 없는 파일 수(data+law) = **배포 원본**. "
            "0 보다 크면 이 도메인은 이미 쓰이고 있는 폴더이고, 업로드 화면에서 "
            "고르면 그 원본이 같이 목록에 뜬다. 삭제는 force=true 없이는 409 다"
        ),
    )


def _domain_item(name: str, p: dict, root: str) -> DomainItem:
    """도메인 한 줄. `p` 는 **어느 루트의 경로 묶음인지** 호출자가 정한다.

    🔴 원장(`_ledger_read`)은 `user_input` 아래에만 있다. 프리셋 행은 원장이 없으므로
       모든 파일이 `preexisting_files` 로 잡힌다 — 그게 맞다. 프리셋의 파일은 전부
       배포 원본이고 업로드 API 로 지울 수 없다(애초에 그 루트에 닿지 않는다).
    """
    law = (
        [
            f
            for f in os.listdir(p["law"])
            if os.path.splitext(f)[1].lower() in LAW_EXTENSIONS
            and not _is_extract_cache(f)
        ]
        if os.path.isdir(p["law"])
        else []
    )
    data = list_dataset_files(p["data"]) if os.path.isdir(p["data"]) else []
    reviewed = (
        Path(str(DATA_ROOT)) / "step1_output" / f"{name}_audit_result_reviewed.json"
    )
    # 🔴 양쪽 다 NFC 로 맞춰서 뺀다. 디스크 이름은 원문(옛 NFD 가능)이고 원장 키도
    #    적힌 시점에 따라 갈린다 — 한쪽만 정규화하면 내가 올린 파일이 배포 원본으로
    #    잡혀 삭제가 409 로 막힌다(`_nfc` 주석).
    mine_data = _ledger_names(name, "data") if root == "upload" else set()
    mine_law = _ledger_names(name, "law") if root == "upload" else set()
    return DomainItem(
        domain=name,
        root=root,
        law_files=len(law),
        data_files=len(data),
        has_audit_reviewed=reviewed.is_file(),
        has_fixture=fixture_blocker(name) is None,
        preexisting_files=(
            sum(1 for f in data if _nfc(os.path.basename(f)) not in mine_data)
            + sum(1 for f in law if _nfc(f) not in mine_law)
        ),
    )


@router.get("/domains", response_model=List[DomainItem])
async def list_domains(
    root: str = Query(
        "all",
        description=(
            "어느 루트를 볼 것인가 — `all`(기본) · `upload` · `preset`. "
            "🔴 기본이 `all` 인 이유: 화면1 프리셋 카드가 **이 엔드포인트 하나**로 "
            "목록을 만든다. 좁히면 프리셋 경로가 통째로 빈다"
        ),
    ),
):
    """도메인 목록. 업로드 API 는 전부 domain 이 필수다.

    🔴 **루트가 둘이다**(2026-08-14). 업로드는 `datasets/user_input/<도메인>` 에 쌓이고
       프리셋(배포 원본)은 `datasets/<도메인>` 에 있다. 같은 이름이어도 **다른 폴더**다 —
       그래서 각 행에 `root` 를 같이 준다. 프리셋 행은 **목록에만** 있다: 업로드도 삭제도
       그 루트에 닿지 않는다(`_dirs` 는 `USER_INPUT_ROOT` 만 본다).
       이름이 겹치면 **업로드 쪽만** 내보낸다 — 업로드 화면이 보는 폴더가 그쪽이고,
       프리셋 개수를 거기 얹으면 「내가 올린 파일」로 읽힌다(2026-08-13 삭제 사고).

    `has_fixture` 는 **러너 자신의 사전검사**로 판정한다
    (`pipeline_runner.fixture_blocker` → `build_commands` → `_load_fixture`).
    여기서 `Path(f"{name}_FIX").is_dir()` 같은 자체 판정식을 쓰면 안 된다 —
    실제 조건은 폴더가 아니라 **파일 둘**이라, 폴더만 보면 "가능" 이라 답해놓고
    실행이 400 으로 죽는다(프런트가 카드 단계에서 막지 못한다).
    ⚠ 픽스처(`<도메인>_FIX`)는 프리셋 루트에 있고 **행의 root 와 무관하다** —
      업로드 도메인이 우연히 같은 이름이면 `has_fixture: true` 가 될 수 있다.

    막힌 **이유**는 응답에 넣지 않는다. 이유 문자열에 저장소 절대경로가 들어가는데,
    `GET /pipeline/runs/{id}/log` 는 그 경로를 `<repo>` 로 마스킹해서 내보낸다 —
    한쪽만 원문으로 내보내면 마스킹이 무의미해진다.
    """
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
            _domain_item(n, domain_paths(str(Path(str(DOMAIN_ROOT)) / n)), "preset")
            for n in _preset_domains()
            if n not in seen
        ]
    return out


# ── 초기화 버튼 — 이 도메인을 통째로 지운다 ────────────────────────────────
@router.delete("/domains/{domain}")
async def reset_domain(
    domain: str,
    redis: aioredis.Redis = Depends(get_redis),
):
    """업로드 도메인 폴더를 **통째로** 지운다(data·law·원장 전부).

    자동 정리(24시간)를 기다리지 않고 지금 비우는 경로다 — 지우는 것도 판정 기준도
    `user_input_pruner` 와 **같다**. 여기서 따로 짜면 손으로 누른 삭제와 자동 삭제가
    다른 것을 남긴다.

    🔴 프리셋(`datasets/흡연`)에는 닿지 않는다. 이 라우터가 보는 루트는
       `datasets/user_input/` 뿐이라 **경로상 불가능**하다 — 문지기가 아니라 자리다.
    🔴 파일보다 **벡터 청크를 먼저** 지운다. 순서가 반대면 「파일은 없는데 검색에는
       잡히는」 상태가 남고, 폴더가 없어져 자동 정리의 계획에도 안 뜬다.
       청크 삭제가 실패하면 폴더도 안 지우고 **500** 이다 — 반쯤 지우고 200 을 주면
       그 도메인의 조문이 같은 시설을 쓰는 **다른 실행의 토론에 계속 인용된다**.
    """
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

    # 돌고 있는 run 의 입력을 발밑에서 지우지 않는다. 판정은 정리기와 **같은 함수**다.
    live, _ = user_input_pruner._run_facts(domain)
    if live:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{domain}' 으로 진행 중인 run 이 있습니다"
                f"(queued·running·awaiting_hitl). "
                f"끝나거나 취소된 뒤에 다시 시도하세요 — "
                f"지금 지우면 그 run 이 읽는 입력이 사라집니다."
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
        logger.exception(f"[upload] {domain} 벡터 청크 삭제 실패 — 폴더도 남깁니다")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"'{domain}' 의 벡터 청크를 지우지 못해 아무것도 지우지 않았습니다: {e}"
            ),
        )
    try:
        shutil.rmtree(folder)
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"'{domain}' 폴더를 지우지 못했습니다: {e} "
                f"(벡터 청크 {chunks}개는 이미 지워졌습니다 — 다시 올려야 검색됩니다)"
            ),
        )

    # Redis 색인은 여기서 **즉시** 지운다. 자동 정리는 TTL 에 맡기지만, 사람이 초기화를
    # 누른 뒤 목록에 이름이 남아 있으면 「안 지워졌다」로 읽힌다.
    redis_removed = bool(await redis.delete(_REDIS_KEY.format(domain=domain)))

    logger.info(
        f"[upload] 도메인 초기화: {folder} ({total / 2**20:.1f} MB · {len(files)}파일 · "
        f"청크 {chunks}개)"
    )
    return {
        "status": "success",
        "domain": domain,
        "files_removed": len(files),
        "bytes_removed": total,
        "vector_chunks_removed": chunks,
        "redis_index_removed": redis_removed,
    }


# ══════════════════════════════════════════════════════════════════════
# 2. 조례 업로드
# ══════════════════════════════════════════════════════════════════════
class RegulationItem(BaseModel):
    filename: str
    size: int
    text_ready: bool = Field(..., description="텍스트 추출이 끝나 STEP1 이 읽을 수 있는가")
    chunks_in_vector_db: int
    source: str = Field(
        "preexisting", description="upload = 이 도메인에 업로드한 것 · preexisting = 배포 원본"
    )
    deletable: bool = Field(
        False, description="False 면 삭제에 force=true 가 필요하다(배포 원본)"
    )


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
        # 🔴 밖으로 나가는 이름은 **NFC** 다. 프런트가 이 이름을 그대로 DELETE 에
        #    되돌려주는데, 디스크 원문(옛 NFD)을 주면 화면에는 같아 보이는 두 이름이
        #    돌아다닌다. 디스크를 만질 때는 `_disk_match` 가 원문을 되찾는다.
        name = _nfc(raw)
        ready = (
            os.path.splitext(raw)[1].lower() in TEXT_EXT
            or _disk_match(law_dir, raw + ".txt") is not None
        )
        items.append(
            RegulationItem(
                filename=name,
                size=path.stat().st_size,
                # 청크 메타(`upload_filename`)는 적재 시점 표기라 갈릴 수 있다.
                chunks_in_vector_db=counts.get(raw) or counts.get(name) or 0,
                text_ready=ready,
                source="upload" if name in ledger_law else "preexisting",
                deletable=name in ledger_law,
            )
        )
    return items


def _chunk_counts(vector_db, domain: str) -> dict[str, int]:
    """이 도메인에서 올린 조례의 파일별 청크 수 (동기 — to_thread 로 부른다).

    콜렉션 자체가 도메인이라(`statutes_<도메인>`) `cmetadata->>'domain'` 조건은
    없앴다. 남겨두면 **적재 시점에 그 키를 안 적은 청크가 조용히 0건으로 보인다** —
    같은 칸에 있는데도 화면엔 「안 올라갔다」로 뜬다(원칙 4).
    """
    from sqlalchemy import create_engine, text as sql_text

    engine = create_engine(vector_db.connection_string)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sql_text(
                    "SELECT e.cmetadata->>'upload_filename' AS fn, count(*) "
                    "FROM langchain_pg_embedding e "
                    "JOIN langchain_pg_collection c ON c.uuid = e.collection_id "
                    "WHERE c.name = :cname "
                    "GROUP BY 1"
                ),
                {"cname": statutes_collection_name(domain)},
            ).fetchall()
        return {r[0]: r[1] for r in rows if r[0]}
    finally:
        engine.dispose()


@router.post("/regulation")
async def upload_regulation(
    domain: str = Form(..., description="도메인 (예: 흡연). 필수."),
    files: List[UploadFile] = File(...),
    facility_type: Optional[str] = Form(
        None,
        description=(
            "청크 메타데이터에 적을 시설 종류(부가정보). 생략하면 STEP1 감리 확정본에서 "
            "읽고, 그것도 없으면 비워 둔다 — 조례 격리는 도메인 콜렉션이 맡으므로 "
            "이 값이 없어도 업로드·검색에 지장이 없다."
        ),
    ),
    create_domain: bool = Form(False, description="도메인 폴더가 없으면 만든다"),
    ingest: bool = Form(
        True, description="벡터 DB(statutes_<도메인>) 적재 여부"
    ),
):
    """조례 문서 다중 업로드 → `datasets/<도메인>/law/` 저장 + 벡터 DB 적재.

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

        # 🔴 같은 파일인데 **표기만 다른 것**(옛 NFD 이름)이 이미 있을 수 있다.
        #    그냥 저장하면 목록에 두 줄이 되고, 한쪽을 지워도 다른 쪽이 남는다.
        #    새 이름은 NFC 로 쓰되 옛 표기는 이 자리에서 걷는다(`_nfc` 주석).
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
            warnings.append(
                "같은 파일이 옛 표기(자모 분리, NFD)로 있어 함께 정리했습니다 — "
                "화면에는 같은 이름으로 보이지만 저장은 둘이었습니다."
            )

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
        _ledger_mark(domain, "law", name, {"uploaded_at": now, "sha256": sha, "size": size})

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
                if twin is not None and twin.name != name:
                    # 옛 표기로 적재된 청크도 걷는다 — 파일만 지우고 두면 같은 조문이
                    # 두 번 인용된다(`delete_statute_chunks` 를 둔 이유와 같다).
                    removed += await asyncio.to_thread(
                        vector_db.delete_statute_chunks,
                        domain=domain,
                        upload_filename=twin.name,
                    )
                entry["deleted_old_chunks"] = removed

                if chunks:
                    await vector_db.add_statute_chunks(
                        domain,
                        [c.text for c in chunks],
                        metadatas=[c.metadata for c in chunks],
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
    filename: str,
    domain: str = Query(..., description="도메인 (예: 흡연)"),
    force: bool = Query(False, description="배포 원본까지 지운다. 되돌릴 수 없다."),
):
    """조례 파일 + 추출 캐시 + 그 파일에서 나온 벡터 청크를 함께 지운다.

    셋 중 하나만 지우면 남은 쪽이 계속 검색에 잡힌다.

    🔴 업로드 원장에 없는 파일(= 배포 원본)은 `force=true` 없이는 **409** 다.
    """
    paths = _dirs(domain)
    law_dir = Path(paths["law"])
    name = _safe_name(urllib.parse.unquote(filename))
    # 이름은 NFC 로 비교하고 **디스크에 있는 그 이름**을 지운다(`_disk_match` 주석).
    target = _disk_match(law_dir, name)

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{domain}' 에 조례 파일이 없습니다: {name}",
        )

    _guard_preexisting(domain, "law", name, force)   # 지우기 **전에** 막는다
    target.unlink()
    _ledger_unmark(domain, "law", name)
    # 🔴 추출 캐시는 **원본 이름 그대로** 뒤에 `.txt` 를 붙인 것이라, 원본이 NFD 면
    #    캐시도 NFD 다. NFC 이름으로만 찾으면 캐시가 남아 조례를 지워도 본문이
    #    계속 검색에 잡힌다 — 벡터 청크와 같은 이유다.
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


def _renumbered(
    before: dict[str, str], after: dict[str, str]
) -> list[dict[str, str]]:
    """**이미 있던 번호가 다른 파일을 가리키게 된 것**만 고른다. 정의는 여기 하나다.

    경고가 막으려는 위험은 「옛 감리·정제 결과가 참조하는 번호가 이제 딴 파일을
    가리킨다」 하나다. 그래서 판정 기준은 `did in before` 다 — 새로 생긴 번호는
    옛 결과가 참조할 수 없으니 위험이 없다.

    🔴 예전엔 `before.get(did) != after[did]` 하나로 걸렀다. 빈 도메인 첫 업로드는
       `before` 가 `{}` 라 `None != "<파일명>"` 이 전건 참이 되어 **신규 배정이
       전부 「밀림」으로 보고**됐다(2026-08-16 사용자 제보). 업로드 모드의 가장 흔한
       경로라 거의 매번 떴고, 늘 켜져 있는 경고등은 꺼져 있는 것과 같다 — 진짜
       밀림까지 같이 넘기게 된다.
    ⚠ 번호↔파일 대응 자체는 `dataset_map` 이 따로 들고 있다. 여기서 뺀다고
      사실이 사라지지는 않는다.
    """
    return [
        {"dataset_id": did, "before": before[did], "after": after[did]}
        for did in sorted(after)
        if did in before and before[did] != after[did]
    ]


async def _redis_put(redis: aioredis.Redis, domain: str, name: str, meta: dict) -> None:
    """색인 한 필드를 쓰고 **키 TTL 을 갱신한다.**

    쓰기가 여기 한 곳으로 모여야 TTL 을 빠뜨릴 자리가 없다 — `hset` 을 직접 부르면
    그 경로만 무TTL 로 남고, 그건 `volatile-lru` 밑에서 키 하나가 정책 밖에 서는 것이다.
    """
    key = _REDIS_KEY.format(domain=domain)
    await redis.hset(key, name, json.dumps(meta, ensure_ascii=False))
    await redis.expire(key, _REDIS_TTL_SEC)


@router.post("/data")
async def upload_data(
    domain: str = Form(..., description="도메인 (예: 흡연). 필수."),
    files: List[UploadFile] = File(...),
    create_domain: bool = Form(False),
    redis: aioredis.Redis = Depends(get_redis),
):
    """감리(STEP1)에 쓸 데이터 파일 다중 업로드 → `datasets/<도메인>/data/`.

    🔴 **dataset_id 는 파일명 가나다순으로 다시 매겨진다.** 파일을 하나 끼워 넣으면
       뒤 번호가 전부 밀리고, 이미 돌린 `<도메인>_audit_result_reviewed.json` ·
       정제 캐시의 번호와 어긋난다. 그래서 응답에 **번호 변화(renumbered)** 를 담는다.
       조용히 밀리면 감리 결과가 엉뚱한 파일에 붙는다 — 안 터지고 값만 틀린다.
       ⚠ **신규 배정은 밀림이 아니다**(`_renumbered` 참고). 지금 배치는 `dataset_map`
         이 항상 전량 담고 있으므로 `renumbered` 는 위험한 것만 골라 담는다.

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

        # 🔴 같은 파일인데 **표기만 다른 것**(옛 NFD 이름)이 이미 있을 수 있다.
        #    그냥 저장하면 목록에 두 줄이 되고 `dataset_id` 도 하나 더 매겨진다 —
        #    감리가 같은 데이터를 두 번 보게 된다(`_nfc` 주석).
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
            "is_dataset": ext in DATA_EXTENSIONS,  # 부속(.dbf 등)은 dataset 이 아니다
            "replaced": replaced,
            # 「덮어썼다」와 「옛 표기를 걷었다」는 다른 사실이다 — 접으면 목록에서
            # 한 줄이 왜 사라졌는지 설명할 데가 없다(원칙 4).
            "old_encoding_removed": old_encoding_removed,
            "uploaded_at": now,
            "path": str(dest),
        }
        await _redis_put(redis, domain, name, meta)
        _ledger_mark(domain, "data", name, meta)   # 디스크에도 남긴다 — Redis 는 걷힌다
        reports.append(meta)

    after = _dataset_map(str(data_dir))
    renumbered = _renumbered(before, after)

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
            await _redis_put(redis, domain, name, m)  # 직접 hset 하면 TTL 이 안 붙는다

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

    # 🔴 키를 **NFC 로 맞춰서** 잡는다. 디스크 이름·Redis 필드·원장 키가 각각 적힌
    #    시점에 따라 NFD 일 수 있는데, 원문끼리 비교하면 같은 파일이 서로 남남이 된다:
    #    색인이 `stale` 로 지워지고, 내가 올린 파일이 배포 원본으로 보인다(`_nfc` 주석).
    on_disk = {
        _nfc(p.name): p
        for p in data_dir.iterdir()
        if p.is_file() and os.path.splitext(p.name)[1].lower() in DATA_UPLOAD_EXTENSIONS
    }
    dataset_map = {k: _nfc(v) for k, v in _dataset_map(str(data_dir)).items()}
    by_name = {v: k for k, v in dataset_map.items()}
    ledger_data = {_nfc(k): v for k, v in (_ledger_read(domain).get("data") or {}).items()}

    stale = [n for n in cached if _nfc(n) not in on_disk]
    # 옛 표기로 적힌 색인 필드는 걷는다 — 아래에서 NFC 이름으로 다시 쓰므로,
    # 안 걷으면 한 파일이 색인에 두 줄로 남고 다음 조회에서 `stale` 로 오인된다.
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
        # 🔴 `source` 판정은 **디스크 원장**이 정본이다. Redis 색인의 sha256 유무로
        #    판정하면 TTL 이 지난 순간 내가 올린 파일이 「폴더에 있던 것」이 되고,
        #    삭제 문지기도 같이 뒤집힌다. 원장에 있으면 업로드, 없으면 배포 원본.
        if name in ledger_data:
            m["source"] = "upload"
            m.setdefault("sha256", ledger_data[name].get("sha256"))
            m.setdefault("uploaded_at", ledger_data[name].get("uploaded_at"))
        else:
            m["sha256"] = m.get("sha256")
            m["uploaded_at"] = m.get("uploaded_at")
            m["source"] = "preexisting"
        # 화면이 삭제 버튼을 띄울지 정하는 값. 규칙을 프런트가 다시 구현하지 않게 **값으로** 준다.
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
        # 「없어진 파일이라 걷었다」와 「표기만 옛것이라 다시 적었다」는 다른 사실이다.
        "redis_legacy_encoding_rewritten": legacy,
        "files": items,
    }


@router.delete("/data/{filename}")
async def delete_data(
    filename: str,
    domain: str = Query(..., description="도메인 (예: 흡연)"),
    force: bool = Query(False, description="배포 원본까지 지운다. 되돌릴 수 없다."),
    redis: aioredis.Redis = Depends(get_redis),
):
    """데이터 파일 삭제. 남은 파일의 dataset_id 가 어떻게 바뀌는지 같이 알린다.

    🔴 업로드 원장에 없는 파일(= 배포 원본)은 `force=true` 없이는 **409** 다.
       이 문지기가 없던 시절 `datasets/흡연/data` 가 화면의 삭제 버튼으로
       통째로 날아갔다(2026-08-13).
    """
    paths = _dirs(domain)
    data_dir = Path(paths["data"])
    name = _safe_name(urllib.parse.unquote(filename))
    # 이름은 NFC 로 비교하고 **디스크에 있는 그 이름**을 지운다(`_disk_match` 주석).
    target = _disk_match(data_dir, name)

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{domain}' 에 데이터 파일이 없습니다: {name}",
        )

    _guard_preexisting(domain, "data", name, force)   # 지우기 **전에** 막는다
    before = _dataset_map(str(data_dir))
    target.unlink()
    _ledger_unmark(domain, "data", name)
    after = _dataset_map(str(data_dir))
    # 색인은 두 표기를 같이 지운다 — 옛 항목이 NFD 로 적혀 있으면 NFC 로만 지울 때
    # 남아서 「없는 파일」이 계속 조회된다.
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
