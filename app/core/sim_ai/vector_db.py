from typing import List


# [동현님 담당] pgvector Vector DB 연결 및 RAG 문서 적재/조회 모듈
class RagVectorStorage:
    def __init__(self):
        # TODO: app/config.py의 OPENAI_API_KEY를 사용하여 임베딩 모델을 활성화해 주세요.
        # self.embeddings = OpenAIEmbeddings(openai_api_key=settings.OPENAI_API_KEY)
        pass

    def add_document_chunks(self, document_id: str, chunks: List[str]):
        """
        [동현님 담당] 업로드된 조례집 PDF/HWP 텍스트 청크를 벡터화하여 pgvector DB에 적재합니다.
        """
        # TODO: pgvector 테이블에 chunk 데이터와 embedding vector를 INSERT하는 로직을 작성해 주세요.
        pass

    def retrieve_similar_statutes(self, query: str, top_k: int = 3) -> List[str]:
        """
        [동현님 담당] 토론 진행 시 발화 문맥(query)과 가장 유사한 조례 규정 텍스트를 pgvector에서 조회합니다.
        """
        # TODO: Cosine Similarity 쿼리를 사용하여 유사도 스코어가 높은 조례 문서 조각 목록을 반환해 주세요.
        return [
            "서울특별시 용산구 금연 환경 조성 조례 제4조: 주거지역 인근 10m 이내 금연구역 버퍼 지정",
            "서울특별시 도로관리 조례 시행규칙 제5조: 시설물의 점용 면적 제한",
        ]
