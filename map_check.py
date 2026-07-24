# ruff: noqa: F403, F405
from app.db.models.spatial import *
from app.db.models.stats import *
from app.db.models.simulation import *


def p(cls):
    print(cls.__tablename__)
    for c in cls.__table__.columns:
        print(f"  {c.name} ({c.type})")


p(TransitStation)
p(TransitPassenger)
p(TrashBin)
p(CadastralLand)
p(IllegalDumpingZone)
p(Park)
p(CommercialShop)
p(LivingPopulationStat)
p(Parcel)
p(RestrictedZone)
p(SmokingArea)
