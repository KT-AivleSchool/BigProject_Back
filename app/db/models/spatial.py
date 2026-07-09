from sqlalchemy import Column, Integer, String, Float, ForeignKey, Date
from geoalchemy2 import Geometry
from app.db.session import Base


class District(Base):
    """자치구 마스터 테이블"""

    __tablename__ = "districts"

    id = Column(Integer, primary_key=True, index=True)
    district_name = Column(String(100), nullable=False)
    sig_cd = Column(String(5), unique=True, nullable=False, index=True)


class DongBoundary(Base):
    """행정동 경계 면 공간정보 테이블"""

    __tablename__ = "dong_boundaries"

    id = Column(Integer, primary_key=True, index=True)
    district_id = Column(
        Integer, ForeignKey("districts.id", ondelete="CASCADE"), nullable=False
    )
    dong_code = Column(String(10), unique=True, nullable=False, index=True)
    dong_name = Column(String(100), nullable=False)
    geom = Column(Geometry(geometry_type="MULTIPOLYGON", srid=4326), nullable=False)


class RestrictedZone(Base):
    """제한/규제구역 정보 테이블 (4단계 피벗 완료)"""

    __tablename__ = "restricted_zones"

    id = Column(Integer, primary_key=True, index=True)
    district_id = Column(
        Integer, ForeignKey("districts.id", ondelete="CASCADE"), nullable=False
    )
    dong_id = Column(
        Integer, ForeignKey("dong_boundaries.id", ondelete="SET NULL"), nullable=True
    )
    zone_name = Column(String(150), nullable=True)
    address = Column(String(250), nullable=True)
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=False)
    area = Column(Float, nullable=True)
    registered_at = Column(Date, nullable=True)
    zone_type = Column(String(50), nullable=False, default="금연구역")


class ChildcareCenter(Base):
    """어린이집/학교 정보 테이블"""

    __tablename__ = "childcare_centers"

    id = Column(Integer, primary_key=True, index=True)
    district_id = Column(
        Integer, ForeignKey("districts.id", ondelete="CASCADE"), nullable=False
    )
    dong_id = Column(
        Integer, ForeignKey("dong_boundaries.id", ondelete="SET NULL"), nullable=True
    )
    center_name = Column(String(150), nullable=False)
    center_type = Column(String(50), nullable=True)
    address = Column(String(250), nullable=True)
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=False)
    student_count = Column(Integer, nullable=True)


class TransitStation(Base):
    """버스/지하철 역사 마스터 테이블"""

    __tablename__ = "transit_stations"

    id = Column(Integer, primary_key=True, index=True)
    district_id = Column(
        Integer, ForeignKey("districts.id", ondelete="CASCADE"), nullable=False
    )
    dong_id = Column(
        Integer, ForeignKey("dong_boundaries.id", ondelete="SET NULL"), nullable=True
    )
    station_no = Column(String(50), unique=True, nullable=False, index=True)
    station_name = Column(String(150), nullable=False)
    transit_type = Column(String(10), nullable=False)
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=False)


class CommercialShop(Base):
    """소상공인 상가상권 정보 테이블"""

    __tablename__ = "commercial_shops"

    id = Column(Integer, primary_key=True, index=True)
    district_id = Column(
        Integer, ForeignKey("districts.id", ondelete="CASCADE"), nullable=False
    )
    dong_id = Column(
        Integer, ForeignKey("dong_boundaries.id", ondelete="SET NULL"), nullable=True
    )
    shop_name = Column(String(150), nullable=False)
    category_code = Column(String(10), nullable=True)
    category_name = Column(String(50), nullable=True)
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=False)


class CadastralLand(Base):
    """국토교통부 연속지적도 테이블"""

    __tablename__ = "cadastral_lands"

    id = Column(Integer, primary_key=True, index=True)
    district_id = Column(
        Integer, ForeignKey("districts.id", ondelete="CASCADE"), nullable=False
    )
    dong_id = Column(
        Integer, ForeignKey("dong_boundaries.id", ondelete="SET NULL"), nullable=True
    )
    pnu = Column(String(19), nullable=False, index=True)
    jibun = Column(String(100), nullable=True)
    land_use_code = Column(String(5), nullable=True)
    ownership_type = Column(String(10), nullable=True)
    geom = Column(Geometry(geometry_type="MULTIPOLYGON", srid=4326), nullable=False)


class TrashBin(Base):
    """가로쓰레기통 및 담배꽁초 수거함 테이블"""

    __tablename__ = "trash_bins"

    id = Column(Integer, primary_key=True, index=True)
    district_id = Column(
        Integer, ForeignKey("districts.id", ondelete="CASCADE"), nullable=False
    )
    dong_id = Column(
        Integer, ForeignKey("dong_boundaries.id", ondelete="SET NULL"), nullable=True
    )
    bin_name = Column(String(150), nullable=True)
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=False)
    bin_type = Column(String(50), nullable=True)


class IllegalDumpingZone(Base):
    """상습무단투기구역 테이블 (4단계 피벗 완료)"""

    __tablename__ = "illegal_dumping_zones"

    id = Column(Integer, primary_key=True, index=True)
    district_id = Column(
        Integer, ForeignKey("districts.id", ondelete="CASCADE"), nullable=False
    )
    dong_id = Column(
        Integer, ForeignKey("dong_boundaries.id", ondelete="SET NULL"), nullable=True
    )
    address = Column(String(250), nullable=True)
    detail_location = Column(String, nullable=True)
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=False)
