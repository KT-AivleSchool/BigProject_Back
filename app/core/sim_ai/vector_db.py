from typing import List
import logging
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import PGVector
from app.config import settings, DB_CONNECT_TIMEOUT

# 🔴 PGVector 는 psycopg(libpq) 로 붙는다 — 여기는 `connect_timeout` 이 맞다.
#    (asyncpg 를 쓰는 `db/session.py` 는 `timeout` 이다. 이름이 다르니 복붙 금지.)
_PGVECTOR_ENGINE_ARGS = {"connect_args": {"connect_timeout": DB_CONNECT_TIMEOUT}}

logger = logging.getLogger(__name__)

# 유사도 임계치 — 3-small 적재분(시드 6종·222청크)에서 실측한 값.
#   관련 질의-문서 쌍 최저 0.3602 / 무관 쌍 최고 0.4416 (0.36~0.44 구간 중첩).
#   0.36 = 관련을 하나도 잃지 않는 최댓값(관련 25/25 유지, 무관 통과 20→8건).
#   조례 인용은 누락(근거 없이 토론 진행)이 오탐보다 치명적이라 재현율 우선.
#   시드 코퍼스가 크게 바뀌면 재측정 필요.
SIMILARITY_THRESHOLD = 0.31


def statutes_collection_name(domain: str) -> str:
    """도메인 하나 = 조례 콜렉션 하나. `statutes_<도메인>`.

    🔴 2026-08-24. 예전엔 `statutes_collection` **하나**에 전 도메인을 몰아넣고
       `facility_type` 메타데이터 태그로 갈랐다. 그 한 줄이 위로 세 가지를 만들었다 —
         ① 업로드가 **시설 종류를 필수로 물어야** 했다(모르면 400). 화면1 에
            「시설 유형」 칸이 있는 이유가 이것뿐이었다.
         ② 적재 태그와 조회 필터가 **정확일치**라 한 글자만 달라도 전량 0건인데
            **안 터지고 근거만 빈다**(`ingest_statutes.py` 머리말의 그 경고).
         ③ 도메인을 지워도 같은 시설을 쓰는 **남의 토론에 조문이 인용**됐다
            (`user_input_pruner.drop_vector_chunks` 가 적어둔 위험).
       칸을 나누면 셋 다 사라진다 — 격리가 **태그가 아니라 구조**가 된다.
       조례 **파일** 쪽은 처음부터 이랬다: `datasets/<도메인>/law/` 폴더가 곧 격리이고
       거기엔 시설 태그가 한 개도 없다. 벡터 쪽만 축이 달랐던 것이다.

    ⚠ 시설 종류는 사라지지 않는다 — **질의문 안에** 그대로 남는다
      (`candidate_context._ORDINANCE_QUERY_TERMS` 와 함께). 의미검색이 알아서
      고르게 하는 것이 원래 설계였다(아래 `add_statute_chunks` 주석 참조).
    """
    d = (domain or "").strip()
    if not d:
        # 빈 도메인을 허용하면 `statutes_` 라는 공용 칸이 생겨 옛 구조로 되돌아간다.
        raise ValueError("domain 이 비었다 — 조례 콜렉션은 도메인마다 따로다.")
    return f"statutes_{d}"


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

        # 1. 조례/법규 콜렉션 — **도메인마다 하나**. 요청 시점에 만들어 캐시한다.
        #    PGVector 의 `__init__` 이 콜렉션 행을 get_or_create 하므로 별도 생성 절차가 없다.
        #    🔴 예전엔 여기서 콜렉션 하나를 만들고 실패를 warning 으로 넘기며 `None` 을
        #       넣었다. 그러면 적재·검색이 한참 뒤에 「초기화 안 됨」으로 죽어서
        #       **진짜 사유(접속 실패)가 사라진다**(원칙 1). 지금은 그 자리에서 raise 한다.
        self._statutes_stores: dict[str, PGVector] = {}

        # 2. 사후 검증된 피드백 콜렉션 (Model Collapse 예방 및 Audit AI 전용)
        try:
            self.feedback_store = PGVector(
                collection_name="feedback_collection",
                connection_string=self.connection_string,
                embedding_function=self.embeddings,
                engine_args=_PGVECTOR_ENGINE_ARGS,
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

    def statutes_store_of(self, domain: str) -> PGVector:
        """그 도메인의 조례 콜렉션. 없으면 만든다(PGVector 가 get_or_create 한다)."""
        name = statutes_collection_name(domain)
        store = self._statutes_stores.get(name)
        if store is None:
            store = PGVector(
                collection_name=name,
                connection_string=self.connection_string,
                embedding_function=self.embeddings,
                engine_args=_PGVECTOR_ENGINE_ARGS,
            )
            self._statutes_stores[name] = store
        return store

    async def add_statute_chunks(
        self, domain: str, chunks: List[str], metadatas: List[dict] = None
    ):
        """조례 텍스트 청크를 **그 도메인의** 콜렉션에 적재합니다.

        (시설 종류는 사전에 지정하지 않고, 토론 시 AI가 의미(Semantic) 검색을 통해
         관련 조례를 스스로 찾아냅니다.)
        ↑ 이 문장은 이 모듈이 처음 쓰였을 때부터 있었다. 콜렉션이 하나뿐이던 시절엔
          거짓이 됐다가(`facility_type` 정확일치 필터가 걸렸다) 도메인별 칸으로
          나누면서 **다시 참이 됐다.**
        """
        if metadatas is not None and len(metadatas) != len(chunks):
            raise ValueError(
                f"metadatas 길이({len(metadatas)})와 chunks 길이({len(chunks)})가 일치하지 않습니다."
            )

        store = self.statutes_store_of(domain)
        try:
            if metadatas is None:
                metadatas = [{"source": "uploaded_statute"} for _ in chunks]
            await store.aadd_texts(texts=chunks, metadatas=metadatas)
            logger.info(
                f"[RAG] 성공적으로 {len(chunks)}개의 조례 청크를 "
                f"{statutes_collection_name(domain)} 에 적재했습니다."
            )
        except Exception as e:
            logger.error(f"[RAG Error] Statute Data Insert Error: {e}")
            raise e

    def delete_statute_chunks(self, domain: str, **equals) -> int:
        """그 도메인 콜렉션에서 청크를 지운다. 지운 행 수 반환.

        `equals` 를 주면 metadata 완전일치분만, 안 주면 **그 도메인 전량**이다.
        같은 파일을 다시 올리면 옛 청크가 남아 **같은 조문이 두 번 인용**되므로
        업로드 경로는 `upload_filename` 으로 좁혀 부른다.
        LangChain PGVector 에는 메타데이터 조건 삭제가 없어 여기서 SQL 로 한다
        (콜렉션 행은 건드리지 않는다 — 지우면 재적재가 "Collection not found" 로 죽는다).

        🔴 예전엔 조건이 비면 **거부**했다. 콜렉션이 하나라 「조건 없음 = 전 도메인
           삭제」였기 때문이다. 지금은 콜렉션 자체가 도메인이라 조건 없는 삭제의
           범위가 **그 도메인 하나로 구조적으로 묶인다** — 거부할 이유가 없어졌다.
           (`user_input_pruner.drop_vector_chunks` 가 바로 이걸 하고 싶어 했다.)
        """
        from sqlalchemy import create_engine, text as sql_text

        where = " AND ".join(f"cmetadata->>'{k}' = :v{i}" for i, k in enumerate(equals))
        params = {f"v{i}": str(v) for i, v in enumerate(equals.values())}

        engine = create_engine(self.connection_string)
        try:
            with engine.begin() as conn:
                row = conn.execute(
                    sql_text("SELECT uuid FROM langchain_pg_collection WHERE name = :n"),
                    {"n": statutes_collection_name(domain)},
                ).fetchone()
                if row is None:
                    return 0
                res = conn.execute(
                    sql_text(
                        "DELETE FROM langchain_pg_embedding WHERE collection_id = :cid"
                        + (f" AND {where}" if where else "")
                    ),
                    {"cid": row[0], **params},
                )
                return res.rowcount or 0
        finally:
            engine.dispose()

    def count_statute_chunks(self, domain: str) -> int:
        """그 도메인 콜렉션의 청크 수. 콜렉션 자체가 없으면 0.

        「적재를 안 했다」와 「적재는 했는데 유사도가 안 나온다」를 가르는 데 쓴다 —
        접으면 화면·로그가 둘 다 「관련 조례 없음」이라고 말한다(원칙 4).
        """
        from sqlalchemy import create_engine, text as sql_text

        engine = create_engine(self.connection_string)
        try:
            with engine.begin() as conn:
                row = conn.execute(
                    sql_text(
                        "SELECT count(*) FROM langchain_pg_embedding e "
                        "JOIN langchain_pg_collection c ON c.uuid = e.collection_id "
                        "WHERE c.name = :n"
                    ),
                    {"n": statutes_collection_name(domain)},
                ).fetchone()
                return int(row[0]) if row else 0
        finally:
            engine.dispose()

    async def retrieve_similar_statutes(
        self, query: str, domain: str, top_k: int = 3
    ) -> List[dict]:
        """토론 발화 문맥(query)과 가장 유사한 조례를 **그 도메인 콜렉션**에서 찾는다.

        🔴 `facility_type` 필터는 없앴다(2026-08-24). 격리가 태그가 아니라 콜렉션이라
           다른 도메인 조문이 섞일 길 자체가 없다 — 필터는 그 시절의 대용품이었다.
           시설 종류는 **질의문 안에** 그대로 남아 의미검색에 쓰인다
           (`candidate_context.retrieve_ordinance_texts` 가 조립한다).
        """
        try:
            store = self.statutes_store_of(domain)

            # LangChain의 비동기 유사도 검색 (asimilarity_search_with_relevance_scores) 사용
            # XGBoost Re-ranking을 위해 1차 검색 범위를 넉넉하게 잡습니다 (Recall 단계)
            recall_k = max(top_k * 3, 15)

            # [A-3] 유사도 임계치 검사 및 점수 포함 검색
            docs_with_scores = await store.asimilarity_search_with_relevance_scores(
                query, k=recall_k
            )

            # 0건이면 **적재를 안 한 것인지** 유사도가 안 나온 것인지 갈린다.
            # 둘은 처치가 다르므로 구분해서 남긴다 — 조용히 빈 배열만 주면
            # 둘 다 "관련 조례 없음" 으로 읽힌다(원칙 4).
            if not docs_with_scores:
                n = self.count_statute_chunks(domain)
                if n == 0:
                    logger.warning(
                        f"[RAG] {statutes_collection_name(domain)} 에 청크가 0개다 — "
                        f"조례를 아직 적재하지 않았다(검색 실패가 아니다)."
                    )
                else:
                    logger.warning(
                        f"[RAG] {statutes_collection_name(domain)} 에 {n}개 청크가 있는데 "
                        f"이 질의로는 0건이다 — 유사도가 안 나온 것이다."
                    )
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
            print(f"[XGBoost Re-ranking 전/후 비교 로그] (Query: {query})")
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
