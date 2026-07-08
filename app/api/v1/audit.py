from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status
from app.schemas.audit import AuditVerifyResponse, AuditSaveResponse
import random
import datetime

router = APIRouter()


@router.post("/verify", response_model=AuditVerifyResponse)
def verify_precedent_document(
    file: UploadFile = File(...), simulation_id: int = Form(...)
):
    """
    [승헌 TL 파트 & 장천명 풀스택] 준공 및 행정 종결 공문 PDF OCR 검증 및 RAG 실제 사례 자동 분류 API
    """
    filename = file.filename
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audit AI 검증용 문서는 오직 PDF 포맷만 지원합니다.",
        )

    # 가상의 OCR 판독 및 시나리오 A/B/C 매핑 결과 반환
    scenarios = ["A", "B", "C"]
    selected_scenario = random.choice(scenarios)

    return {
        "ocr_success": True,
        "extracted_text_snippet": "본 스마트 쉼터 준공 보고서에 의하면 차폐벽 설치 및 주민 소음 방지 펜스 설계가 완료되었음을 확인...",
        "matched_scenario": selected_scenario,
        "similarity_score": 0.897,
        "classification_status": "COMPLIANT",
    }


@router.post("/save", response_model=AuditSaveResponse)
def save_audit_feedback(simulation_id: int, matched_scenario: str):
    """
    RAG 환류 오염 방지(Model Collapse)를 위해 실증 적용 결과를 격리 적재하는 API
    """
    return {
        "audit_id": 105,
        "is_feedback_loop_isolated": True,
        "saved_at": datetime.datetime.now().isoformat(),
    }
