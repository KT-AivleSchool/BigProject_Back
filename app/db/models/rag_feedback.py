from sqlalchemy import Column, Integer, Text, Numeric, DateTime
from sqlalchemy.sql import func
from app.db.session import Base

class RagFeedbackLog(Base):
    __tablename__ = "rag_feedback_log"

    id = Column(Integer, primary_key=True, index=True)
    query_text = Column(Text, nullable=False)
    chunk_text = Column(Text, nullable=False)
    vector_score = Column(Numeric, nullable=False)
    label = Column(Integer, nullable=False)  # 1 (사용됨), 0 (미사용됨)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
