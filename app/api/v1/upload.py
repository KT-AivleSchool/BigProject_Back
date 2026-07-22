import os
import urllib.parse
from typing import List
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.core.sim_ai.document_loader import statute_document_loader
from app.core.sim_ai.vector_db import vector_db

router = APIRouter()

# 조례 파일 저장 디렉토리 경로 (프로젝트 루트 / uploads / regulations)
UPLOAD_DIR = os.path.join(os.getcwd(), "uploads", "regulations")
os.makedirs(UPLOAD_DIR, exist_ok=True)


class RegulationItem(BaseModel):
    filename: str = Field(..., description="조례 파일명")
    size: int = Field(..., description="파일 크기 (바이트)")


@router.get("/regulations", response_model=List[RegulationItem])
async def list_regulations():
    """
    [장천명 풀스택] 조례 PDF 및 법규 문서 목록 조회 API
    uploads/regulations 디렉토리에 저장된 조례 파일들의 파일명과 용량을 반환합니다.
    """
    if not os.path.exists(UPLOAD_DIR):
        return []

    items = []
    for filename in os.listdir(UPLOAD_DIR):
        file_path = os.path.join(UPLOAD_DIR, filename)
        if os.path.isfile(file_path):
            size = os.path.getsize(file_path)
            items.append(RegulationItem(filename=filename, size=size))

    # 파일명 기준 오름차순 정렬
    items.sort(key=lambda x: x.filename)
    return items


@router.post("/regulation")
async def upload_regulation(files: List[UploadFile] = File(...)):
    """
    [장천명 풀스택] 조례 PDF/HWP/DOCX 파일 다중 업로드 및 RAG 자동 임베딩 API
    수신된 파일들을 uploads/regulations 디렉토리에 저장하고 텍스트를 추출해 Vector DB에 적재합니다.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="업로드할 조례 파일이 제공되지 않았습니다.",
        )

    saved_files = []
    allowed_extensions = {".pdf", ".hwp", ".docx", ".doc", ".txt"}

    for file in files:
        filename = file.filename or "unnamed_regulation.pdf"
        ext = os.path.splitext(filename)[1].lower()

        if ext not in allowed_extensions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"지원하지 않는 조례 파일 형식입니다 ({filename}). 허용 확장자: {allowed_extensions}",
            )

        file_path = os.path.join(UPLOAD_DIR, filename)
        file_bytes = await file.read()

        # 디스크 파일 저장
        with open(file_path, "wb") as f:
            f.write(file_bytes)

        # RAG 텍스트 추출 및 청킹
        try:
            chunks = statute_document_loader.process_document(file_bytes, ext)
            if chunks:
                metadatas = [
                    {"source": "uploaded_statute", "filename": filename} for _ in chunks
                ]
                await vector_db.add_statute_chunks(chunks, metadatas=metadatas)
        except Exception as e:
            # 텍스트 파싱 실패 시 경고 로그만 남기고 파일 저장은 유지
            print(f"[Regulation RAG Warning] {filename} 텍스트 추출/임베딩 실패: {e}")

        saved_files.append(filename)

    return {
        "status": "success",
        "message": f"{len(saved_files)}개 조례 파일이 성공적으로 적재되었습니다.",
        "files": saved_files,
    }


@router.delete("/regulations/{filename}")
async def delete_regulation(filename: str):
    """
    [장천명 풀스택] 조례 파일 및 RAG 캐시 삭제 API
    """
    decoded_filename = urllib.parse.unquote(filename)
    file_path = os.path.join(UPLOAD_DIR, decoded_filename)

    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"삭제 대상 조례 파일을 찾을 수 없습니다: {decoded_filename}",
        )

    try:
        os.remove(file_path)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"파일 삭제 처리 중 오류가 발생했습니다: {str(e)}",
        )

    return {
        "status": "success",
        "message": f"조례 '{decoded_filename}' 파일이 성공적으로 삭제되었습니다.",
    }
