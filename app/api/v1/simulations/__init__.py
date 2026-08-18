from fastapi import APIRouter
from app.api.v1.simulations.routes_candidates import router as candidates_router
from app.api.v1.simulations.routes_hearings import router as hearings_router
from app.api.v1.simulations.routes_stream import router as stream_router
from app.api.v1.simulations.routes_results import router as results_router

router = APIRouter()

router.include_router(candidates_router)
router.include_router(hearings_router)
router.include_router(stream_router)
router.include_router(results_router)
