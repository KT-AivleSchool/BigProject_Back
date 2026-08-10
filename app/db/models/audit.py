from sqlalchemy import (
    Column,
    Integer,
    String,
    Numeric,
    Text,
    Boolean,
    DateTime,
    Index,
    UniqueConstraint,
    func,
)
from app.db.session import Base


class AuditRule(Base):
    """STEP1 감리 AI 산출물(`<도메인>_audit_result_reviewed.json`)의 role 한 줄 = 이 테이블 한 행.

    🔴 2026-08-09 재정의. 원래 이 테이블은 `dummy_audit.json`(더미)을 담을 용도로
       7컬럼짜리였고, 실제 감리 산출물의 절반을 담지 못했다 —
       `배제반경_m`·`exclusion_type`·`confirmed`·`need_review`·`facility_inference` 가
       전부 갈 곳이 없었다. 컬럼이 없으면 값은 조용히 사라진다(원칙 4).
       그래서 **우리 산출물 구조를 기준으로** 컬럼을 맞췄다.
       매핑 근거는 산출물 자신의 `_schema.role_필드` 설명이다.

    ⚠ `facility_type` 은 **배제 대상 시설**(금연구역·어린이집…)이다.
       입지를 정하려는 **대상 시설**(흡연부스)은 `target_facility` 다.
       원래 코드가 `facility_type` 하나로 둘 다 쓰려다
       "대상 시설 = 금연구역" 으로 읽히고 있었다.
    """

    __tablename__ = "audit_rules"
    __table_args__ = (
        # 같은 도메인·같은 run 을 두 번 적재하면 값이 두 배가 된다.
        # 로더는 domain+run_id 단위로 지우고 다시 넣지만, 제약으로도 막는다.
        UniqueConstraint(
            "domain", "run_id", "dataset_id", "role_index", name="uq_audit_rules_role"
        ),
        Index("idx_audit_rules_domain", "domain"),
        Index("idx_audit_rules_target", "target_facility"),
    )

    id = Column(Integer, primary_key=True, index=True)

    # ── 어느 산출물에서 왔는가 (출처 기록) ──────────────────────────────
    domain = Column(String(50), nullable=False)  # '흡연' · '재활용'
    # 🔴 출처가 아니라 **조회 키**이기도 하다 — 화면5 가 `booth_candidates` 행의
    #    run_id 로 이 표를 좁힌다. 그래서 `booth_candidates.run_id` 와 **어휘가 같아야**
    #    한다: 격리 run 은 `r_YYYYMMDD_NNN`, 정본은 `'정본'`(2026-08-10 통일).
    #    예전엔 STEP 폴더 이름(`step1_output` / `step4_output`)이 들어가 갈려 있었다.
    run_id = Column(String(64), nullable=False)  # 'r_20260810_002' · 정본이면 '정본'
    dataset_id = Column(String(50), nullable=True)  # '01','02'…
    role_index = Column(Integer, nullable=False, default=0)  # results[].roles[i]

    # ── 대상 시설 (facility_inference) ─────────────────────────────────
    target_facility = Column(String(100), nullable=True)  # '흡연부스'
    region = Column(String(100), nullable=True)  # '서울특별시 용산구'

    # ── role 본문 ──────────────────────────────────────────────────────
    role_type = Column(String(50), nullable=False)
    # positive_factor / negative_factor / hard_exclusion / reference_only
    factor_name = Column(String(300), nullable=True)
    # 가중치 인자 표시명. facility_type 이 없으면 dataset summary 로 채운다
    facility_type = Column(String(100), nullable=True)  # 배제 대상 시설명
    weight = Column(Numeric, nullable=True)  # -1~1. hard_exclusion 은 null
    exclusion_type = Column(String(20), nullable=True)  # radius / polygon
    exclusion_radius_m = Column(Numeric, nullable=True)  # 배제반경_m
    rationale = Column(Text, nullable=True)  # 판정 근거
    source = Column(String(250), nullable=True)
    # 산출물의 source **원문**. 조항 문자열이거나 리터럴 'human_confirmed' 다.
    # 산출물에서 이미 두 의미가 섞여 있어(gam2_audit_judgment_test.apply_radius_answer
    # 가 사람 확정 시 조항 자리에 'human_confirmed' 를 덮어쓴다) 여기서
    # 임의로 쪼개지 않는다 — 쪼개면 없는 정보를 지어내는 것이다(원칙 5).
    confirmed = Column(Boolean, nullable=False, default=False)  # 조례 대조로 검증됨
    need_review = Column(Boolean, nullable=False, default=False)  # 사람 확인 필요

    # ── 데이터셋 문맥 ──────────────────────────────────────────────────
    summary = Column(Text, nullable=True)  # 이 데이터셋 한 줄 요약
    coord_status = Column(String(30), nullable=True)  # has_coords / needs_geocoding …

    created_at = Column(DateTime, default=func.current_timestamp())
