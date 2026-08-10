from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Numeric,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from geoalchemy2 import Geometry
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.session import Base


class Parcel(Base):
    """STEP4(MCLP)가 고른 **후보점**. 필지가 아니라 점이다.

    ⚠ 클래스명이 `Parcel` 이라 필지로 읽히지만 테이블은 `booth_candidates` 다.
      필지는 `candidate_lands`(6,524행)이고 여기는 후보점이다 — **id 공간이 다르다.**
      `land_id` 가 그 점이 놓인 필지를 가리킨다.
    """

    __tablename__ = "booth_candidates"

    id = Column(Integer, primary_key=True, index=True)
    # 🔴 `ForeignKey("candidate_lands.id")` 를 쓰지 않는다. SQLAlchemy 는 FK 대상을
    #    **DB 가 아니라 `Base.metadata`** 에서 찾는데 `candidate_lands` 는 ORM 에
    #    선언이 없다. 선언하면 이 매퍼를 쓰는 모든 flush 가 `NoReferencedTableError`
    #    로 죽는다(2026-08-09 실측 — STEP5 저장이 5분 돌고 나서 이걸로 실패했다).
    #    제약 자체는 실 DB 에 걸려 있다(booth_candidates_land_id_fkey).
    land_id = Column(Integer, nullable=True)
    score = Column(Float, nullable=True)
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=True)

    # ── STEP4 Top-N 적재분 (2026-08-10) ─────────────────────────────────
    # 🔴 아래 6개는 `schema_step4_topn.sql` 가산분이다. **적용 안 하면 이 매퍼를 쓰는
    #    모든 SELECT 가 `UndefinedColumnError` 로 죽는다**(화면5 토론 포함).
    #    선언만 빼두면 조용히 안 보이는 대신 Top-N 조회가 불가능해진다 — 터지는 쪽을 택한다.
    #    적용: docker exec -i omnisite-postgres-db psql -U postgres -d omnisite < schema_step4_topn.sql
    domain = Column(String(50), nullable=True)  # STEP4 를 돌린 도메인 (흡연 / 재활용 …)
    run_id = Column(String(64), nullable=True)  # runs/<id> 또는 step4_output(정본)
    facility_type = Column(String(100), nullable=True)  # audit_rules.target_facility 와 같은 어휘
    pnu = Column(String(19), nullable=True)
    jibun = Column(String(100), nullable=True)
    # 🔴 topN.geojson 의 `순위`. **1 이 최상위**다 (점수 내림차순이 아니다).
    rank = Column(Integer, nullable=True)

    simulations = relationship(
        "ConflictSimulation", back_populates="parcel", cascade="all, delete-orphan"
    )


class ConflictSimulation(Base):
    """STEP5 공청회 시뮬레이션 1회 = 1행.

    🔴 2026-08-09 재선언(B안). 이전 선언은 `parcel_id`·`facility_type`·`result_json`
       세 컬럼뿐이었고 **실 DB 에 그 셋이 다 없어서** INSERT 가 100% 실패했다
       (`UndefinedColumnError`). 실 DB 쪽 컬럼(`css_score`·`css_vector`·시나리오 3칸)은
       반대로 ORM 에 없어서 채울 수도 없었다. 이제 **양쪽을 다 선언**한다.

    ⚠ 시나리오 3칸 중 채워지는 건 **매 실행 1칸**이다. 엔진(`reporter_node`)이
      수용도 점수로 A/B/C 중 하나만 확정한다(`app/templates/default/reporter.txt`).
      나머지 둘이 NULL 인 것은 결함이 아니라 사실이다 — 안 나온 걸 지어내지 않는다(원칙 4).
    """

    __tablename__ = "conflict_simulations"

    id = Column(Integer, primary_key=True, index=True)

    # ── 대상 ────────────────────────────────────────────────────────────
    parcel_id = Column(
        Integer,
        ForeignKey("booth_candidates.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # 위 후보점이 놓인 필지. booth_candidates.land_id 에서 유도해 채운다.
    # parcel_id 의 다른 이름이 아니다 — 가리키는 테이블이 다르다.
    # `ForeignKey` 를 안 붙이는 이유는 `Parcel.land_id` 쪽 주석과 같다.
    candidate_land_id = Column(Integer, nullable=True)
    facility_type = Column(String(100), nullable=True)

    # ── 산출물 원본 ─────────────────────────────────────────────────────
    # 아래 개별 컬럼은 전부 여기서 뽑아낸 사본이다. 원본을 통째로 남기는 이유는
    # 컬럼으로 안 쪼갠 값(candidate_lat/lng·intensity_level·timestamp 등)이
    # 있어서다. 쪼갠 것만 남기면 나머지는 조용히 사라진다.
    result_json = Column(JSONB, nullable=True)

    # ── 갈등 민감도 ─────────────────────────────────────────────────────
    css_score = Column(Numeric, nullable=False)  # conflict_sensitivity_score
    css_vector = Column(JSONB, nullable=False)  # ahp_weights (요인별 가중치)

    # ── 시나리오 (매 실행 1칸만 채워진다) ───────────────────────────────
    optimal_scenario = Column(Text, nullable=True)  # A 원만한 타결
    normal_scenario = Column(Text, nullable=True)  # B 조건부 타결
    worst_scenario = Column(Text, nullable=True)  # C 협상 결렬
    # 엔진이 내는 final_acceptance_score 는 「수용도」라 의미가 다르다.
    # 이름이 비슷하다고 넣으면 없는 값을 지어내는 것이다(원칙 5).
    confidence_score = Column(Numeric, nullable=True)

    created_at = Column(DateTime, server_default=func.current_timestamp())

    parcel = relationship("Parcel", back_populates="simulations")
    debate_logs = relationship(
        "DebateLog",
        back_populates="simulation",
        cascade="all, delete-orphan",
        order_by="DebateLog.turn_index",
    )


class DebateLog(Base):
    """공청회 토론 발화 1건 = 1행.

    `ConflictSimulation.result_json["debate_logs"]` 와 같은 내용이지만 행으로도
    남긴다. 통짜 JSON 으로는 발화 단위 조회·인용·보고서 재구성이 안 된다.
    STEP1~4 주요 산출물을 테이블로 둔 것과 같은 방침이다.

    실측 규모: 1회 토론 = 완성 발화 14행. SSE 패킷 1,434건은 토큰 단위 조각이라
    여기 오는 게 아니다.
    """

    __tablename__ = "debate_logs"
    __table_args__ = (
        UniqueConstraint("simulation_id", "turn_index", name="uq_debate_logs_turn"),
    )

    id = Column(Integer, primary_key=True, index=True)
    simulation_id = Column(
        Integer,
        ForeignKey("conflict_simulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    turn_index = Column(Integer, nullable=False)  # 0-base 발화 순서
    sender = Column(String(50), nullable=False)  # 찬성 / 반대 / 정부 / 시스템
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    simulation = relationship("ConflictSimulation", back_populates="debate_logs")
