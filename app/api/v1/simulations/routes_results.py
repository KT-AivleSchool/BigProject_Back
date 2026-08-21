from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.schemas.simulations import SimulationResultResponse
from app.api.deps import get_db
from app.db.models.simulation import HearingResultA

router = APIRouter()


@router.get("/results/{parcel_id}", response_model=SimulationResultResponse)
async def get_simulation_results(parcel_id: int, db: AsyncSession = Depends(get_db)):
    """모의 심의 토론 종결 후 최종 도출된 3대 시나리오 예측치 조회 API."""
    result = await db.execute(
        select(HearingResultA)
        .where(HearingResultA.parcel_id == parcel_id)
        .order_by(HearingResultA.id.desc())
    )
    sim_data = result.scalars().first()

    if not sim_data:
        raise HTTPException(
            status_code=404,
            detail=f"필지 ID {parcel_id}에 대한 기존 모의 심의 시뮬레이션 이력이 존재하지 않습니다. 먼저 토론 스트리밍을 가동해 주세요.",
        )

    res_json = sim_data.result_json or {}
    raw_scenarios = res_json.get("scenarios", [])

    if raw_scenarios and isinstance(raw_scenarios, list) and len(raw_scenarios) > 0:
        sc_data = raw_scenarios[0]
        scenario_obj = {
            "scenario": str(
                sc_data.get("scenario") or sc_data.get("scenario_type") or "알 수 없음"
            ),
            "scenario_description": str(
                sc_data.get("scenario_description")
                or sc_data.get("title")
                or "설명 없음"
            ),
            "final_acceptance_score": float(
                sc_data.get("final_acceptance_score") or 0.0
            ),
            "reason": str(sc_data.get("reason") or "이유 없음"),
            "summary": str(sc_data.get("summary") or "요약 없음"),
            "conflict_risk_index": float(sc_data.get("conflict_risk_index") or 0.0),
            "risk_reason": str(sc_data.get("risk_reason") or "갈등 위험 이유 없음"),
        }
    elif isinstance(raw_scenarios, dict) and "scenario" in raw_scenarios:
        scenario_obj = raw_scenarios
    else:
        raise HTTPException(
            status_code=503,
            detail=(
                "[OPENAI_QUOTA_EXCEEDED] AI 모의 심의 토론 결과 시나리오가 존재하지 않습니다. "
                "OpenAI API Quota가 초과되었거나 토론이 정상 완료되지 않았습니다. "
                "API 키 잔액을 확인하고 토론을 다시 시작해 주세요."
            ),
        )

    css_score = res_json.get("conflict_sensitivity_score")
    conflict_factors = res_json.get("conflict_factors")

    if css_score is None or conflict_factors is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "[OPENAI_QUOTA_EXCEEDED] 갈등 민감도 지수(CSS) 데이터가 존재하지 않습니다. "
                "OpenAI API Quota가 초과되어 AI 분석이 완료되지 않았습니다."
            ),
        )

    css_score = float(css_score)
    debate_logs = res_json.get("debate_logs", [])

    return {
        "parcel_id": parcel_id,
        "conflict_sensitivity_score": css_score,
        "conflict_factors": conflict_factors,
        "scenario": scenario_obj,
        "debate_logs": debate_logs,
    }


@router.get("/results/{parcel_id}/pdf")
@router.get("/report/{parcel_id}")
async def download_feasibility_report_pdf(
    parcel_id: int, db: AsyncSession = Depends(get_db)
):
    """Step 5 최종 입지 선정 타당성 보고서 PDF 실시간 다운로드 API."""
    result = await db.execute(
        select(HearingResultA)
        .where(HearingResultA.parcel_id == parcel_id)
        .order_by(HearingResultA.id.desc())
    )
    sim_data = result.scalars().first()

    if not sim_data:
        raise HTTPException(
            status_code=404,
            detail="해당 필지의 심의 시뮬레이션 이력이 존재하지 않아 보고서를 출력할 수 없습니다.",
        )

    res_json = sim_data.result_json or {}
    if not res_json:
        raise HTTPException(
            status_code=404,
            detail="[SIMULATION_NOT_FOUND] 시뮬레이션 결과 데이터가 존재하지 않습니다.",
        )

    candidate_lat = res_json.get("candidate_lat")
    candidate_lng = res_json.get("candidate_lng")
    if (
        candidate_lat is None
        or candidate_lng is None
        or (candidate_lat == 0.0 and candidate_lng == 0.0)
    ):
        raise HTTPException(
            status_code=422,
            detail="[GEOCODING_FAILED] 시뮬레이션 대상의 유효한 위경도 좌표가 존재하지 않습니다.",
        )

    css_score = res_json.get("conflict_sensitivity_score")
    if css_score is None:
        raise HTTPException(
            status_code=503,
            detail="[AI_SCORE_UNAVAILABLE] 갈등 민감도 지수(CSS) 연산에 실패했거나 아직 완료되지 않았습니다.",
        )

    raw_scenarios = res_json.get("scenarios", [])
    scenario_obj = {}
    if raw_scenarios and isinstance(raw_scenarios, list) and len(raw_scenarios) > 0:
        scenario_obj = raw_scenarios[0]
    elif isinstance(raw_scenarios, dict) and "scenario" in raw_scenarios:
        scenario_obj = raw_scenarios

    report_data = {
        "candidate_jibun": res_json.get("candidate_jibun", "알 수 없음"),
        "candidate_lat": candidate_lat,
        "candidate_lng": candidate_lng,
        "facility_type": res_json.get("facility_type", "지정되지 않음"),
        "conflict_sensitivity_score": css_score,
        "ahp_weights": res_json.get("ahp_weights", {}),
        "scenario": scenario_obj,
        "debate_logs": res_json.get("debate_logs", []),
    }

    try:
        from app.services.pdf_service import pdf_builder

        pdf_file = await pdf_builder.generate_feasibility_pdf(report_data)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"PDF 생성 중 오류가 발생했습니다. 서버 환경(Playwright 설치)을 확인해 주세요. 오류: {str(e)}",
        )

    filename = f"OmniSite_Feasibility_Report_{parcel_id}.pdf"
    return StreamingResponse(
        pdf_file,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
