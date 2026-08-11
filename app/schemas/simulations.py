from pydantic import BaseModel, Field
from typing import Dict, Optional


class StreamRequest(BaseModel):
    """화면5 A 대립 토론 시작 요청.

    🔴 `audit_data`(프런트가 감리 결과 JSON 을 실어 보내던 칸)를 **뺐다**
       (2026-08-11, 프런트 합의). 핸들러가 한 번도 읽지 않았고, 감리 근거는
       `parcel_id` → `booth_candidates` 행의 `(domain, run_id, facility_type)` 로
       백엔드가 조달한다(`candidate_context`). 요청으로 받으면 **후보지와 근거가
       어긋날 수 있다** — 요청 본문에서 도메인을 안 받는 이유와 같다.
       제거 순서는 **백엔드가 먼저**다: pydantic 은 모르는 키를 무시하므로
       프런트가 아직 `audit_data: {}` 를 보내도 통과한다. 반대로 하면 422 다.

    ⚠ 이 클래스는 파일 **맨 아래에 같은 이름으로 한 번 더** 선언돼 있었다.
      나중 선언이 이겨서 여기 `Field(description=…)` 은 전부 죽은 값이었다 —
      `/docs` 스키마에 설명이 안 나오던 이유다. 아래 것을 지웠다.
    """

    parcel_id: int = Field(..., description="시뮬레이션할 적격 후보지 필지 ID")
    facility_type: str = Field(..., description="건립할 시설의 종류 (예: 흡연부스)")


class SimulationRunRequest(BaseModel):
    parcel_id: int = Field(..., description="시뮬레이션할 적격 후보지 필지 ID")
    ahp_model_id: Optional[int] = Field(
        None, description="적용할 가중치 락 세트 ID (생략 시 기본 락 세트 적용)"
    )


class ScenarioDetail(BaseModel):
    scenario: str = Field(..., description="시나리오 종류 (A, B, C)")
    scenario_description: str = Field(..., description="시나리오 제목/설명")
    final_acceptance_score: float = Field(..., description="최종 수용도 점수")
    reason: str = Field(..., description="시나리오 도출 이유")
    summary: str = Field(..., description="시나리오 전개 요약")
    conflict_risk_index: float = Field(..., description="갈등 위험 지수 (0.0 ~ 100.0)")
    risk_reason: str = Field(..., description="갈등 위험 지수 산출 이유")


class SimulationResultResponse(BaseModel):
    parcel_id: int = Field(..., description="필지 고유 ID")
    conflict_sensitivity_score: float = Field(
        ..., description="갈등 민감도 지수 (CSS - 0.0 ~ 10.0)"
    )
    conflict_factors: Dict[str, float] = Field(
        ...,
        description="상세 갈등 인자 영향도 (예: 소음피해, 임대료상승, 보행혼잡, 경관훼손 등)",
    )
    scenario: ScenarioDetail = Field(
        ..., description="토론 결과로 도출된 최종 1개의 시나리오 정보"
    )
    debate_logs: Optional[list] = Field(
        None, description="토론에 참여한 페르소나들의 전체 대화 내역 리스트"
    )


class SseMessagePacket(BaseModel):
    sender: str = Field(
        ..., description="발화자 구분 (주민대표, 상인대표, 조정공무원, 시스템)"
    )
    message: str = Field(..., description="실시간 출력 텍스트 토큰")
    is_finished: bool = Field(False, description="스트리밍 종료 여부")
