from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.db.session import get_db
from app.db.models.precedent import VerifiedPrecedent
from app.db.models.simulation import ConflictSimulation
from app.core.audit_ai.parser import pdf_parser
from app.core.audit_ai.classifier import audit_classifier
from app.schemas.audit import AuditVerifyResponse, AuditSaveResponse, AuditSaveRequest
import datetime

router = APIRouter()


@router.post("/verify", response_model=AuditVerifyResponse)
async def verify_precedent_document(
    file: UploadFile = File(...),
    simulation_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """
    [승헌 TL 파트 & 장천명 풀스택] 준공 및 행정 종결 공문 PDF OCR 검증 및 RAG 실제 사례 자동 분류 API
    - 업로드된 PDF 바이너리 스트림에서 실물 텍스트 레이어를 PyMuPDF로 실시간 파싱 및 정규식 메타데이터를 추출합니다.
    - 추출된 텍스트와 기존 예측 시나리오 간의 초경량 코사인 유사도를 수학적으로 계산하여 이행 적합성 판정을 수행합니다.
    """
    filename = file.filename
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audit AI 검증용 문서는 오직 PDF 포맷만 지원합니다.",
        )

    try:
        # 1. PDF 바이너리 수신 및 텍스트 추출 (PyMuPDF 컨텍스트 매니저 사용)
        pdf_bytes = await file.read()
        extracted_text = pdf_parser.extract_text_from_pdf(pdf_bytes)

        # 이미지 전용 스캔본 PDF로 인해 텍스트 레이어가 비어있을 시 422 반환 (리뷰 반영)
        if not extracted_text.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="PDF 파일 내에 물리 텍스트 레이어가 존재하지 않거나 이미지 전용 스캔 PDF입니다.",
            )

        # 2. DB에서 원래 에이전트들이 도출했던 3대 예측 시나리오 정보 획득
        sim_result = await db.execute(
            select(ConflictSimulation).where(ConflictSimulation.id == simulation_id)
        )
        sim_data = sim_result.scalar()

        predicted_scenarios = []
        if sim_data and sim_data.result_json:
            predicted_scenarios = sim_data.result_json.get("scenarios", [])

        # 3. 정규식 기반 메타데이터 파싱 (지번 주소, 문서번호, 날짜 등 추출)
        parsed_metadata = pdf_parser.parse_document_metadata(extracted_text)

        # 4. 실증 유사도 분류 판정 가동 (DB 예측 시나리오 대조)
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
            detail=f"준공 공문 검증 연산 중 런타임 서버 에러가 발생했습니다: {str(e)}",
        )


@router.post("/save", response_model=AuditSaveResponse)
async def save_audit_feedback(
    payload: AuditSaveRequest, db: AsyncSession = Depends(get_db)
):
    """
    RAG 환류 오염 방지(Model Collapse)를 위해 실증 적용 결과를 verified_precedents 테이블에 격리 적재하는 API
    """
    try:
        db_precedent = VerifiedPrecedent(
            parcel_id=payload.parcel_id,
            document_no=payload.document_no,
            matched_scenario=payload.matched_scenario,
            similarity_score=payload.similarity_score,
            classification_status=payload.classification_status,
            extracted_text=payload.extracted_text,
        )
        db.add(db_precedent)
        await db.commit()
        await db.refresh(db_precedent)

        return {
            "audit_id": db_precedent.id,
            "is_feedback_loop_isolated": True,
            "saved_at": datetime.datetime.now().isoformat(),
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"격리 사례 적재 DB 트랜잭션 처리 중 오류가 발생했습니다: {str(e)}",
        )
