from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.db.session import get_db
from app.db.models.precedent import VerifiedPrecedent
from app.db.models.simulation import ConflictSimulation
from app.core.audit_ai.parser import pdf_parser
from app.core.audit_ai.classifier import audit_classifier
from app.schemas.audit import AuditVerifyResponse, AuditSaveResponse

router = APIRouter()


@router.post("/verify", response_model=AuditVerifyResponse)
async def verify_precedent_document(
    file: UploadFile = File(...),
    simulation_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """
    [승헌 TL 파트 & 장천명 풀스택] 준공 및 행정 종결 공문 PDF OCR 검증 및 RAG 실제 사례 자동 분류 API
    - 업로드된 PDF 준공 공문서의 실물 텍스트 레이어를 PyMuPDF로 파싱 및 추출하여 감증을 준비합니다.
    """
    filename = file.filename
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audit AI 검증용 문서는 오직 PDF 포맷만 지원합니다.",
        )

    try:
        # PDF 바이너리 수신 및 텍스트 추출
        pdf_bytes = await file.read()
        extracted_text = pdf_parser.extract_text_from_pdf(pdf_bytes)

        if not extracted_text.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="PDF 파일 내에 물리 텍스트 레이어가 존재하지 않거나 이미지 전용 스캔 PDF입니다.",
            )

        # DB에서 원래 에이전트들이 도출했던 3대 예측 시나리오 정보 획득
        sim_result = await db.execute(
            select(ConflictSimulation).where(ConflictSimulation.id == simulation_id)
        )
        sim_data = sim_result.scalar()

        predicted_scenarios = []
        if sim_data and sim_data.result_json:
            predicted_scenarios = sim_data.result_json.get("scenarios", [])

        # 정규식 메타데이터 도출
        parsed_metadata = pdf_parser.parse_document_metadata(extracted_text)

        # 실증 유사도 분류 판정 가동
        analysis = audit_classifier.classify_actual_scenario(
            extracted_text, predicted_scenarios
        )

        return {
            "ocr_success": True,
            "extracted_text_snippet": extracted_text[:200].replace("\n", " ").strip()
            + "...",
            "matched_scenario": analysis["matched_scenario"],
            "similarity_score": analysis["similarity_score"],
            "classification_status": analysis["classification_status"],
            "parsed_metadata": parsed_metadata,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"준공 공문 PDF OCR 분석 중 내부 서버 에러가 발생했습니다: {str(e)}",
        )


@router.post("/save", response_model=AuditSaveResponse)
async def save_audit_feedback(
    simulation_id: int = Form(...),
    matched_scenario: str = Form(...),
    similarity_score: float = Form(...),
    classification_status: str = Form(...),
    extracted_text: str = Form(...),
    document_no: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """
    [장천명 풀스택] RAG 환류 오염 방지(Model Collapse)를 위해 실증 적용 결과를 VerifiedPrecedent 테이블에 격리 적재하는 API
    """
    try:
        new_precedent = VerifiedPrecedent(
            parcel_id=simulation_id,
            document_no=document_no,
            matched_scenario=matched_scenario,
            similarity_score=similarity_score,
            classification_status=classification_status,
            extracted_text=extracted_text,
        )
        db.add(new_precedent)
        await db.commit()
        await db.refresh(new_precedent)

        return {
            "audit_id": new_precedent.id,
            "is_feedback_loop_isolated": True,
            "saved_at": new_precedent.verified_at.isoformat(),
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"실증 이행 사례 격리 저장 중 데이터베이스 트랜잭션 에러가 발생했습니다: {str(e)}",
        )
