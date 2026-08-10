from typing import Optional

from pydantic import BaseModel, Field


class ParsedDocumentMetadata(BaseModel):
    """PDF 공문서에서 정규식으로 추출한 메타데이터 서브 스키마 (리뷰 반영 추가)"""

    parsed_jibun: Optional[str] = Field(None, description="파싱된 지번 주소")
    parsed_date: Optional[str] = Field(None, description="파싱된 준공 및 접수 일자")
    facility_type: Optional[str] = Field(None, description="인프라 시설 유형")
    document_no: Optional[str] = Field(None, description="공문 문서 번호")


class AuditVerifyResponse(BaseModel):
    ocr_success: bool = Field(..., description="OCR PDF 텍스트 추출 성공 여부")
    extracted_text_snippet: str = Field(
        ..., description="추출된 텍스트 일부 스니펫 (준공 검사 요약)"
    )
    matched_scenario: Optional[str] = Field(
        None, description="매핑 판정된 3대 시나리오 유형 (A, B, C 중 1개 또는 None)"
    )
    similarity_score: float = Field(
        ..., description="시나리오 유사도 스코어 (코사인 유사도 등 - 0.0 ~ 1.0)"
    )
    classification_status: str = Field(
        ...,
        description="행정 이행 적합성 판정 결과 (COMPLIANT: 적합, DEVIATED: 일탈, WARNING: 경고, UNCLASSIFIED: 분류 불가)",
    )
    parsed_metadata: Optional[ParsedDocumentMetadata] = Field(
        None,
        description="공문 내 정규식 추출 메타데이터 (지번, 일자, 시설 유형, 문서번호)",
    )


class AuditSaveResponse(BaseModel):
    audit_id: int = Field(..., description="등록된 Audit 레코드 ID")
    is_feedback_loop_isolated: bool = Field(
        True,
        description="RAG 모델 붕괴 방지를 위해 실증 이행 사례 테이블에 격리 적재 완료 여부",
    )
    saved_at: str = Field(..., description="저장 완료 시각")
