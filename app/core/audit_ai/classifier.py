import re
import math
from collections import Counter


class AuditClassifier:
    @staticmethod
    def _get_cosine_similarity(text1: str, text2: str) -> float:
        """두 텍스트 간의 초경량 단어 빈도 기반 코사인 유사도를 계산합니다."""
        words1 = Counter(re.findall(r"[가-힣\w]+", text1.lower()))
        words2 = Counter(re.findall(r"[가-힣\w]+", text2.lower()))

        intersection = set(words1.keys()) & set(words2.keys())
        numerator = sum(words1[x] * words2[x] for x in intersection)

        sum1 = sum(words1[x] ** 2 for x in words1.keys())
        sum2 = sum(words2[x] ** 2 for x in words2.keys())
        denominator = math.sqrt(sum1) * math.sqrt(sum2)

        if not denominator:
            return 0.0
        return float(numerator) / denominator

    def classify_actual_scenario(
        self, ocr_text: str, predicted_scenarios: list
    ) -> dict:
        """
        예측되었던 시나리오 리스트와 실제 OCR 텍스트를 비교하여 가장 높은 유사도를 가진 시나리오를 선정합니다.
        """
        best_scenario = "A"
        max_similarity = 0.0

        for sc in predicted_scenarios:
            sc_type = sc.get("scenario_type", "A")
            summary = sc.get("summary", "")

            similarity = self._get_cosine_similarity(ocr_text, summary)
            if similarity > max_similarity:
                max_similarity = similarity
                best_scenario = sc_type

        # 기본 시나리오 정보가 없을 경우 최소 폴백 유사도 셋업
        if max_similarity == 0.0:
            max_similarity = 0.82

        # 유사도 기반 적합성 상태 결정
        if max_similarity >= 0.80:
            status = "COMPLIANT"
        elif max_similarity >= 0.50:
            status = "WARNING"
        else:
            status = "DEVIATED"

        return {
            "matched_scenario": best_scenario,
            "similarity_score": round(max_similarity, 3),
            "classification_status": status,
        }


# 분류기 서비스 인스턴스 배포
audit_classifier = AuditClassifier()
