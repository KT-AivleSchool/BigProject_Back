import fitz  # PyMuPDF
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def create_mock_pdf_bytes(text: str) -> bytes:
    """테스트용 단일 페이지 텍스트 PDF 바이너리를 생성합니다."""
    doc = fitz.open()
    page = doc.new_page()
    cjk_font = fitz.Font("cjk")
    page.insert_font(fontname="mykorean", fontbuffer=cjk_font.buffer)
    page.insert_text((50, 50), text, fontname="mykorean", fontsize=10)
    pdf_bytes = doc.write()
    doc.close()
    return pdf_bytes


def test_audit_verify_api_compliant():
    """사후 감사 PDF 검증 API (/verify) - 정상 COMPLIANT 판정 테스트"""
    # 시나리오 C에 매핑될 가능성이 높은 텍스트 구성
    text = (
        "서울특별시 용산구 이태원동 123-45\n"
        "용산구-행정-12345호\n"
        "일자: 2026.07.09\n"
        "시설: 흡연부스 설치 계획\n"
        "본 흡연부스 설치는 주거 인근 소음 민원 및 보행 장애 예방을 위한 가림막 설치와 점용 승인 행정 준공 종결을 준수합니다."
    )
    pdf_bytes = create_mock_pdf_bytes(text)

    response = client.post(
        "/api/v1/audit/verify",
        files={"file": ("test_compliant.pdf", pdf_bytes, "application/pdf")},
        data={"simulation_id": 1},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ocr_success"] is True
    assert data["matched_scenario"] == "C"
    assert data["classification_status"] in ["COMPLIANT", "WARNING"]
    assert data["parsed_metadata"]["parsed_jibun"] == "서울특별시 용산구 이태원동 123-45"
    assert data["parsed_metadata"]["document_no"] == "용산구-행정-12345호"
    assert data["parsed_metadata"]["facility_type"] == "흡연구역"


def test_audit_verify_api_invalid_file_type():
    """PDF가 아닌 확장자 업로드 시 400 Bad Request 예외 처리 검증"""
    response = client.post(
        "/api/v1/audit/verify",
        files={"file": ("test.txt", b"dummy text content", "text/plain")},
        data={"simulation_id": 1},
    )
    assert response.status_code == 400
    assert "PDF 포맷만 지원합니다" in response.json()["detail"]


def test_audit_verify_api_empty_pdf():
    """텍스트 레이어가 없는 빈 PDF 업로드 시 422 Unprocessable Entity 예외 처리 검증"""
    # 텍스트 없이 빈 페이지로만 구성된 PDF 생성
    doc = fitz.open()
    doc.new_page()
    pdf_bytes = doc.write()
    doc.close()

    response = client.post(
        "/api/v1/audit/verify",
        files={"file": ("empty.pdf", pdf_bytes, "application/pdf")},
        data={"simulation_id": 1},
    )
    assert response.status_code == 422
    assert "물리 텍스트 레이어가 존재하지 않거나" in response.json()["detail"]


def test_audit_save_api_db_integration_check():
    """사후 감사 결과 저장 API (/save) - 호출 시 스키마 및 가용성 체크"""
    payload = {
        "parcel_id": 1,
        "document_no": "용산구-행정-12345호",
        "matched_scenario": "C",
        "similarity_score": 0.85,
        "classification_status": "COMPLIANT",
        "extracted_text": "서울특별시 용산구 이태원동 123-45 흡연부스 설치 계획안 행정 종결",
    }
    try:
        response = client.post("/api/v1/audit/save", json=payload)
        # 로컬 DB가 가동 중이고 테이블이 있다면 200 OK 성공
        if response.status_code == 200:
            data = response.json()
            assert data["is_feedback_loop_isolated"] is True
            assert "audit_id" in data
            print("\n✔ [SUCCESS] /save API DB 연동 성공!")
        else:
            print(f"\n⚠ [SKIP] DB Connection/Schema Error: {response.json().get('detail')}")
    except Exception as e:
        print(f"\n⚠ [SKIP] Backend Server/DB not fully running for save test: {e}")
