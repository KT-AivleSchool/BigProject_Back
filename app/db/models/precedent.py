from sqlalchemy import (
    Column,
    Integer,
    String,
    Numeric,
    DateTime,
    Text,
    ForeignKey,
)
from sqlalchemy.sql import func
from app.db.session import Base


class VerifiedPrecedent(Base):
    """최종 준공 완료된 행정 실증 사례 격리 적재 테이블.

    RAG 환류 오염(Model Collapse) 방지용이다 — LLM 이 만든 예측이 아니라
    **실제로 일어난 결과**만 여기 쌓는다.

    🔴 2026-08-09 정정. 예전 선언은 `parcel_id`·`document_no`·`matched_scenario`·
       `similarity_score`·`classification_status`·`extracted_text` 였는데 실 DB 엔
       그중 **하나도 없었다** → 저장이 100% 실패(0행)했다.

    ⚠ 여기는 **DB 이름이 맞고 코드 이름이 틀렸던** 경우다. `audit.py` 는
      `VerifiedPrecedent(parcel_id=simulation_id, ...)` 로 넣는다 — 이름은 필지인데
      값은 시뮬레이션 id 다. 실 DB 의 `conflict_simulation_id` 가 실제 의미이므로
      그쪽에 맞췄다. 이름이 값을 속이면 나중에 필지 조인에 그대로 쓰인다.
    """

    __tablename__ = "verified_precedents"

    id = Column(Integer, primary_key=True, index=True)

    # 예측을 낸 시뮬레이션. 시뮬레이션이 지워져도 사례 자체는 남긴다(SET NULL).
    conflict_simulation_id = Column(
        Integer,
        ForeignKey("hearing_result_a.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    document_title = Column(String(250), nullable=True)  # 공문 제목
    document_no = Column(String(100), nullable=True)  # 공문 번호 (정규식 추출)
    document_ocr_text = Column(Text, nullable=True)  # PDF 텍스트 레이어 원문

    # 분류기가 준공 공문에서 판정한 **실제** 시나리오(A/B/C).
    # 코드의 `matched_scenario` 가 이 값이다 — 예측이 아니라 실측이라 이 이름을 쓴다.
    actual_scenario = Column(String(50), nullable=False)
    similarity_score = Column(Numeric, nullable=True)  # 예측 ↔ 실제 유사도
    classification_status = Column(String(20), nullable=True)  # 분류 판정 상태

    verified_at = Column(DateTime, server_default=func.current_timestamp())
