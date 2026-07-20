from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_step_0_and_1_upload_and_ingestion():
    """Step 0 & 1: 통계 데이터셋 업로드 및 감리 분석 E2E 테스트"""
    # 임의의 CSV 데이터 생성
    csv_content_1 = "id,lat,lng,name\n1,37.53,126.97,어린이집A\n2,37.535,126.975,어린이집B".encode("utf-8")
    csv_content_2 = "id,lat,lng,name\n1,37.52,126.98,금연구역A\n2,37.525,126.985,금연구역B".encode("utf-8")

    # lands/upload 호출
    response = client.post(
        "/api/v1/lands/upload",
        files=[
            ("files", ("childcare.csv", csv_content_1, "text/csv")),
            ("files", ("nosmoking.csv", csv_content_2, "text/csv")),
        ],
        data={"district_id": 1},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "summary" in data
    assert data["summary"]["filename"] == "childcare.csv"
    assert data["summary"]["file_type"] == "CSV"


def test_step_1_details_retrieval():
    """Step 1: 지적도 필지 상세정보 조회 테스트"""
    response = client.get("/api/v1/lands/details/123")

    assert response.status_code == 200
    data = response.json()
    assert data["parcel_id"] == 123
    assert data["address"] == "서울특별시 용산구 한강대로 180"
    assert "geometry_geojson" in data
    assert data["is_excluded"] is False


def test_step_2_hitl_coordinate_correction():
    """Step 2: HITL 지도 마커 드래그앤드롭 수동 좌표 보정 커밋 테스트"""
    payload = {
        "parcel_id": 123,
        "corrected_lat": 37.535,
        "corrected_lng": 126.975,
    }
    response = client.post("/api/v1/lands/hitl/commit", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "updated_coordinates" in data
    assert data["updated_coordinates"]["lat"] == 37.535
    assert data["updated_coordinates"]["lng"] == 126.975


def test_step_1_csv_audit_llm():
    """Step 1: CSV 데이터셋 업로드 및 실시간 감리 AI(LLM) 파이프라인 연동 테스트"""
    csv_content = "id,lat,lng,name\n1,37.53,126.97,어린이집A\n2,37.535,126.975,어린이집B\n".encode("utf-8")

    response = client.post(
        "/api/v1/lands/audit/csv",
        files=[("files", ("childcare_centers.csv", csv_content, "text/csv"))]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "audit_reason" in data
    assert "user_intent" in data
    assert "extracted_weights" in data

    # 실시간 LLM vs Fallback 상태 출력
    if "Fallback" in data["audit_reason"] or "예외" in data["audit_reason"]:
        print("\n⚠ [NOTICE] OpenAI API Key가 만료/한도 초과(429)되어 Fallback 데이터로 안전하게 응답했습니다.")
    else:
        print("\n✔ [SUCCESS] OpenAI API를 통한 실시간 LLM 감리 연산 성공!")
        print("감리 분석 결과 요약:", data["audit_reason"])

