from typing import List
import logging
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import PGVector
from app.config import settings

logger = logging.getLogger(__name__)

# 유사도 임계치 (데이터팀에서 3-small 적재 후 무관 질의 분포를 측정해 최적값으로 조정 예정)
SIMILARITY_THRESHOLD = 0.25

# [동현님 담당] pgvector Vector DB 연결 및 RAG 문서 적재/조회 모듈
class RagVectorStorage:
    def __init__(self):
        # app/config.py의 OPENAI_API_KEY를 사용하여 임베딩 모델 활성화
        self.embeddings = OpenAIEmbeddings(
            api_key=settings.OPENAI_API_KEY, model="text-embedding-3-small"
        )

        # 드라이버 호환성을 위해 접속 문자열 조정 (psycopg3 드라이버인 psycopg 명시)
        conn_str = settings.DATABASE_URL
        if conn_str.startswith("postgres://"):
            conn_str = conn_str.replace("postgres://", "postgresql+psycopg://")
        elif conn_str.startswith("postgresql://") and "psycopg" not in conn_str:
            conn_str = conn_str.replace("postgresql://", "postgresql+psycopg://")

        self.connection_string = conn_str

        # 1. 기본 조례/법규 콜렉션 (일반적인 RAG 참조용)
        try:
            self.statutes_store = PGVector(
                collection_name="statutes_collection",
                connection_string=self.connection_string,
                embedding_function=self.embeddings,
            )
        except Exception as e:
            logger.warning(
                f"⚠️ Vector DB (PGVector) 초기화 실패 - 로컬 DB 미사용 모드로 동작합니다: {e}"
            )
            self.statutes_store = None

        # 2. 사후 검증된 피드백 콜렉션 (Model Collapse 예방 및 Audit AI 전용)
        try:
            self.feedback_store = PGVector(
                collection_name="feedback_collection",
                connection_string=self.connection_string,
                embedding_function=self.embeddings,
            )
        except Exception as e:
            logger.warning(f"⚠️ Vector DB (PGVector) 피드백 콜렉션 초기화 실패: {e}")
            self.feedback_store = None

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
            logger.info(
                f"[RAG] 성공적으로 {len(chunks)}개의 피드백 청크를 verified_precedents에 적재했습니다. (문서ID: {document_id})"
            )
        except Exception as e:
            logger.error(f"[RAG Error] Feedback Data Insert Error: {e}")

    async def add_statute_chunks(self, chunks: List[str], metadatas: List[dict] = None):
        """
        조례 및 범례 다중 포맷 문서에서 추출된 텍스트 청크를 기본 조례 콜렉션(statutes_collection)에 적재합니다.
        (시설 종류는 사전에 지정하지 않고, 토론 시 AI가 의미(Semantic) 검색을 통해 관련 조례를 스스로 찾아냅니다.)
        """
        try:
            if metadatas is None:
                metadatas = [{"source": "uploaded_statute"} for _ in chunks]
            await self.statutes_store.aadd_texts(texts=chunks, metadatas=metadatas)
            logger.info(
                f"[RAG] 성공적으로 {len(chunks)}개의 조례 청크를 statutes_collection에 적재했습니다."
            )
        except Exception as e:
            logger.error(f"[RAG Error] Statute Data Insert Error: {e}")

    async def retrieve_similar_statutes(
        self, query: str, top_k: int = 3, facility_type: str = None
    ) -> List[str]:
        """
        [동현 AI 메인] 토론 시나리오 발화 문맥(query)과 가장 유사한 조례 규정 텍스트를
        '기본 조례 콜렉션(statutes_collection)'에서 비동기로 검색합니다.
        """
        if not self.statutes_store:
            return []

        try:
            # LangChain의 비동기 유사도 검색 (asimilarity_search_with_relevance_scores) 사용
            search_kwargs = {"k": top_k}

            # [A-2] facility_type 쿼리 prefix 제거 및 필터(filter) 적용
            if facility_type:
                search_kwargs["filter"] = {"facility_type": facility_type}

            # [A-3] 유사도 임계치 검사 및 점수 포함 검색
            docs_with_scores = (
                await self.statutes_store.asimilarity_search_with_relevance_scores(
                    query, **search_kwargs
                )
            )

            # 검색 결과가 없을 경우 안전한 빈 배열 반환
            if not docs_with_scores:
                return []

            # 임계치 이상인 문서만 순수 텍스트(page_content) 추출
            filtered_docs = [
                doc.page_content for doc, score in docs_with_scores if score >= SIMILARITY_THRESHOLD
            ]

            return filtered_docs
        except Exception as e:
            # 호출부에서 에러를 인지할 수 있도록 예외를 던짐
            logger.error(f"[RAG Error] Vector DB Search Error: {e}")
            raise e
