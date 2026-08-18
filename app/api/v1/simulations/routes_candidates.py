from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.api.deps import get_db
from app.db.models.simulation import Parcel

router = APIRouter()


@router.get("/candidates")
async def list_booth_candidates(
    domain: str,
    run_id: str | None = None,
    limit: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """STEP4 Top-N 후보점 목록."""
    if limit is not None and limit < 1:
        raise HTTPException(status_code=400, detail="limit 은 1 이상이어야 한다")

    if run_id is None:
        run_id = await db.scalar(
            select(Parcel.run_id)
            .where(Parcel.domain == domain)
            .order_by(Parcel.id.desc())
            .limit(1)
        )

    stmt = (
        select(
            Parcel,
            func.ST_Y(Parcel.geom).label("lat"),
            func.ST_X(Parcel.geom).label("lng"),
        )
        .where(Parcel.domain == domain, Parcel.run_id == run_id)
        .order_by(Parcel.rank.asc().nullslast())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    rows = result.all()
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"domain='{domain}' run_id={run_id!r} 의 후보점이 "
                f"booth_candidates 에 없다. "
                f"적재: python scripts/load_topn_candidates.py {domain} --yes"
            ),
        )

    return {
        "domain": domain,
        "run_id": run_id,
        "count": len(rows),
        "candidates": [
            {
                "parcel_id": p.id,
                "rank": p.rank,
                "score": p.score,
                "pnu": p.pnu,
                "jibun": p.jibun,
                "facility_type": p.facility_type,
                "run_id": p.run_id,
                "land_id": p.land_id,
                "lat": lat,
                "lng": lng,
            }
            for p, lat, lng in rows
        ],
    }
