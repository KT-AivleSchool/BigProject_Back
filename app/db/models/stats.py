from sqlalchemy import Column, Integer, String, Float, ForeignKey
from app.db.session import Base


class TransitPassenger(Base):
    """대중교통 역사별 승하차 인원 통계 테이블"""

    __tablename__ = "transit_passengers"

    id = Column(Integer, primary_key=True, index=True)
    station_id = Column(
        Integer, ForeignKey("transit_stations.id", ondelete="CASCADE"), nullable=False
    )
    analysis_ym = Column(String(6), nullable=False)
    boarding_count = Column(Integer, default=0)
    alighting_count = Column(Integer, default=0)
    total_volume = Column(Integer, default=0)


class PopulationStat(Base):
    """행정동 단위 생활인구 통계 요약 테이블"""

    __tablename__ = "population_stats"

    id = Column(Integer, primary_key=True, index=True)
    dong_id = Column(
        Integer, ForeignKey("dong_boundaries.id", ondelete="CASCADE"), nullable=False
    )
    day_type = Column(String(10), nullable=False)
    time_type = Column(String(10), nullable=False)
    avg_population = Column(Float, nullable=False)


class CivilComplaint(Base):
    """행정동 단위 민원 건수 통계 테이블"""

    __tablename__ = "civil_complaints"

    id = Column(Integer, primary_key=True, index=True)
    dong_id = Column(
        Integer, ForeignKey("dong_boundaries.id", ondelete="CASCADE"), nullable=False
    )
    complaint_count = Column(Integer, nullable=False)
    analysis_year = Column(String(4), nullable=False)


class AgeDemographics(Base):
    """행정동 단위 주민등록인구 연령 통계 테이블"""

    __tablename__ = "age_demographics"

    id = Column(Integer, primary_key=True, index=True)
    dong_id = Column(
        Integer, ForeignKey("dong_boundaries.id", ondelete="CASCADE"), nullable=False
    )
    youth_population = Column(Integer, nullable=False)
    total_population = Column(Integer, nullable=False)
    youth_ratio = Column(Float, nullable=False)
