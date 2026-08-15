import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

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
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.config import BASE_DIR
from app.db.base import Post, User
from app.schemas.post import PostListItem, PostListResponse, PostResponse


router = APIRouter()

logger = logging.getLogger("uvicorn.error")

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB 제한

# 🔴 목록 조회의 열거형 인자. **모르는 값은 조용히 무시하지 않는다**(원칙 1).
#    예전엔 `if/elif` 만 있고 `else` 가 없어서 `search_type` 에 오타가 나면
#    검색 조건이 통째로 빠진 채 **200 + 전체 목록**이 나갔다 — 프런트에는
#    「검색 결과가 이만큼」으로 보인다. 안 터지고 값만 틀린다.
_SEARCH_TYPES = ("title", "content", "author", "title_content")
_SORT_COLUMNS = ("id", "title", "author_name", "created_at")
_SORT_ORDERS = ("asc", "desc")


def get_upload_dir() -> Path:
    """uploads/posts/YYYY/MM/ 디렉터리 경로 반환 및 생성"""
    now = datetime.now()
    upload_path = (
        BASE_DIR
        / "uploads"
        / "posts"
        / now.strftime("%Y")
        / now.strftime("%m")
    )
    upload_path.mkdir(parents=True, exist_ok=True)
    return upload_path



@router.post("", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=PostResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_post(

    title: str = Form(..., max_length=200),
    content: str = Form(...),
    file: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    🔒 [토큰 필수] 신규 게시글 작성 (제목, 내용 필수 + 1개 이하 첨부파일)
    """
    clean_title = title.strip()
    clean_content = content.strip()

    if not clean_title:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="제목을 입력해주세요.",
        )
    if not clean_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="내용을 입력해주세요.",
        )

    saved_file_path: Optional[str] = None
    orig_filename: Optional[str] = None
    file_size: Optional[int] = None

    if file and file.filename:
        # 파일 용량 및 읽기 검증
        file_bytes = await file.read()
        file_size = len(file_bytes)

        if file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"첨부파일 크기는 최대 20MB를 초과할 수 없습니다. (현재: {file_size / (1024*1024):.1f}MB)",
            )

        orig_filename = file.filename
        ext = os.path.splitext(orig_filename)[1]
        unique_filename = f"{uuid.uuid4().hex}{ext}"

        upload_dir = get_upload_dir()
        target_file_path = upload_dir / unique_filename

        with open(target_file_path, "wb") as f:
            f.write(file_bytes)

        # 상대 경로 저장 (예: uploads/posts/2026/08/xxx.pdf)
        saved_file_path = str(
            target_file_path.relative_to(BASE_DIR)
        )

    new_post = Post(
        user_id=current_user.id,
        title=clean_title,
        content=clean_content,
        file_path=saved_file_path,
        original_filename=orig_filename,
        file_size=file_size,
    )

    db.add(new_post)
    await db.commit()
    await db.refresh(new_post)

    return PostResponse(
        id=new_post.id,
        user_id=new_post.user_id,
        author_name=current_user.username,
        author_email=current_user.email,
        title=new_post.title,
        content=new_post.content,
        has_file=bool(new_post.file_path),
        original_filename=new_post.original_filename,
        file_size=new_post.file_size,
        created_at=new_post.created_at,
        is_owner=True,
    )


@router.get("", response_model=PostListResponse)
@router.get("/", response_model=PostListResponse, include_in_schema=False)
async def list_posts(
    page: int = Query(1, ge=1, description="페이지 번호"),
    limit: int = Query(10, ge=1, le=50, description="페이지당 개수"),
    sort_by: str = Query("created_at", description="정렬 기준 컬럼 (id, title, author_name, created_at)"),
    order: str = Query("desc", description="정렬 방향 (asc, desc)"),
    search_type: str = Query("title_content", description="검색 기준 (title, content, author, title_content)"),
    search_query: Optional[str] = Query(None, description="검색어 키워드"),
    mine: bool = Query(False, description="본인 작성글만 필터링하여 조회할지 여부"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    🔒 [토큰 필수] 게시글 목록 조회 (페이지네이션, 정렬, 키워드 검색 및 본인글 필터 기능)
    """
    offset = (page - 1) * limit

    # 🔴 모르는 값은 거절한다. 422(pydantic `Literal`)가 아니라 **400 + 한 문장**인 이유:
    #    프런트는 에러 `code` 로 분기하지 않고 `detail` 문장을 그대로 띄운다.
    #    422 의 `detail` 은 객체 배열이라 화면에 그대로 못 쓴다.
    #    빈 문자열은 「안 보냈다」로 보고 기본값을 쓴다 — 프런트가 검색을 안 할 때
    #    빈 칸을 실어 보내는 것까지 거절하면 멀쩡하던 호출이 깨진다.
    search_type = (search_type or "").strip() or "title_content"
    sort_by = (sort_by or "").strip() or "created_at"
    order = (order or "").strip().lower() or "desc"

    if search_type not in _SEARCH_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"search_type 은 {', '.join(_SEARCH_TYPES)} 중 하나여야 합니다. (받은 값: {search_type})",
        )
    if sort_by not in _SORT_COLUMNS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"sort_by 는 {', '.join(_SORT_COLUMNS)} 중 하나여야 합니다. (받은 값: {sort_by})",
        )
    if order not in _SORT_ORDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"order 는 {', '.join(_SORT_ORDERS)} 중 하나여야 합니다. (받은 값: {order})",
        )

    # 검색 필터 조건 구성
    where_clauses = []
    if mine:
        where_clauses.append(Post.user_id == current_user.id)

    if search_query and search_query.strip():
        kw = f"%{search_query.strip()}%"
        if search_type == "title":
            where_clauses.append(Post.title.ilike(kw))
        elif search_type == "content":
            where_clauses.append(Post.content.ilike(kw))
        elif search_type == "author":
            where_clauses.append(User.username.ilike(kw))
        elif search_type == "title_content":
            where_clauses.append(or_(Post.title.ilike(kw), Post.content.ilike(kw)))


    # 전체 수 쿼리
    count_stmt = select(func.count(Post.id)).join(User, Post.user_id == User.id)
    if where_clauses:
        count_stmt = count_stmt.where(*where_clauses)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one_or_none() or 0

    # 정렬 컬럼 및 방향 지정 (위에서 값을 이미 검증했다)
    order_column = {
        "id": Post.id,
        "title": Post.title,
        "author_name": User.username,
        "created_at": Post.created_at,
    }[sort_by]

    sort_clause = order_column.asc() if order == "asc" else order_column.desc()

    # 목록 조인 쿼리 (User 테이블과 조인하여 작성자 이름 획득)
    stmt = select(Post, User.username).join(User, Post.user_id == User.id)
    if where_clauses:
        stmt = stmt.where(*where_clauses)

    stmt = stmt.order_by(sort_clause).offset(offset).limit(limit)

    result = await db.execute(stmt)
    rows = result.all()



    items = []
    for post_obj, author_name in rows:
        items.append(
            PostListItem(
                id=post_obj.id,
                user_id=post_obj.user_id,
                author_name=author_name,
                title=post_obj.title,
                has_file=bool(post_obj.file_path),
                created_at=post_obj.created_at,
            )
        )

    return PostListResponse(total=total, page=page, limit=limit, posts=items)


