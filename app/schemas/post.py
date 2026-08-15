from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


# 게시글 작성 폼 입력 (Form data) 처리용
class PostCreate(BaseModel):
    title: str = Field(..., max_length=200, description="게시글 제목")
    content: str = Field(..., description="게시글 본문 내용")


# 게시글 상세 정보 응답
class PostResponse(BaseModel):
    id: int
    user_id: int
    author_name: str = Field(..., description="작성자 이름")
    author_email: str = Field(..., description="작성자 이메일")
    title: str
    content: str
    has_file: bool = Field(..., description="첨부파일 존재 여부")
    original_filename: Optional[str] = Field(None, description="원본 파일명")
    file_size: Optional[int] = Field(None, description="파일 크기 (Byte)")
    created_at: datetime
    is_owner: bool = Field(False, description="현재 요청자가 작성자인지 여부")

    class Config:
        from_attributes = True


# 게시글 목록 항목 (요약)
class PostListItem(BaseModel):
    id: int
    user_id: int
    author_name: str
    title: str
    has_file: bool
    created_at: datetime

    class Config:
        from_attributes = True


# 게시글 목록 페이징 응답
class PostListResponse(BaseModel):
    total: int
    page: int
    limit: int
    posts: list[PostListItem]
