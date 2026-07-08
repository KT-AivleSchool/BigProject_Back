from pydantic import BaseModel, Field


class AuditVerifyResponse(BaseModel):
    ocr_success: bool = Field(..., description="OCR PDF 텍스트 추출 성공 여부")
    extracted_text_snippet: str = Field(
        ..., description="추출된 텍스트 일부 스니펫 (준공 검사 요약)"
    )
    matched_scenario: str = Field(
        ..., description="매핑 판정된 3대 시나리오 유형 (A, B, C 중 1개)"
    )
    similarity_score: float = Field(
        ..., description="시나리오 유사도 스코어 (코사인 유사도 등 - 0.0 ~ 1.0)"
    )
    classification_status: str = Field(
        ...,
        description="행정 이행 적합성 판정 결과 (COMPLIANT: 적합, DEVIATED: 일탈, WARNING: 경고)",
    )


class AuditSaveResponse(BaseModel):
    audit_id: int = Field(..., description="등록된 Audit 레코드 ID")
    is_feedback_loop_isolated: bool = Field(
        True,
        description="RAG 모델 붕괴 방지를 위해 실증 이행 사례 테이블에 격리 적재 완료 여부",
    )
    saved_at: str = Field(..., description="저장 완료 시각")