@router.get("/{post_id}", response_model=PostResponse)
async def get_post_detail(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    🔒 [토큰 필수] 게시글 상세 조회
    """
    stmt = (
        select(Post, User.username, User.email)
        .join(User, Post.user_id == User.id)
        .where(Post.id == post_id)
    )
    result = await db.execute(stmt)
    row = result.first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="존재하지 않거나 이미 삭제된 게시글입니다.",
        )

    post_obj, author_name, author_email = row

    return PostResponse(
        id=post_obj.id,
        user_id=post_obj.user_id,
        author_name=author_name,
        author_email=author_email,
        title=post_obj.title,
        content=post_obj.content,
        has_file=bool(post_obj.file_path),
        original_filename=post_obj.original_filename,
        file_size=post_obj.file_size,
        created_at=post_obj.created_at,
        is_owner=(post_obj.user_id == current_user.id),
    )


@router.delete("/{post_id}", status_code=status.HTTP_200_OK)
async def delete_post(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    🔒 [토큰 필수] 게시글 삭제 (작성자 본인만 허용)
    """
    stmt = select(Post).where(Post.id == post_id)
    result = await db.execute(stmt)
    post_obj = result.scalar_one_or_none()

    if not post_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="존재하지 않거나 이미 삭제된 게시글입니다.",
        )

    # 작성자 본인 확인 (또는 is_admin 권한 확장 가능)
    if post_obj.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="자신이 작성한 게시글만 삭제할 수 있습니다.",
        )

    # 🔴 파일을 **행보다 먼저** 지우면 안 된다. commit 이 실패했을 때 행은 남고
    #    파일만 사라져 `has_file: true` 인데 다운로드가 404 「서버에 물리 파일이
    #    존재하지 않습니다」가 된다 — 사용자에겐 「글은 있는데 첨부가 증발」이다.
    #    순서를 뒤집으면 최악이 **주인 없는 파일 하나**이고, 그건 아래 로그에 남는다.
    #    싼 쪽으로 실패하게 둔다.
    file_to_remove = post_obj.file_path

    await db.delete(post_obj)
    await db.commit()

    if file_to_remove:
        full_file_path = BASE_DIR / file_to_remove
        if full_file_path.is_file():
            try:
                full_file_path.unlink()
            except Exception as e:
                # 🔴 print 는 uvicorn 로그로 안 간다(러너가 돌리면 stdout 이 사라진다).
                #    지우다 실패한 파일은 **참조가 끊긴 채 영원히 남는다** —
                #    `runs/` 와 달리 이 폴더엔 정리기도 상한도 없다. 흔적은 남긴다.
                logger.warning(
                    "[posts] 첨부파일 삭제 실패 — 주인 없는 파일이 남는다: "
                    "post_id=%s path=%s err=%s",
                    post_id,
                    file_to_remove,
                    e,
                )

    return {"message": "게시글이 성공적으로 삭제되었습니다.", "post_id": post_id}


