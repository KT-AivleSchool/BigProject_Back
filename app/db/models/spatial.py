from sqlalchemy import Column, Integer, String, Float, Numeric
from geoalchemy2 import Geometry
from app.db.session import Base


class BusStop(Base):
    __tablename__ = "bus_stop_passenger_stats"

    id = Column(Integer, primary_key=True, index=True)
    stop_name = Column(String(150), nullable=False)
    longitude = Column(Float, nullable=False)
    latitude = Column(Float, nullable=False)
    avg_floating_population = Column(Numeric, nullable=True)
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=True)


class SubwayStation(Base):
    __tablename__ = "subway_station_passenger_stats"

    id = Column(Integer, primary_key=True, index=True)
    station_name = Column(String(150), nullable=False)
    total_passengers = Column(Integer, nullable=True)
    # Note: subway station seems to not have geom in schema dump but typically it might.
    # We will use it if needed, else we can skip.
    # Ah, in the schema dump for subway_station_passenger_stats there was no longitude, latitude, geom.
    # Let me add a dummy geom or we might just not use it if it lacks geom.


class StreetTrashBin(Base):
    __tablename__ = "street_trash_bins"

    id = Column(Integer, primary_key=True, index=True)
    installation_address = Column(String(300), nullable=False)
    longitude = Column(Float, nullable=False)
    latitude = Column(Float, nullable=False)
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=True)


class Park(Base):
    __tablename__ = "parks"

    id = Column(Integer, primary_key=True, index=True)
    facility_name = Column(String(200), nullable=False)
    longitude = Column(Float, nullable=False)
    latitude = Column(Float, nullable=False)
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=True)


class SmokingArea(Base):
    __tablename__ = "smoking_areas"

    id = Column(Integer, primary_key=True, index=True)
    installation_location = Column(String(300), nullable=False)
    longitude = Column(Float, nullable=False)
    latitude = Column(Float, nullable=False)
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=True)
