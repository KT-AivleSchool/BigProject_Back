from fastapi import APIRouter
from app.api.v1.upload.routes_domains import router as domains_router
from app.api.v1.upload.routes_regulations import router as regulations_router
from app.api.v1.upload.routes_data import router as data_router

router = APIRouter()

router.include_router(domains_router)
router.include_router(regulations_router)
router.include_router(data_router)
