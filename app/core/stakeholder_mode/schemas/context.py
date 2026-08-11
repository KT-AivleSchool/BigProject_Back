from typing import Optional
from pydantic import BaseModel


class SpatialFact(BaseModel):
    fact_id: str
    category: str
    name: str

    candidate_id: str

    straight_distance_m: Optional[float] = None
    network_distance_m: Optional[float] = None
    count_within_radius: Optional[int] = None

    tags: list[str] = []
    source_id: str
    observed_at: Optional[str] = None


class CandidateContext(BaseModel):
    candidate_id: str
    name: str

    spatial_facts: list[SpatialFact]
    restrictions: list[dict]
    transportation: list[dict]
    operational_facts: list[dict]
