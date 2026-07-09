from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from sqlalchemy.sql import func
from app.db.session import Base


class VerifiedPrecedent(Base):
    """최종 준공 완료된 행정 실증 사례 격리 적재 테이블"""

    __tablename__ = "verified_precedents"

    id = Column(Integer, primary_key=True, index=True)
    parcel_id = Column(Integer, nullable=False, index=True)
    document_no = Column(String(100), nullable=True)
    matched_scenario = Column(String(10), nullable=False)
    similarity_score = Column(Float, nullable=False)
    classification_status = Column(String(20), nullable=False)
    extracted_text = Column(Text, nullable=False)
    verified_at = Column(DateTime(timezone=True), server_default=func.now())
