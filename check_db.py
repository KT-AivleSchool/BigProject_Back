import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.session import AsyncSessionLocal
from app.db.models.rag_feedback import RagFeedbackLog
from sqlalchemy import select

async def show_feedback():
    async with AsyncSessionLocal() as session:
        print("🔍 DB 연결 완료! rag_feedback_log 테이블 조회 중...")
        result = await session.execute(select(RagFeedbackLog).order_by(RagFeedbackLog.id))
        logs = result.scalars().all()
        
        if not logs:
            print("현재 DB에 쌓인 피드백 데이터가 없습니다.")
            return

        print(f"총 {len(logs)}건의 피드백 데이터가 있습니다.\n")
        print("-" * 80)
        for log in logs:
            print(f"[ID: {log.id}] Label: {log.label} | Vector Score: {log.vector_score:.4f}")
            print(f" - 질문: {log.query_text}")
            print(f" - 조례원문: {log.chunk_text[:60]}...")
            print("-" * 80)

if __name__ == "__main__":
    asyncio.run(show_feedback())
