from typing import List
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import PGVector
from app.config import settings

# [동현님 담당] LangChain 기반 pgvector RAG 모듈 (Model Collapse 예방 아키텍처)
class RagVectorStorage:
    def __init__(self):
        # app/config.py의 OPENAI_API_KEY를 사용하여 임베딩 모델 활성화
        self.embeddings = OpenAIEmbeddings(api_key=settings.OPENAI_API_KEY)
        
        # 드라이버 호환성을 위해 접속 문자열 조정 (SQLAlchemy 기본 드라이버인 psycopg2 명시)
        conn_str = settings.DATABASE_URL
        if conn_str.startswith("postgres://"):
            conn_str = conn_str.replace("postgres://", "postgresql+psycopg2://")
        elif conn_str.startswith("postgresql://") and "psycopg2" not in conn_str:
            conn_str = conn_str.replace("postgresql://", "postgresql+psycopg2://")
            
        self.connection_string = conn_str

        # 1. 기본 조례/법규 콜렉션 (일반적인 RAG 참조용)
        self.statutes_store = PGVector(
            collection_name="statutes_collection",
            connection_string=self.connection_string,
            embedding_function=self.embeddings,
        )

        # 2. 사후 검증된 피드백 콜렉션 (Model Collapse 예방 및 Audit AI 전용)
        self.feedback_store = PGVector(
            collection_name="verified_precedents",
            connection_string=self.connection_string,
            embedding_function=self.embeddings,
        )

    async def add_document_chunks(self, document_id: str, chunks: List[str]):
        """
        [동현 AI 메인] 사업 준공 후 실제 타결된 공문서(PDF/HWP) 텍스트를 청크화하여 
        Model Collapse 예방용 '격리된 피드백 콜렉션(verified_precedents)'에 적재합니다.
        """
        try:
            # 문서 추적을 위해 메타데이터에 document_id 태깅
            metadatas = [{"document_id": document_id} for _ in chunks]
            
            # 비동기 임베딩 및 DB 적재 (aadd_texts 활용)
            await self.feedback_store.aadd_texts(texts=chunks, metadatas=metadatas)
            print(f"[RAG] 성공적으로 {len(chunks)}개의 피드백 청크를 verified_precedents에 적재했습니다. (문서ID: {document_id})")
        except Exception as e:
            print(f"[RAG Error] Feedback Data Insert Error: {e}")

    async def retrieve_similar_statutes(self, query: str, top_k: int = 3) -> List[str]:
        """
        [동현 AI 메인] 토론 시나리오 발화 문맥(query)과 가장 유사한 조례 규정 텍스트를 
        '기본 조례 콜렉션(statutes_collection)'에서 비동기로 검색합니다.
        """
        try:
            # LangChain의 비동기 유사도 검색 (asimilarity_search) 사용
            docs = await self.statutes_store.asimilarity_search(query, k=top_k)
            
            # 검색 결과가 없을 경우 안전한 빈 배열 또는 폴백 반환
            if not docs:
                return self._get_fallback_data()
                
            # Document 객체에서 순수 텍스트(page_content)만 추출하여 리스트로 반환
            return [doc.page_content for doc in docs]
        except Exception as e:
            # DB 미생성, 연결 오류 등에 대비한 폴백 처리 (프론트엔드 크래시 방지)
            print(f"[RAG Warning] Vector DB Search Error (Falling back to dummy data): {e}")
            return self._get_fallback_data()

    def _get_fallback_data(self) -> List[str]:
        """DB 미연결 또는 데이터 부재 시 반환되는 임시 폴백 데이터"""
        return [
            "서울특별시 용산구 금연 환경 조성 조례 제4조: 주거지역 인근 10m 이내 금연구역 버퍼 지정",
            "친환경자동차법 제11조의2: 공공건물 및 공중이용시설의 전기차 충전시설 설치 의무화 (주차대수 50면 이상)",
            "서울특별시 도로관리 조례 시행규칙 제5조: 보행자 통행에 지장을 주지 않는 범위 내에서 시설물 점용 허가",
        ]
