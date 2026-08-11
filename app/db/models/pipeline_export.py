from datetime import datetime
from geoalchemy2 import Geometry
from sqlalchemy import Column, Float, Integer, String, BigInteger, Text, DateTime, JSON
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class PipelineAuditReview(Base):
    """STEP 1: 사람이 최종 검토 및 확정한 감리 결과 정본"""
    __tablename__ = "pipeline_audit_reviews"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(100), nullable=False, index=True)
    domain = Column(String(50), nullable=False)
    reviewed_data = Column(JSON, nullable=False)
    reviewed_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CleanSpatialLayer(Base):
    """STEP 2: 지오코딩 및 위·경도 정제가 완료된 점/선/면 공간 데이터 (gpkg)"""
    __tablename__ = "clean_spatial_layers"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(100), nullable=False, index=True)
    dataset_id = Column(String(20), nullable=False, index=True)
    layer_name = Column(String(100), nullable=False)
    geom = Column(Geometry("GEOMETRY", srid=4326), nullable=False)
    properties = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class CleanStatTable(Base):
    """STEP 2: 좌표가 없는 순수 행정동별 통계/집계표 정제 데이터 (parquet)"""
    __tablename__ = "clean_stat_tables"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(100), nullable=False, index=True)
    dataset_id = Column(String(20), nullable=False, index=True)
    adm_dong_cd = Column(String(20), nullable=True)
    stat_data = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class PipelineCleanReport(Base):
    """STEP 2: 데이터 정제 요약 종합 리포트"""
    __tablename__ = "pipeline_clean_reports"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(100), nullable=False, unique=True)
    domain = Column(String(50), nullable=False)
    total_datasets = Column(Integer, nullable=False, default=0)
    report_data = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class PipelineWeightSet(Base):
    """STEP 3: AHP 및 가중치 모델에 의해 확정된 지표 가중치 세트"""
    __tablename__ = "pipeline_weight_sets"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(100), nullable=False, unique=True)
    domain = Column(String(50), nullable=False)
    alpha = Column(Float, nullable=True)
    decay = Column(JSON, nullable=True)
    weight_data = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)



class CandidateParcel(Base):
    """STEP 3: 지적도 경계 필지 연산을 통해 추출된 1차 후보지 필지"""
    __tablename__ = "candidate_parcels"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(100), nullable=False, index=True)
    pnu = Column(String(20), nullable=False, index=True)
    geom = Column(Geometry("GEOMETRY", srid=5186), nullable=False)
    attributes = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class SelectedTopnSite(Base):
    """STEP 4: 최적 추천 입지 상위 N개 필지 평가점수 및 경계 데이터"""
    __tablename__ = "selected_topn_sites"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(100), nullable=False, index=True)
    rank = Column(Integer, nullable=False)
    pnu = Column(String(20), nullable=False)
    total_score = Column(Float, nullable=False)
    geom = Column(Geometry("GEOMETRY", srid=5186), nullable=False)
    indicator_scores = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)



class PipelineFinalReport(Base):
    """STEP 4: 최적 입지 선정 결과 최종 심의 요약 보고서"""
    __tablename__ = "pipeline_final_reports"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(100), nullable=False, unique=True)
    domain = Column(String(50), nullable=False)
    facility = Column(String(50), nullable=False)
    top_candidate_pnu = Column(String(20), nullable=True)
    report_data = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
