from typing import List
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import text
from app.config import settings
from app.db.session import SessionLocal


# [동현님 담당] pgvector Vector DB 연결 및 RAG 문서 적재/조회 모듈
class RagVectorStorage:
    def __init__(self):
        # app/config.py의 OPENAI_API_KEY를 사용하여 임베딩 모델 활성화
        self.embeddings = OpenAIEmbeddings(api_key=settings.OPENAI_API_KEY)

    async def add_document_chunks(self, document_id: str, chunks: List[str]):
        """
        [동현님 담당] 업로드된 조례집 PDF/HWP 텍스트 청크를 벡터화하여 pgvector DB에 적재합니다.
        (현재는 이미 적재되어 있다고 가정하므로 Pass)
        """
        pass

    async def retrieve_similar_statutes(self, query: str, top_k: int = 3) -> List[str]:
        """
        [동현님 담당] 토론 진행 시 발화 문맥(query)과 가장 유사한 조례 규정 텍스트를 pgvector에서 조회합니다.
        """
        # 1. 사용자의 발화 문맥을 임베딩 벡터로 변환 (비동기 처리 지원 시 aembed_query 권장)
        query_vector = await self.embeddings.aembed_query(query)

        # 2. pgvector 코사인 거리 연산자(<=>)를 사용하여 유사한 문서 검색
        # 가상의 테이블명: statute_chunks (content, embedding 컬럼 존재 가정)
        sql_query = text("""
            SELECT content
            FROM statute_chunks
            ORDER BY embedding <=> :vector::vector
            LIMIT :top_k
        """)

        # 3. 비동기 DB 세션을 열고 쿼리 실행
        async with SessionLocal() as session:
            try:
                # pgvector 쿼리 실행 시 리스트 형태의 벡터를 문자열로 포맷팅하여 전달해야 할 수 있음
                vector_str = "[" + ",".join(map(str, query_vector)) + "]"
                result = await session.execute(
                    sql_query, {"vector": vector_str, "top_k": top_k}
                )
                rows = result.fetchall()

                if not rows:
                    # Fallback (디버깅용)
                    return [
                        "서울특별시 용산구 금연 환경 조성 조례 제4조: 주거지역 인근 10m 이내 금연구역 버퍼 지정",
                        "서울특별시 도로관리 조례 시행규칙 제5조: 시설물의 점용 면적 제한",
                    ]

                # 검색된 텍스트 컨텐츠 리스트 반환
                return [row.content for row in rows]
            except Exception as e:
                # 테이블 미생성 오류 등의 대비 폴백 처리
                print(f"Vector DB Search Error: {e}")
                return [
                    "서울특별시 용산구 금연 환경 조성 조례 제4조: 주거지역 인근 10m 이내 금연구역 버퍼 지정",
                    "서울특별시 도로관리 조례 시행규칙 제5조: 시설물의 점용 면적 제한",
                ]
