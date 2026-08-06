from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON
from geoalchemy2 import Geometry
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.session import Base


class Parcel(Base):
    __tablename__ = "booth_candidates"

    id = Column(Integer, primary_key=True, index=True)
    score = Column(Float, nullable=True)
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=True)

    # Existing relationships can be kept or modified if needed
    simulations = relationship(
        "ConflictSimulation", back_populates="parcel", cascade="all, delete-orphan"
    )


class ConflictSimulation(Base):
    __tablename__ = "conflict_simulations"

    id = Column(Integer, primary_key=True, index=True)
    parcel_id = Column(
        Integer,
        ForeignKey("booth_candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    facility_type = Column(String(100), nullable=False)
    result_json = Column(JSON, nullable=False)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    parcel = relationship("Parcel", back_populates="simulations")
