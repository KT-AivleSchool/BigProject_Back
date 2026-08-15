from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.db.session import get_db
from app.db.models.precedent import VerifiedPrecedent
from app.db.models.simulation import HearingResultA
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
            select(HearingResultA).where(HearingResultA.id == simulation_id)
        )
        sim_data = sim_result.scalar()

        # 🔴 2026-08-11. 예전엔 없는 `simulation_id` 여도 `predicted_scenarios = []`
        #    로 넘어가 **200 + UNCLASSIFIED** 가 나갔다 — 호출자는 "대조했는데 안
        #    맞았다" 로 읽는다. 실제로는 대조할 것을 못 찾은 것이다(원칙 1·4).
        if sim_data is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"hearing_result_a 에 id={simulation_id} 가 없다.",
            )

        predicted_scenarios = (sim_data.result_json or {}).get("scenarios") or []

        # 정규식 메타데이터 도출.
        # 🔴 2026-08-11. 시설 어휘는 **이 시뮬레이션이 다루는 시설**에서 온다.
        #    예전엔 파서 안에 `{"흡연구역": [...], "쓰레기통": [...], …}` 사전이
        #    박혀 있었다 — MVP 도메인 넷만 알아보고 나머지는 영영 `None` 이다
        #    (원칙 2). 여기서 넘기면 도메인이 늘어도 코드가 안 바뀐다.
        #    `facility_type` 이 비어 있으면 어휘 없이 부른다 → `None` 이 나간다.
        #    「이 공문이 그 시설 얘기인지」를 못 정한 것이고, 지어내지 않는다(원칙 1).
        sim_facility = (sim_data.facility_type or "").strip()
        parsed_metadata = pdf_parser.parse_document_metadata(
            extracted_text,
            facility_vocab={sim_facility: [sim_facility]} if sim_facility else None,
        )

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
    # 🔴 2026-08-11. `/verify` 는 판정을 못 하면 `matched_scenario: null` +
    #    `UNCLASSIFIED`(안 겹침) 또는 `NO_PREDICTION`(대조할 시나리오 없음)을 준다.
    #    그걸 그대로 넘기면 `actual_scenario` 가 NOT NULL 이라 500 이 나거나,
    #    빈 문자열이 **실증 사례로 적재**된다. 이 테이블의 존재 이유가 RAG 환류
    #    오염 방지인데 미분류를 넣으면 그 목적이 무너진다 — 여기서 막는다.
    if classification_status in ("UNCLASSIFIED", "NO_PREDICTION") or not (
        matched_scenario or ""
    ).strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"미분류 결과는 적재하지 않는다 (matched_scenario={matched_scenario!r}, "
                f"classification_status={classification_status!r}). "
                "실증 사례 테이블은 실제로 일어난 결과만 쌓는다."
            ),
        )

    try:
        # 🔴 2026-08-09 컬럼명 정정. 예전엔 `parcel_id=simulation_id` 였다 —
        #    이름은 필지인데 값은 시뮬레이션 id 였고, 나머지 5개는 실 DB 에 아예
        #    없는 컬럼이라 이 저장은 **항상 실패**했다(verified_precedents 0행).
        #    `matched_scenario`→`actual_scenario`(예측이 아니라 실측이라 이 이름),
        #    `extracted_text`→`document_ocr_text` 로 실 DB 컬럼에 흡수했다.
        new_precedent = VerifiedPrecedent(
            conflict_simulation_id=simulation_id,
            document_no=document_no,
            actual_scenario=matched_scenario,
            similarity_score=similarity_score,
            classification_status=classification_status,
            document_ocr_text=extracted_text,
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
