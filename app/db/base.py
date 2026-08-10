# Alembic 마이그레이션 도구가 모든 ORM 모델을 한 번에 가져올 수 있도록 모으는 파일
# DDL 테이블 추가 시 아래에 Import를 추가해야 함

from app.db.session import Base
from app.db.models.user import User
from app.db.models.simulation import Parcel, ConflictSimulation, DebateLog
from app.db.models.precedent import VerifiedPrecedent
from app.db.models.audit import AuditRule
from app.db.models.spatial import (
    BusStop,
    SubwayStation,
    StreetTrashBin,
    Park,
    CigaretteLitterHotspot,
    FireWaterFacility,
    SmokingArea,
)
from app.db.models.stats import (
    TransitPassenger,
    PopulationStat,
    CivilComplaint,
    AgeDemographics,
)
from app.db.models.rag_feedback import RagFeedbackLog

__all__ = [
    "Base",
    "User",
    "Parcel",
    "ConflictSimulation",
    "DebateLog",
    "VerifiedPrecedent",
    "AuditRule",
    "BusStop",
    "SubwayStation",
    "StreetTrashBin",
    "Park",
    "CigaretteLitterHotspot",
    "FireWaterFacility",
    "SmokingArea",
    "TransitPassenger",
    "PopulationStat",
    "CivilComplaint",
    "AgeDemographics",
    "RagFeedbackLog",
]
