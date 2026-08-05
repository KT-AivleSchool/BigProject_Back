from typing import List
import logging
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import PGVector
from app.config import settings

logger = logging.getLogger(__name__)

# 유사도 임계치 — 3-small 적재분(시드 6종·222청크)에서 실측한 값.
#   관련 질의-문서 쌍 최저 0.3602 / 무관 쌍 최고 0.4416 (0.36~0.44 구간 중첩).
#   0.36 = 관련을 하나도 잃지 않는 최댓값(관련 25/25 유지, 무관 통과 20→8건).
#   조례 인용은 누락(근거 없이 토론 진행)이 오탐보다 치명적이라 재현율 우선.
#   시드 코퍼스가 크게 바뀌면 재측정 필요.
SIMILARITY_THRESHOLD = 0.31


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
        if metadatas is not None and len(metadatas) != len(chunks):
            raise ValueError(
                f"metadatas 길이({len(metadatas)})와 chunks 길이({len(chunks)})가 일치하지 않습니다."
            )

        if not self.statutes_store:
            raise RuntimeError("RAG Vector DB (PGVector) is not initialized. Database connection is required.")

        try:
            if metadatas is None:
                metadatas = [{"source": "uploaded_statute"} for _ in chunks]
            await self.statutes_store.aadd_texts(texts=chunks, metadatas=metadatas)
            logger.info(
                f"[RAG] 성공적으로 {len(chunks)}개의 조례 청크를 statutes_collection에 적재했습니다."
            )
        except Exception as e:
            logger.error(f"[RAG Error] Statute Data Insert Error: {e}")
            raise e

    async def retrieve_similar_statutes(
        self, query: str, top_k: int = 3, facility_type: str = None
    ) -> List[dict]:
        """
        [동현 AI 메인] 토론 시나리오 발화 문맥(query)과 가장 유사한 조례 규정 텍스트를
        '기본 조례 콜렉션(statutes_collection)'에서 비동기로 검색합니다.
        """
        if not self.statutes_store:
            raise RuntimeError("RAG Vector DB (PGVector) is not initialized. Database connection is required.")

        try:
            # LangChain의 비동기 유사도 검색 (asimilarity_search_with_relevance_scores) 사용
            # XGBoost Re-ranking을 위해 1차 검색 범위를 넉넉하게 잡습니다 (Recall 단계)
            recall_k = max(top_k * 3, 15)
            search_kwargs = {"k": recall_k}

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

            # 임계치 이상인 문서만 텍스트와 점수로 추출 (Re-ranking 후보군)
            # 초기 검색 임계값을 약간 완화하여 충분한 후보군 확보 (예: 0.36 -> 0.31)
            candidate_chunks = [
                (doc.page_content, float(score))
                for doc, score in docs_with_scores
                if score >= (SIMILARITY_THRESHOLD)
            ]

            # [A-4] XGBoost 기반 Re-ranking (Precision 단계)
            from app.services.xgboost_rag_service import xgboost_rag_service

            print("\n" + "=" * 60)
            print(f"🔍 [XGBoost Re-ranking 전/후 비교 로그] (Query: {query})")
            print("-" * 60)
            print(
                f"▶ 1. PGVector 원본 검색 결과 (총 {len(candidate_chunks)}건 중 상위 3건 미리보기)"
            )
            for i, (chunk, score) in enumerate(candidate_chunks[:3]):
                preview = chunk.replace("\n", " ")[:50] + "..."
                print(f"   [{i + 1}] Vector 점수: {score:.4f} | {preview}")
            if len(candidate_chunks) > 3:
                print("   ... (나머지 생략)")

            # XGBoost 모델(또는 Rule-based)로 재평가 후 최종 top_k 반환 (Dict 리스트)
            final_docs = xgboost_rag_service.rerank_chunks(
                query, candidate_chunks, top_k=top_k
            )

            print("-" * 60)
            print(f"▶ 2. XGBoost Re-ranked 결과 (최종 상위 {len(final_docs)}건)")
            for i, doc_info in enumerate(final_docs):
                preview = doc_info["text"].replace("\n", " ")[:50] + "..."
                print(
                    f"   [{i + 1}] 최종 점수: {doc_info['final_score']:.4f} (원본 Vector: {doc_info['vector_score']:.4f}) | {preview}"
                )
            print("=" * 60 + "\n")

            return final_docs

        except Exception as e:
            logger.error(f"[RAG Error] 유사도 검색 및 Re-ranking 실패: {e}")
            raise e

_vector_db_instance = None

def get_vector_db() -> RagVectorStorage:
    """RagVectorStorage 싱글톤 인스턴스를 지연 생성(Lazy Load)하여 반환합니다."""
    global _vector_db_instance
    if _vector_db_instance is None:
        _vector_db_instance = RagVectorStorage()
    return _vector_db_instance
