from collections import Counter
from typing import List, Dict, Any
from app.core.stakeholder_mode.schemas.interest_profile import StakeholderInterestProfile


class PersonaDiversityValidator:
    """
    이해관계 프로필 간의 차별성을 검사하는 검증기
    """
    def validate(self, profiles: List[StakeholderInterestProfile]) -> List[Dict[str, Any]]:
        issues = []

        goals = [self._normalize(p.primary_goal) for p in profiles]
        goal_counts = Counter(goals)

        for goal, count in goal_counts.items():
            if goal and count >= 2:
                affected = [
                    p.stakeholder_id
                    for p in profiles
                    if self._normalize(p.primary_goal) == goal
                ]
                issues.append({
                    "persona_ids": affected,
                    "field": "primary_goal",
                    "issue_type": "duplicate_goal",
                    "message": f"동일한 핵심 목표가 {count}개 페르소나에 반복됩니다.",
                    "severity": "warning",
                })

        for profile in profiles:
            if not profile.expected_benefits:
                issues.append({
                    "persona_ids": [profile.stakeholder_id],
                    "field": "expected_benefits",
                    "issue_type": "missing_benefit",
                    "message": "이해관계자의 직접 이익이 정의되지 않았습니다.",
                    "severity": "error",
                })

            if not profile.expected_costs:
                issues.append({
                    "persona_ids": [profile.stakeholder_id],
                    "field": "expected_costs",
                    "issue_type": "missing_cost",
                    "message": "이해관계자의 직접 비용이 정의되지 않았습니다.",
                    "severity": "error",
                })

            if not profile.unique_questions:
                issues.append({
                    "persona_ids": [profile.stakeholder_id],
                    "field": "unique_questions",
                    "issue_type": "missing_unique_question",
                    "message": "고유 질문이 정의되지 않았습니다.",
                    "severity": "error",
                })

        return issues

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.lower().split())
