import os
import shutil
from fastapi import APIRouter, UploadFile, File, HTTPException, status
from typing import List
from pydantic import BaseModel

router = APIRouter()

# 저장 디렉토리 정의
REGULATION_DIR = os.path.abspath("data/regulations")
CACHE_DIR = os.path.join(REGULATION_DIR, "cache")

# 디렉토리 생성
os.makedirs(REGULATION_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

class RegulationInfo(BaseModel):
    filename: str
    size: int

@router.post("/regulation")
def upload_regulations(files: List[UploadFile] = File(...)):
    """
    [조장 배종현 R&D 트랙] 조례 PDF 다중 업로드 및 중복 업로드 방지 가드 API
    """
    saved_files = []
    
    for file in files:
        filename = file.filename
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"PDF 파일만 업로드할 수 있습니다: {filename}"
            )
            
        file_path = os.path.join(REGULATION_DIR, filename)
        
        # 중복 방지 가드 (Duplication Guard)
        if os.path.exists(file_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"이미 등록된 조례 파일입니다: {filename}"
            )
            
        # PDF 파일 저장
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # RAG 텍스트 캐시 가상 추출 및 물리 저장
        cache_path = os.path.join(CACHE_DIR, f"{filename}.txt")
        with open(cache_path, "w", encoding="utf-8") as cache_file:
            cache_file.write(f"RAG 캐시 텍스트: {filename} 내용 파싱본 예시")
            
        saved_files.append(filename)
        
    return {
        "status": "success",
        "message": "조례 PDF 다중 업로드가 성공적으로 완료되었습니다.",
        "files": saved_files
    }

@router.get("/regulations", response_model=List[RegulationInfo])
def get_regulations():
    """
    등록된 조례(파일명 및 용량) 목록 비동기 동기화 리스팅 API
    """
    regulations = []
    if os.path.exists(REGULATION_DIR):
        for filename in os.listdir(REGULATION_DIR):
            file_path = os.path.join(REGULATION_DIR, filename)
            if os.path.isfile(file_path) and filename.lower().endswith(".pdf"):
                regulations.append(
                    RegulationInfo(
                        filename=filename,
                        size=os.path.getsize(file_path)
                    )
                )
    return regulations

@router.delete("/regulations/{filename}")
def delete_regulation(filename: str):
    """
    법규 및 RAG 텍스트 캐시 물리 동시 삭제 엔진 (Deletion Engine)
    """
    pdf_path = os.path.join(REGULATION_DIR, filename)
    cache_path = os.path.join(CACHE_DIR, f"{filename}.txt")
    
    deleted = False
    
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
        deleted = True
        
    if os.path.exists(cache_path):
        os.remove(cache_path)
        deleted = True
        
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"삭제 대상 파일을 찾을 수 없습니다: {filename}"
        )
        
    return {
        "status": "success",
        "message": f"조례 파일 {filename} 및 RAG 텍스트 캐시가 물리적으로 영구 삭제되었습니다."
    }
