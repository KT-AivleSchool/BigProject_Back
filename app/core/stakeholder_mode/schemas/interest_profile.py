from enum import StrEnum
from pydantic import BaseModel, Field


class RiskTolerance(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DecisionAuthority(StrEnum):
    AFFECTED = "affected"
    USER = "user"
    OPERATOR = "operator"
    REGULATOR = "regulator"
    BUDGET_OWNER = "budget_owner"
    EXPERT = "expert"


class StakeholderInterestProfile(BaseModel):
    stakeholder_id: str

    primary_goal: str = Field(..., description="이 페르소나가 가장 달성하려는 것")
    success_definition: list[str] = Field(..., description="무엇이 되면 성공이라고 보는지")

    expected_benefits: list[str] = Field(..., description="사업으로 얻을 수 있는 직접 이익")
    expected_costs: list[str] = Field(..., description="사업 때문에 부담할 비용·불편")
    major_risks: list[str] = Field(..., description="우려하는 위험")

    protected_interests: list[str] = Field(..., description="반드시 보호하려는 가치·권익")
    red_lines: list[str] = Field(..., description="절대 수용할 수 없는 조건")
    negotiable_conditions: list[str] = Field(..., description="협상 가능한 조건")

    spatial_focus_tags: list[str] = Field(..., description="GIS에서 우선 조회할 항목")
    required_evidence_types: list[str] = Field(..., description="판단에 필요한 근거 유형")
    preferred_metrics: list[str] = Field(..., description="판단에 사용할 수치")

    unique_questions: list[str] = Field(..., description="다른 페르소나와 구분되는 질문")

    decision_authority: DecisionAuthority = Field(..., description="영향자·이용자·운영자·규제자 등 역할")
    risk_tolerance: RiskTolerance = Field(..., description="위험을 어느 정도 수용하는지")
    time_horizon: str = Field(..., description="단기·장기 중 어느 관점을 중시하는지")

    likely_initial_position: str = Field(..., description="예상 초기 입장")
    position_reason: str = Field(..., description="입장의 이유")

    forbidden_assumptions: list[str] = Field(default_factory=list, description="이 역할이 함부로 가정하면 안 되는 것")
