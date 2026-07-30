from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.session import Base


class Parcel(Base):
    __tablename__ = "parcels"

    id = Column(Integer, primary_key=True, index=True)
    jibun = Column(String(255), nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    intensity_level = Column(String(50), nullable=False, default="normal")
    ahp_weights = Column(JSON, nullable=True)

    simulations = relationship(
        "ConflictSimulation", back_populates="parcel", cascade="all, delete-orphan"
    )


class ConflictSimulation(Base):
    __tablename__ = "conflict_simulations"

    id = Column(Integer, primary_key=True, index=True)
    parcel_id = Column(Integer, ForeignKey("parcels.id"), nullable=False, index=True)
    facility_type = Column(String(100), nullable=False)
    result_json = Column(JSON, nullable=False)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    parcel = relationship("Parcel", back_populates="simulations")
