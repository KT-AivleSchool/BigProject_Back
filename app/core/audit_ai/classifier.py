import re
import math
from collections import Counter

from app.core.sim_ai.scenario import scenario_code, scenario_compare_text


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

        🔴 2026-08-11 수정. 예전엔 `sc.get("scenario_type", "A")` 였다 —
           실제 키는 `scenario` 이고(A 엔진 `reporter.txt:11`), 이 코드는 그 스키마가
           정해지기 **11일 전**에 쓰였다. 그래서 기본값 `"A"` 가 그대로 나가
           `matched_scenario` 가 **항상 `"A"`** 였다. 예외는 안 났다.
           코드 추출은 `app/core/sim_ai/scenario.py` 한 곳에서만 한다.
        """
        best_scenario = None
        max_similarity = 0.0
        # 「비교할 텍스트가 하나도 없다」와 「비교했는데 안 겹친다」는 다른 사실이다.
        comparable = 0

        for sc in predicted_scenarios:
            text = scenario_compare_text(sc)
            if not text:
                continue
            comparable += 1

            similarity = self._get_cosine_similarity(ocr_text, text)
            if similarity > max_similarity:
                max_similarity = similarity
                # 🔴 못 뽑으면 `None` 이다. 여기서 "A" 를 넣으면 다시 같은 사고가 난다.
                best_scenario = scenario_code(sc)

        # 대조할 시나리오 텍스트가 아예 없었다 → 판정을 한 게 아니다(원칙 4).
        if not comparable:
            return {
                "matched_scenario": None,
                "similarity_score": 0.0,
                "classification_status": "NO_PREDICTION",
            }

        # 모든 시나리오와 공통 단어가 전혀 없는 경우 → 분류 불가 상태 명시
        # (기존 0.82 매직 넘버 하드코딩 Fallback 제거 — 리뷰 반영)
        if max_similarity == 0.0:
            return {
                "matched_scenario": None,
                "similarity_score": 0.0,
                "classification_status": "UNCLASSIFIED",
            }

        # 유사도 기반 적합성 상태 결정 (0.8 이상: COMPLIANT, 0.5~0.8: WARNING, 미만: DEVIATED)
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
