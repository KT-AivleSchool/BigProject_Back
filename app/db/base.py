# Alembic 마이그레이션 도구가 모든 ORM 모델을 한 번에 가져올 수 있도록 모으는 파일
# DDL 테이블 추가 시 아래에 Import를 추가해야 함

from app.db.session import Base
from app.db.models.user import User
from app.db.models.simulation import Parcel, ConflictSimulation, DebateLog
from app.db.models.precedent import VerifiedPrecedent
from app.db.models.audit import AuditRule

# 🔴 `app/db/models/spatial.py`(BusStop·SubwayStation·StreetTrashBin·Park·SmokingArea)는
#    2026-08-11 에 지웠다. 그 5개 테이블은 도메인(흡연) 데이터셋이고, 프리셋 원본은
#    이제 DB 가 아니라 **디스크**에 둔다(이슈 #215 계층 구분 — 1계층 전국 경계·크로스워크 /
#    2계층 지역단위 지적·공유지 / 산출물). 마지막 소비자였던 `gis_service.get_poi_context_from_db`
#    는 파일을 읽는 `app/services/poi_context.py` 로 교체됐고 모듈 자체도 삭제됐다.
#    🔴 ORM 선언을 남기면 `create_missing_tables.py --yes` 가 없는 테이블을 다시 만든다 —
#    DB 에서 지웠으면 선언도 같이 지운다(FK 는 DB 가 아니라 `Base.metadata` 에서 풀린다).
from app.db.models.rag_feedback import RagFeedbackLog
from app.db.models.run_record import RunRecord

__all__ = [
    "Base",
    "User",
    "RunRecord",
    "Parcel",
    "ConflictSimulation",
    "DebateLog",
    "VerifiedPrecedent",
    "AuditRule",
    "RagFeedbackLog",
]
