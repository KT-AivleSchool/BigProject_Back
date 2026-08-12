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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.config import BASE_DIR, settings
from app.db.base import User
from app.db.models.post import Post
from app.schemas.post import PostListItem, PostListResponse, PostResponse

router = APIRouter()

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB 제한


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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    🔒 [토큰 필수] 게시글 목록 조회 (페이지네이션 & 정렬 기능)
    """
    offset = (page - 1) * limit

    # 전체 수 쿼리
    count_stmt = select(func.count(Post.id))
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one_or_none() or 0

    # 정렬 컬럼 및 방향 지정
    order_column = Post.created_at
    if sort_by == "id":
        order_column = Post.id
    elif sort_by == "title":
        order_column = Post.title
    elif sort_by == "author_name":
        order_column = User.username
    elif sort_by == "created_at":
        order_column = Post.created_at

    sort_clause = order_column.asc() if order.lower() == "asc" else order_column.desc()

    # 목록 조인 쿼리 (User 테이블과 조인하여 작성자 이름 획득)
    stmt = (
        select(Post, User.username)
        .join(User, Post.user_id == User.id)
        .order_by(sort_clause)
        .offset(offset)
        .limit(limit)
    )
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

    # 저장된 첨부파일이 있는 경우 디스크에서 삭제
    if post_obj.file_path:
        full_file_path = BASE_DIR / post_obj.file_path
        if full_file_path.is_file():
            try:
                full_file_path.unlink()
            except Exception as e:
                print(f"[Post File Delete Warning] {e}")

    await db.delete(post_obj)
    await db.commit()

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
        path=full_path,
        filename=download_name,
        media_type="application/octet-stream",
    )
