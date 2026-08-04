from sqlalchemy import Column, Integer, String, Numeric, Text, DateTime, func
from app.db.session import Base


class AuditRule(Base):
    """Audit 규칙 (감리 AI 산출물 대체용) 테이블"""

    __tablename__ = "audit_rules"

    id = Column(Integer, primary_key=True, index=True)
    dataset_id = Column(String(50), nullable=True)
    facility_type = Column(String(100), nullable=True)
    role_type = Column(
        String(50), nullable=True
    )  # "positive_factor", "negative_factor", "hard_exclusion"
    weight = Column(Numeric, nullable=True)  # 가점/감점 가중치
    rationale = Column(Text, nullable=True)  # 판정 근거
    source = Column(String(250), nullable=True)  # 출처 (조항 등)
    created_at = Column(DateTime, default=func.current_timestamp())
