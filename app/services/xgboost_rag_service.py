import os
import re
import logging
import numpy as np
import xgboost as xgb
from typing import List, Tuple

logger = logging.getLogger(__name__)


class XGBoostRAGService:
    def __init__(self, model_path: str = None):
        """
        XGBoost Re-ranker 서비스입니다.
        제공된 모델 경로에 파일이 존재하면 해당 모델을 로드합니다.
        모델이 없으면 추후 학습 가능한 규칙 기반(Rule-based) 채점기로 대체 동작합니다.
        """
        self.model = None

        # 기본 디렉토리에 모델 경로 설정
        if not model_path:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            model_path = os.path.join(base_dir, "models", "xgboost_rag_ranker.json")

        self.model_path = model_path
        self._load_model()

    def _load_model(self):
        if xgb and os.path.exists(self.model_path):
            try:
                self.model = xgb.XGBClassifier()
                self.model.load_model(self.model_path)
                logger.info(
                    f"[{self.model_path}] 경로에서 XGBoost 모델을 성공적으로 로드했습니다."
                )
            except Exception as e:
                logger.error(f"XGBoost 모델 로드 실패: {e}")
                self.model = None
        else:
            logger.info(
                "사전 학습된 XGBoost 모델을 찾을 수 없거나 xgboost 패키지가 설치되지 않았습니다. 현재는 규칙 기반(Rule-based) Re-ranking을 사용합니다."
            )

    def _extract_features(
        self, query: str, chunk_text: str, vector_score: float
    ) -> np.ndarray:
        """
        XGBoost 랭킹을 위해 단일 청크에서 입력 변수(Feature)를 추출합니다.
        특징(Features):
        0. vector_score: PGVector에서 나온 원본 코사인 유사도 점수
        1. chunk_length: 청크 텍스트의 길이
        2. keyword_match_count: 쿼리의 핵심 단어가 청크에 등장한 횟수
        3. has_numeric_condition: 청크 내에 특정 수치 조건(예: 50m, 100제곱미터)이 포함되어 있는지 여부
        """
        # 1. 텍스트 길이 (Length)
        chunk_length = len(chunk_text)

        # 2. 키워드 매칭 (쿼리의 명사구를 단순 정확히 매칭)
        # 실제 환경에서는 형태소 분석기를 사용하여 명사를 추출하는 것이 좋습니다.
        # 이 프로토타입에서는 공백 기준으로 분리하여 사용합니다.
        query_terms = [term for term in query.split() if len(term) > 1]
        keyword_match_count = sum(1 for term in query_terms if term in chunk_text)

        # 3. 수치 조건 (숫자 뒤에 m, km, 제곱미터, 대, %, 만원 등의 단위가 붙는 정규식 패턴)
        numeric_pattern = (
            r"\d+[\s]*(m|km|제곱미터|㎡|대|%|만원|미터|제곱|이상|이하|미만|초과)"
        )
        has_numeric = 1.0 if re.search(numeric_pattern, chunk_text) else 0.0

        features = [
            vector_score,
            float(chunk_length),
            float(keyword_match_count),
            has_numeric,
        ]

        return np.array(features)

    def _rule_based_score(self, features: np.ndarray) -> float:
        """
        XGBoost 모델이 아직 학습되지 않았을 때 사용하는 휴리스틱(규칙 기반) 채점 함수입니다.
        """
        v_score, length, kw_count, has_num = features

        # 텍스트가 너무 짧으면 감점 (예: 30자 미만이면 단순히 제목일 가능성이 높음)
        length_penalty = 1.0 if length >= 30 else 0.5

        # 수치 조건이 포함되어 있으면 약간의 가점 부여
        num_boost = 1.1 if has_num == 1.0 else 1.0

        # 키워드 매칭 횟수에 따라 가점 부여
        kw_boost = 1.0 + (kw_count * 0.1)

        # 최종 휴리스틱 점수 계산
        final_score = v_score * length_penalty * kw_boost * num_boost
        return float(final_score)

    def rerank_chunks(
        self, query: str, docs_with_scores: List[Tuple[str, float]], top_k: int = 3
    ) -> List[str]:
        """
        PGVector에서 가져온 초기 청크들을 XGBoost(또는 규칙 기반 휴리스틱)를 사용하여 재순위화(Re-rank)합니다.

        매개변수(Args):
            query (str): 검색 쿼리 또는 시나리오 문맥.
            docs_with_scores (List[Tuple[str, float]]): (청크 텍스트, 벡터 유사도 점수) 튜플의 리스트.
            top_k (int): 재순위화 후 반환할 상위 청크의 개수.

        반환값(Returns):
            List[Dict[str, Any]]: 재순위화된 상위 top_k 개의 청크 정보 딕셔너리 리스트.
        """
        if not docs_with_scores:
            return []

        scored_chunks = []

        for chunk_text, vector_score in docs_with_scores:
            features = self._extract_features(query, chunk_text, vector_score)

            if self.model is not None:
                # XGBoost가 로드되어 있으면, 클래스 1(관련 있음)일 확률을 예측
                # 단일 예측을 위해 배열 형태 변환: (1, num_features)
                feature_matrix = features.reshape(1, -1)
                pred_prob = self.model.predict_proba(feature_matrix)[0][1]
                final_score = float(pred_prob)
            else:
                # 규칙 기반 채점(Fallback) 사용
                final_score = self._rule_based_score(features)

            scored_chunks.append((final_score, chunk_text))

        # 새로운 점수를 기준으로 내림차순 정렬
        scored_chunks.sort(key=lambda x: x[0], reverse=True)

        # 디버깅을 위해 상위 점수 로그 출력
        top_scores_log = [round(score, 4) for score, _ in scored_chunks[:top_k]]
        logger.info(f"[RAG Re-ranker] 최종 상위 {top_k}개 점수: {top_scores_log}")

        # 상위 top_k 개의 청크 정보를 추출하여 반환 (원래 vector_score 유지 필요)
        # 딕셔너리 형태로 반환하여 vector_db에서 참조할 수 있도록 함
        final_results = []
        for i, (final_score, chunk_text) in enumerate(scored_chunks[:top_k]):
            # 원본 docs_with_scores에서 원래 vector_score 찾기
            original_v_score = next(v for t, v in docs_with_scores if t == chunk_text)
            final_results.append(
                {
                    "doc_id": i + 1,
                    "text": chunk_text,
                    "vector_score": float(original_v_score),
                    "final_score": float(final_score),
                    "query": query,
                }
            )

        return final_results


# 앱 전체에서 사용할 싱글톤 인스턴스 생성
xgboost_rag_service = XGBoostRAGService()
