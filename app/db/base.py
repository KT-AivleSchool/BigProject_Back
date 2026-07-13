# Alembic 마이그레이션 도구가 모든 ORM 모델을 한 번에 가져올 수 있도록 모으는 파일
# DDL 테이블 추가 시 아래에 Import를 추가해야 함

from app.db.session import Base
from app.db.models.user import User
from app.db.models.simulation import Parcel, ConflictSimulation
from app.db.models.spatial import (
    District,
    DongBoundary,
    RestrictedZone,
    ChildcareCenter,
    TransitStation,
    CommercialShop,
    CadastralLand,
    TrashBin,
    IllegalDumpingZone,
)
from app.db.models.stats import (
    TransitPassenger,
    PopulationStat,
    CivilComplaint,
    AgeDemographics,
)

__all__ = [
    "Base",
    "User",
    "Parcel",
    "ConflictSimulation",
    "District",
    "DongBoundary",
    "RestrictedZone",
    "ChildcareCenter",
    "TransitStation",
    "CommercialShop",
    "CadastralLand",
    "TrashBin",
    "IllegalDumpingZone",
    "TransitPassenger",
    "PopulationStat",
    "CivilComplaint",
    "AgeDemographics",
]

