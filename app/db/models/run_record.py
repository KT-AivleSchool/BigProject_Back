from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from app.db.session import Base


class RunRecord(Base):
    """파이프라인 실행 1회 = 이 테이블 한 행. **run 메타데이터**(4계층 중 ③).

    🔴 산출물이 아니다. 산출물의 자리 기준(계약 §8-5-1 — ① 파이프라인 미참조
       ② 행 질의 ③ FK 대상)은 「무엇을 만들었나」에 대한 기준이고, 이 표는
       「누가 언제 무엇을 돌렸나」다. 별개 축이라 그 기준으로 재지 않는다.
       산출물 행(`audit_rules`·`booth_candidates`)은 단계가 **성공해야만** 생겨서
       실패·대기 run 은 DB 에 아예 안 보인다 — 마이페이지가 그걸로는 못 산다.

    🔴 **진행 상태의 정본은 `status.json` 이고 여기는 사본이다.** 그래서 컬럼명이
       `status` 가 아니라 `last_known_status` 다. 값은 셋뿐이다 —
       `queued`(발급 시) · `succeeded` · `failed`. `running`·`awaiting_hitl` 은
       **일부러 안 넣는다**: 진행률을 DB 에 물으면 정본이 둘이 되고, 그 둘은
       언젠가 갈린다. 지금 어디까지 갔는지는 `GET /pipeline/runs/{id}` 로 본다.

    ⚠ 행은 **발급 시점에** 만든다(`last_known_status='queued'`). 「DB 는 끝난 사실만」을
      문자대로 읽으면 **돌다 죽은 run 은 행이 아예 안 생겨** `reap_orphans` 가 갱신할
      대상이 없고 실패 run 이 이력에서 사라진다(원칙 4). 「끝난 사실만」은
      *행을 언제 만드나*가 아니라 *무엇을 DB 에 묻지 않나*로 읽는다.

    ⚠ `user_id` 는 **영구 nullable** 이다. 「아직 로그인 배선 전」이라서가 아니라
      **익명 실행이 정상 상태**이기 때문이다(2026-08-11 사람 결정). NOT NULL 로
      바꾸면 로그인 없이 도는 경로가 통째로 죽는다.

    🔴 **여기 행이 있다고 `runs/<run_id>/` 폴더가 지켜지지 않는다.** 정리기
       (`run_pruner`)의 보호는 `keep` 개수와 진행 중 여부 **둘뿐이고 DB 를 안 본다** —
       「목록에는 있는데 산출물은 못 여는 run」은 고장이 아니라 정상 동작의 결과다.
       마이페이지는 그 갈래(`RUN_FOLDER_GONE`·`ARTIFACT_PRUNED`)를 반드시 갖는다.

    설계 정본: `01_설계결정\\산출물_저장구조_4계층_확정.md`
    """

    __tablename__ = "run_records"
    __table_args__ = (
        # 마이페이지 = "내 실행을 최신순으로". 익명 행(user_id IS NULL)도 같은
        # 인덱스를 탄다 — Postgres 는 NULL 도 인덱싱한다.
        Index("ix_run_records_user", "user_id", "started_at"),
    )

    # 러너가 발급한 값 그대로다(`r_YYYYMMDD_NNN`). 대리키를 두지 않는다 —
    # 두면 "같은 run_id 의 행이 둘" 이 가능해지고, 그때 어느 쪽이 그 run 인지
    # 정할 방법이 없다. run_id 는 `runs/run_seq.json` 최고수위 원장이 유일성을 준다.
    run_id = Column(String(64), primary_key=True)

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    domain = Column(String(50), nullable=False)
    mode = Column(String(16), nullable=False)          # fixture · hitl · full
    last_known_status = Column(String(16), nullable=False)
    user_input = Column(Text, nullable=True)           # full 모드의 안건 문장

    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    # 이 run 이 DB 에 넣은 행 수. `status.json` 의 `loaded` 사본이다.
    # 🔴 `loaded.cascaded` 는 **안 옮긴다** — 그건 「넣은 수」가 아니라 재적재로
    #    **지워진 수**다(뜻이 정반대). 같은 지붕 아래 두 방향을 담으면 읽는 쪽이
    #    반드시 한 번은 틀린다(2026-08-11 실제로 그렇게 읽혔다).
    loaded_audit_rules = Column(Integer, nullable=True)
    loaded_booth_candidates = Column(Integer, nullable=True)