@router.get("/{post_id}/download")
async def download_post_file(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    🔒 [토큰 필수] 게시글 첨부파일 안전 다운로드
    """
    stmt = select(Post).where(Post.id == post_id)
    result = await db.execute(stmt)
    post_obj = result.scalar_one_or_none()

    if not post_obj or not post_obj.file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="첨부파일이 존재하지 않습니다.",
        )

    full_path = BASE_DIR / post_obj.file_path
    if not full_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="서버에 물리 파일이 존재하지 않습니다.",
        )

    download_name = post_obj.original_filename or full_path.name
    return FileResponse(
        path=str(full_path),
        filename=download_name,
        media_type="application/octet-stream",
    )


@router.put("/{post_id}", response_model=PostResponse)
async def update_post(
    post_id: int,
    title: str = Form(..., description="수정할 안건 제목"),
    content: str = Form(..., description="수정할 안건 본문"),
    remove_file: bool = Form(False, description="기존 첨부파일 삭제 여부"),
    file: Optional[UploadFile] = File(None, description="새 첨부파일 (선택)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    🔒 [토큰 필수] 본인 작성 게시글 수정 (제목, 본문, 첨부파일 변경/제거)
    """
    stmt = select(Post).where(Post.id == post_id)
    result = await db.execute(stmt)
    post_obj = result.scalar_one_or_none()

    if not post_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="해당 게시글을 찾을 수 없습니다.",
        )

    if post_obj.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="본인이 작성한 게시글만 수정할 수 있습니다.",
        )

    # 1. 기본 필드 업데이트
    #    🔴 작성(create_post)에는 있는 검사가 여기엔 없었다 — 공백만 보내면
    #    제목이 `"   "` 인 글로 **수정된다**(만들 땐 400 인데). 같은 규칙을 건다.
    clean_title = title.strip()
    clean_content = content.strip()

    if not clean_title:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="제목을 입력해주세요.",
        )
    if not clean_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="내용을 입력해주세요.",
        )

    post_obj.title = clean_title
    post_obj.content = clean_content

    # 🔴 삭제와 같은 이유로 **commit 뒤에** 지운다(delete_post 주석 참조).
    #    여기 모아두고 아래에서 한 번에 지운다.
    pending_removals: list[str] = []

    # 2. 기존 첨부파일 제거 요청 처리
    if remove_file and post_obj.file_path:
        pending_removals.append(post_obj.file_path)
        post_obj.file_path = None
        post_obj.original_filename = None
        post_obj.file_size = None

    # 3. 새 첨부파일 업로드 처리
    if file and file.filename:
        file_bytes = await file.read()
        file_size = len(file_bytes)

        if file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"첨부파일 크기는 최대 20MB를 초과할 수 없습니다. (현재: {file_size / (1024*1024):.1f}MB)",
            )

        # 기존 파일 제거 (실제 unlink 는 commit 뒤)
        if post_obj.file_path:
            pending_removals.append(post_obj.file_path)

        orig_filename = file.filename
        ext = os.path.splitext(orig_filename)[1]
        unique_filename = f"{uuid.uuid4().hex}{ext}"

        upload_dir = get_upload_dir()
        target_file_path = upload_dir / unique_filename

        with open(target_file_path, "wb") as f:
            f.write(file_bytes)

        saved_file_path = str(target_file_path.relative_to(BASE_DIR))
        post_obj.file_path = saved_file_path
        post_obj.original_filename = orig_filename
        post_obj.file_size = file_size

    await db.commit()
    await db.refresh(post_obj)

    for rel_path in pending_removals:
        old_path = BASE_DIR / rel_path
        if old_path.is_file():
            try:
                old_path.unlink()
            except Exception as e:
                logger.warning(
                    "[posts] 첨부파일 교체/제거 실패 — 주인 없는 파일이 남는다: "
                    "post_id=%s path=%s err=%s",
                    post_id,
                    rel_path,
                    e,
                )

    return PostResponse(
        id=post_obj.id,
        user_id=post_obj.user_id,
        author_name=current_user.username,
        author_email=current_user.email,
        title=post_obj.title,
        content=post_obj.content,
        has_file=bool(post_obj.file_path),
        original_filename=post_obj.original_filename,
        file_size=post_obj.file_size,
        created_at=post_obj.created_at,
        is_owner=True,
    )
