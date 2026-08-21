from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.api.deps import get_db
from app.db.models.simulation import (
    Parcel,
    HearingResultA,
    DebateLog,
    HearingResultB,
)
from app.api.v1.simulations.services import _missing_run_detail

router = APIRouter()


@router.get("/hearings")
async def list_hearings(
    run_id: str,
    domain: str | None = None,
    engine: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """이 실행(run)에서 열린 공청회 목록."""
    if engine is not None and engine not in ("A", "B"):
        raise HTTPException(status_code=400, detail="engine 은 'A' 또는 'B' 다")

    cand_stmt = select(func.count(Parcel.id)).where(Parcel.run_id == run_id)
    if domain is not None:
        cand_stmt = cand_stmt.where(Parcel.domain == domain)
    if await db.scalar(cand_stmt) == 0:
        raise HTTPException(status_code=404, detail=await _missing_run_detail(run_id, domain))

    hearings: list[dict] = []

    a_stmt = (
        select(
            HearingResultA.id.label("simulation_id"),
            HearingResultA.parcel_id,
            HearingResultA.facility_type,
            HearingResultA.css_score,
            HearingResultA.candidate_land_id,
            HearingResultA.created_at,
            HearingResultA.optimal_scenario.isnot(None).label("has_a"),
            HearingResultA.normal_scenario.isnot(None).label("has_b"),
            HearingResultA.worst_scenario.isnot(None).label("has_c"),
            Parcel.rank,
            Parcel.domain,
            Parcel.run_id,
            Parcel.jibun,
            func.count(DebateLog.id).label("debate_log_count"),
        )
        .join(Parcel, Parcel.id == HearingResultA.parcel_id)
        .outerjoin(DebateLog, DebateLog.simulation_id == HearingResultA.id)
        .where(Parcel.run_id == run_id)
        .group_by(HearingResultA.id, Parcel.id)
        .order_by(Parcel.rank.asc().nullslast(), HearingResultA.id.asc())
    )
    if domain is not None:
        a_stmt = a_stmt.where(Parcel.domain == domain)

    if engine != "B":
        rows = (await db.execute(a_stmt)).all()

        latest_of: dict[int, int] = {}
        for r in rows:
            if r.simulation_id > latest_of.get(r.parcel_id, -1):
                latest_of[r.parcel_id] = r.simulation_id

        for r in rows:
            is_latest = latest_of[r.parcel_id] == r.simulation_id
            scenario_code = (
                "A" if r.has_a else "B" if r.has_b else "C" if r.has_c else None
            )
            hearings.append(
                {
                    "simulation_id": r.simulation_id,
                    "engine": "A",
                    "parcel_id": r.parcel_id,
                    "rank": r.rank,
                    "jibun": r.jibun,
                    "domain": r.domain,
                    "run_id": r.run_id,
                    "facility_type": r.facility_type,
                    "candidate_land_id": r.candidate_land_id,
                    "css_score": float(r.css_score)
                    if r.css_score is not None
                    else None,
                    "scenario_code": scenario_code,
                    "debate_log_count": r.debate_log_count,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "is_latest_for_parcel": is_latest,
                    "result_url": f"/api/v1/simulations/results/{r.parcel_id}"
                    if is_latest
                    else None,
                    "pdf_url": f"/api/v1/simulations/results/{r.parcel_id}/pdf"
                    if is_latest
                    else None,
                }
            )

    if engine != "A":
        b_stmt = (
            select(
                HearingResultB.id.label("hearing_id"),
                HearingResultB.parcel_id,
                HearingResultB.facility_type,
                HearingResultB.topic,
                HearingResultB.purpose,
                func.jsonb_array_length(HearingResultB.personas).label("persona_count"),
                HearingResultB.message_count,
                HearingResultB.created_at,
                Parcel.rank,
                Parcel.domain,
                Parcel.run_id,
                Parcel.jibun,
            )
            .join(Parcel, Parcel.id == HearingResultB.parcel_id)
            .where(Parcel.run_id == run_id)
            .order_by(Parcel.rank.asc().nullslast(), HearingResultB.id.asc())
        )
        if domain is not None:
            b_stmt = b_stmt.where(Parcel.domain == domain)

        for r in (await db.execute(b_stmt)).all():
            hearings.append(
                {
                    "hearing_id": r.hearing_id,
                    "engine": "B",
                    "parcel_id": r.parcel_id,
                    "rank": r.rank,
                    "jibun": r.jibun,
                    "domain": r.domain,
                    "run_id": r.run_id,
                    "facility_type": r.facility_type,
                    "topic": r.topic,
                    "purpose": r.purpose,
                    "persona_count": r.persona_count,
                    "message_count": r.message_count,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "result_url": f"/api/v1/simulations/hearings/b/{r.hearing_id}",
                }
            )

    hearings.sort(
        key=lambda h: (
            h["rank"] is None,
            h["rank"] if h["rank"] is not None else 0,
            h["created_at"] or "",
        )
    )

    return {
        "run_id": run_id,
        "domain": domain,
        "engine": engine,
        "count": len(hearings),
        "hearings": hearings,
    }


@router.get("/hearings/b/{hearing_id}")
async def get_hearing_b(hearing_id: int, db: AsyncSession = Depends(get_db)):
    """B 다인 토론 결과 1건."""
    stmt = (
        select(
            HearingResultB,
            Parcel.rank,
            Parcel.domain,
            Parcel.run_id,
            Parcel.jibun,
        )
        .join(Parcel, Parcel.id == HearingResultB.parcel_id)
        .where(HearingResultB.id == hearing_id)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"hearing_id={hearing_id} 의 B 다인 토론 결과가 없다.",
        )
    h, rank, domain, run_id, jibun = row

    return {
        "hearing_id": h.id,
        "engine": "B",
        "parcel_id": h.parcel_id,
        "rank": rank,
        "jibun": jibun,
        "domain": domain,
        "run_id": run_id,
        "facility_type": h.facility_type,
        "topic": h.topic,
        "purpose": h.purpose,
        "personas": h.personas,
        "message_count": h.message_count,
        "result_json": h.result_json,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }
