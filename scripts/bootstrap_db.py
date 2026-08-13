# -*- coding: utf-8 -*-
"""빈 DB 를 쓸 수 있는 상태로 만든다 — **스키마만**. (계획만 출력이 기본)

    python scripts/bootstrap_db.py            # 무엇을 할지만 출력 (dry-run)
    python scripts/bootstrap_db.py --yes      # 실제 적용
    python scripts/bootstrap_db.py --yes --force   # 아래 🔴 파괴적 단계까지 허용

왜 있나
  `docker compose up -d` 다음에 `schema.sql` **하나만** 치면 DB 가 안 만들어진다.
  그 파일은 2026-08-11 에 19개 선언 중 17개를 들어냈고(정본이 딴 데다), 지금
  남은 건 확장팩 + `districts` + `dong_boundaries` 뿐이다. 빈 DB 를 채우려면
  `.sql` 여섯 개와 ORM 생성기를 **순서대로** 쳐야 하는데, 그 순서는 README 표에만
  글로 있었다 — 글로만 있으면 빠뜨려도 아무 데도 안 남는다.

  실제로 빠졌다: `run_records` 는 ORM 정본이라 어떤 `.sql` 에도 없다. 코드를 받고
  `.sql` 만 친 사람의 DB 에는 그 테이블이 없고, `audit_rules`·`booth_candidates`
  까지 없으면 **세 모드(fixture·hitl·full) 전부 꼬리의 적재 두 칸에서 죽는다.**

🔴 이 스크립트는 DDL 을 **한 줄도 새로 쓰지 않는다.**
   기존 `.sql` 파일과 `create_missing_tables.py` 를 순서대로 부르기만 한다.
   여기에 `CREATE TABLE` 을 적는 순간 같은 스키마가 두 곳에 있게 되고, 그건
   이 저장소가 2026-08-11 에 `schema.sql` 에서 들어낸 바로 그 함정이다.

🔴 **스키마만 만든다. 데이터는 안 넣는다.**
   경계 3종·지적도 적재(`scripts/load_region_boundaries.py`·`load_cadastral.py`)는
   원본 SHP 가 `.gitignore` 라 clone 에 없고, 지역을 늘릴 때만 하는 별개 작업이다.
   여기서 부르면 「원본이 없다」가 부트스트랩 실패로 보인다. 안내만 하고 안 부른다.

🔴 **step5 계열은 ORM 생성기 뒤에 온다** (README 표도 2026-08-12 에 같이 고쳤다).
   `schema_step5.sql`·`schema_step5_b.sql` 은 `hearing_result_a`·`verified_precedents`
   를 **ALTER** 하는데 그 둘은 어느 `.sql` 에도 `CREATE TABLE` 이 없다(ORM 정본).
   옛 표 순서(step5 → ORM)대로 빈 DB 에 치면 `relation … does not exist` 로 죽는다.
   표가 틀렸다기보다 **한 번도 빈 DB 에서 끝까지 쳐본 적이 없었다**는 뜻이다.

검증 (2026-08-12, 일회용 DB `omnisite_bootstrap_check` 를 만들어 완주 후 삭제)
   빈 DB → **9칸 전부 통과 · 21테이블**. 실 DB 와 대조하면 컬럼까지 같고
   차이는 셋뿐이다: `langchain_pg_*` 2개(PGVector 가 런타임에 만든다) ·
   `candidate_lands.dong_id`(실 DB 에만 있는 옛 잔재 — 코드 참조 0회).
   ⚠ `booth_candidates` 는 5번(SQL)이 만들고 7번(ORM)이 「이미 있다」며 건너뛴다.
      「같은 이름의 다른 스키마」가 날 자리라 컬럼 집합을 실제로 댔다 —
      ORM 선언은 DB 의 **부분집합**이고 ORM 에만 있는 컬럼은 0개다(=쿼리는 다 돈다).

종료코드 (🔴 CI 가 `set -e` 로 읽는다 — 뜻을 바꾸면 배포가 같이 바뀐다)
  0  할 일을 다 했거나, 건너뛴 단계가 **이미 전부 있어 할 일이 없던 것**뿐이다
  1  건너뛴 단계에 **아직 없는 것이 남아 있다**(스키마가 반쪽) 또는 실제 실패
  2  DB 에 못 붙었다

  🔴 「건너뛴다」에는 뜻이 둘이라 한 덩어리로 세면 안 된다.
     ⓐ 이미 다 있어서 안 해도 된다   → 스키마는 완성이다. 배포를 막을 이유가 없다
     ⓑ 아직 없는데 DROP 때문에 못 한다 → 반쪽 스키마다. 반드시 멈춰야 한다
     2026-08-13 에 실제로 났다: 앞선 배포가 `national_properties` 를 만들어놓자
     그 존재가 5번을 영구히 막았고, ⓐ 하나 때문에 rc=1 이 나가 **그 뒤 모든 푸시가
     빨간불**이 됐다. 그렇다고 전부 rc=0 으로 접으면 이번엔 ⓑ 가 조용히 통과한다.
  ⚠ ⓐ 판정은 `expect_tables`·`expect_cols` 로만 한다 — **선언한 만큼만 본다.**
     `.sql` 에 `ADD COLUMN` 을 더하면 `expect_cols` 에도 더할 것. 안 더하면
     테이블은 있으니 「이미 있음」이 되고, 없는 컬럼이 **초록불 아래** 남는다.

접속
  `settings.DATABASE_URL`(= `.env`) 을 쓴다. `docker exec … psql` 이 아니다 —
  그 형태는 `-i` 를 빠뜨리면 **stdin 이 무시되고 출력도 없이 exit 0** 이 되는
  조용한 실패가 있고, 컨테이너 이름이 바뀌면 같이 깨진다. 접속지의 정본은 `.env` 다.
"""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if hasattr(sys.stdout, "buffer"):
    # line_buffering=True — 재래핑하면서 이걸 빼면 `python -u` 가 무력화된다.
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", line_buffering=True
    )

import psycopg  # noqa: E402

from app.config import DB_CONNECT_TIMEOUT, settings  # noqa: E402


@dataclass
class Step:
    no: str
    what: str
    kind: str  # "sql" | "py"
    target: str  # 저장소 루트 기준 상대경로
    # 이 단계가 끝나면 **있어야 하는** 것. rc=0 은 "명령이 돌았다"만 뜻한다.
    expect_tables: tuple[str, ...] = ()
    expect_cols: tuple[tuple[str, str], ...] = ()  # (table, column)
    # 이 테이블이 이미 있으면 --force 없이 멈춘다(파일 안에 DROP 이 있다).
    drops: tuple[str, ...] = ()
    note: str = ""
    args: list[str] = field(default_factory=list)


# 🔴 순서가 정책이다. 바꾸기 전에 위 docstring 의 「5 와 6 이 바뀐다」를 읽을 것.
STEPS: tuple[Step, ...] = (
    Step(
        "1",
        "확장팩 + 자치구역·행정동 경계(앱 전용)",
        "sql",
        "schema.sql",
        expect_tables=("districts", "dong_boundaries"),
        note="dong_boundaries 는 ORM 이 FK 로 가리키는데 선언은 없는 테이블이다 — "
        "여기서 안 만들면 6단계가 NoReferencedTableError 로 죽는다",
    ),
    Step(
        "2",
        "1계층 경계 3종 + 크로스워크 (빈 껍데기)",
        "sql",
        "schema_region_boundaries.sql",
        expect_tables=(
            "sido_boundaries",
            "sigungu_boundaries",
            "adm_dong_boundaries",
            "admin_crosswalk",
        ),
        note="행 적재는 scripts/load_region_boundaries.py --commit (원본 SHP 필요)",
    ),
    Step(
        "3",
        "연속지적도 (빈 껍데기)",
        "sql",
        "schema_cadastral.sql",
        expect_tables=("cadastral_lands",),
        note="행 적재는 scripts/load_cadastral.py --commit (원본 SHP 필요)",
    ),
    Step(
        "4",
        "2계층 후보 필지",
        "sql",
        "schema_cleaned_data.sql",
        expect_tables=("candidate_lands",),
    ),
    Step(
        "5",
        "국유부동산 + 후보점 테이블",
        "sql",
        "schema_cleaned_data_add.sql",
        expect_tables=("national_properties", "booth_candidates"),
        # 🔴 테이블 2개만 선언해두면 「이미 있음」이 거짓말이 된다. 이 파일은 테이블을
        #    만드는 데서 안 끝나고 ALTER 로 컬럼 18개를 가산한다 — 테이블만 보고
        #    「이미 있음」이라 말하면, 나중에 이 파일에 ADD COLUMN 이 하나 붙었을 때
        #    테이블은 있으니 계속 건너뛰면서 **초록불**이 뜬다. 안 터지고 컬럼만 없다.
        #    아래 목록은 이 파일의 `ADD COLUMN` 전수다(2026-08-13 실측 대조).
        #    ⚠ 이 파일에 컬럼을 더하면 **여기도 같이 더한다.** 안 더하면 그 컬럼은
        #      「있어야 하는 것」에서 빠져 영원히 확인되지 않는다.
        expect_cols=(
            ("national_properties", "geom_5186"),
            ("national_properties", "sigungu_cd"),
            ("candidate_lands", "geom_5186"),
            ("candidate_lands", "area_m2"),
            ("candidate_lands", "width_m"),
            ("candidate_lands", "sigungu_cd"),
            ("booth_candidates", "land_id"),
            ("booth_candidates", "area_m2"),
            ("booth_candidates", "width_m"),
            ("booth_candidates", "is_national"),
            ("booth_candidates", "shops_150m"),
            ("booth_candidates", "dist_transit"),
            ("booth_candidates", "dist_litter"),
            ("booth_candidates", "dist_existing"),
            ("booth_candidates", "score"),
            ("booth_candidates", "rank"),
            ("booth_candidates", "geom"),
            ("booth_candidates", "geom_5186"),
        ),
        drops=("national_properties",),
        note="🔴 이 파일 20행에 `DROP TABLE IF EXISTS national_properties CASCADE;` 가 있다",
    ),
    Step(
        "6",
        "STEP4 Top-N 적재용 컬럼 가산",
        "sql",
        "schema_step4_topn.sql",
        expect_cols=(("booth_candidates", "domain"), ("booth_candidates", "run_id")),
    ),
    Step(
        "7",
        "ORM 정본 9종 (users·audit_rules·run_records·hearing_result_a…)",
        "py",
        "scripts/create_missing_tables.py",
        args=["--yes"],
        expect_tables=(
            "audit_rules",
            "booth_candidates",
            "debate_logs",
            "hearing_result_a",
            "hearing_result_b",
            "rag_feedback_log",
            "run_records",
            "users",
            "verified_precedents",
        ),
        note="🔴 여기가 README 표의 6단계다. 아래 8·9 보다 **먼저** 와야 한다",
    ),
    Step(
        "8",
        "STEP5 A 엔진 정합 (hearing_result_a ALTER + debate_logs)",
        "sql",
        "schema_step5.sql",
        expect_tables=("debate_logs",),
        expect_cols=(
            ("hearing_result_a", "parcel_id"),
            ("verified_precedents", "document_no"),
        ),
    ),
    Step(
        "9",
        "STEP5 B 엔진 결과 테이블",
        "sql",
        "schema_step5_b.sql",
        expect_tables=("hearing_result_b",),
    ),
)


def _connect() -> psycopg.Connection:
    return psycopg.connect(settings.DATABASE_URL, connect_timeout=DB_CONNECT_TIMEOUT)


def _tables(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        )
        return {r[0] for r in cur.fetchall()}


def _columns(conn: psycopg.Connection, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        )
        return {r[0] for r in cur.fetchall()}


def _rowcount(conn: psycopg.Connection, table: str) -> int:
    with conn.cursor() as cur:
        # 테이블명은 STEPS 의 리터럴에서만 온다(사용자 입력 아님).
        cur.execute(f'SELECT count(*) FROM "{table}"')
        return int(cur.fetchone()[0])


def _dependents(conn: psycopg.Connection, table: str) -> list[str]:
    """`table` 을 FK 로 가리키는 테이블 이름. CASCADE 로 딸려 나갈 후보다."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT c.conrelid::regclass::text
              FROM pg_constraint c
             WHERE c.contype = 'f'
               AND c.confrelid = to_regclass(%s)
            """,
            (table,),
        )
        return sorted(r[0] for r in cur.fetchall())


def _run_sql(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    # 파일이 자기 트랜잭션을 갖고 있으면 그대로 둔다. 없으면 통째로 감싼다 —
    # 중간에 터졌을 때 절반만 적용된 스키마가 남으면 다음 사람이 원인을 못 찾는다.
    has_own_tx = any(
        ln.strip().upper().startswith("BEGIN;") for ln in text.splitlines()
    )
    body = text if has_own_tx else f"BEGIN;\n{text}\nCOMMIT;"
    with _connect() as conn:
        conn.autocommit = True  # 트랜잭션 경계는 파일(또는 위 래핑)이 정한다
        with conn.cursor() as cur:
            cur.execute(body)  # params 없음 → simple protocol, 다중 구문 허용


def _run_py(target: str, args: list[str]) -> None:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"  # cp949 콘솔에서 이모지 출력이 터진다
    cmd = [sys.executable, str(_ROOT / target), *args]
    proc = subprocess.run(cmd, cwd=str(_ROOT), env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"{target} 이 rc={proc.returncode} 로 끝났다")


def _verify(step: Step) -> list[str]:
    """단계가 실제로 만든 것을 확인. 어긋난 항목을 문자열로 돌려준다."""
    bad: list[str] = []
    with _connect() as conn:
        have = _tables(conn)
        for t in step.expect_tables:
            if t not in have:
                bad.append(f"테이블 없음: {t}")
        for t, c in step.expect_cols:
            if t not in have:
                bad.append(f"테이블 없음: {t} (컬럼 {c} 확인 불가)")
            elif c not in _columns(conn, t):
                bad.append(f"컬럼 없음: {t}.{c}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(
        description="빈 DB 에 스키마를 순서대로 만든다 (계획만 출력이 기본)"
    )
    ap.add_argument("--yes", action="store_true", help="실제로 적용한다")
    ap.add_argument(
        "--force",
        action="store_true",
        help="파일 안에 DROP 이 있는 단계도 실행한다 (기존 행이 사라진다)",
    )
    a = ap.parse_args()

    print("=" * 88)
    print("[bootstrap_db] 빈 DB → 쓸 수 있는 스키마")
    print(f"  접속 : {settings.DATABASE_URL.split('@')[-1]}")
    print(f"  모드 : {'적용(--yes)' if a.yes else '계획만 (dry-run)'}")
    print("=" * 88)

    missing_files = [s.target for s in STEPS if not (_ROOT / s.target).exists()]
    if missing_files:
        # 조용히 건너뛰지 않는다 — 없는 파일을 건너뛰면 부분 스키마가 완성으로 보인다.
        print(f"🔴 파일이 없다: {missing_files}")
        return 2

    try:
        with _connect() as conn:
            before = _tables(conn)
    except Exception as ex:  # noqa: BLE001
        print(f"🔴 DB 에 못 붙었다: {type(ex).__name__}: {ex}")
        print("   · docker compose up -d 로 컨테이너가 떠 있는지")
        print("   · .env 의 DATABASE_URL 이 맞는지 (.env.example 참조)")
        return 2
    print(f"현재 테이블 {len(before)}개\n")

    # ── 1. 계획 ──────────────────────────────────────────────────────────
    blocked: list[Step] = []
    # 🔴 「건너뛴다」에는 뜻이 둘이다. 이 둘을 한 덩어리로 세면 **아무것도 안 해도
    #    되는 상태**가 **해야 하는데 못 한 상태**와 같은 종료코드를 받는다.
    #    ⓐ 이미 다 있어서 안 해도 된다  → 스키마는 완성이다. 배포를 막을 이유가 없다
    #    ⓑ 아직 없는데 DROP 때문에 못 한다 → 반쪽 스키마다. 반드시 멈춰야 한다
    #    갈라놓지 않으면 ⓐ 하나 때문에 rc=1 이 나가고, `set -e` 인 배포가 매번
    #    빨간불이 된다(2026-08-13 실제 사고). 그렇다고 전부 rc=0 으로 접으면
    #    이번엔 ⓑ 가 조용히 통과한다 — 그게 더 나쁘다(원칙 1).
    satisfied: set[str] = set()
    for s in STEPS:
        # 「이미 있음」은 그 단계가 **확인하겠다고 선언한 것 전부**를 봐야 한다.
        # 테이블만 보면 컬럼만 가산하는 단계(6번)가 영원히 「만든다」로 찍힌다 —
        # 멱등이라 실제로는 아무 일도 안 일어나는데 계획이 거짓말을 한다(원칙 4).
        done = bool(s.expect_tables or s.expect_cols) and not _verify(s)
        if done:
            satisfied.add(s.no)
        mark = "이미 있음" if done else "만든다"
        print(f"[{s.no}] {s.what}")
        print(f"     {s.target}  →  {mark}")
        if s.note:
            print(f"     {s.note}")
        for t in s.drops:
            if t in before:
                n = 0
                deps: list[str] = []
                with _connect() as conn:
                    n = _rowcount(conn, t)
                    deps = _dependents(conn, t)
                print(f"     🔴 `{t}` 가 이미 있다 — 이 단계는 그걸 **지우고 다시 만든다**")
                print(f"        지금 {n}행. CASCADE 로 딸려 나갈 후보: {deps or '없음'}")
                if not a.force:
                    if s.no in satisfied:
                        print(
                            "        → --force 없이는 이 단계를 건너뛴다 "
                            "(이 단계가 만들 것이 **이미 전부 있어** 할 일이 없다)"
                        )
                    else:
                        print(
                            "        → --force 없이는 이 단계를 건너뛴다 "
                            "🔴 아직 없는 것이 있는데 못 만든다 — 스키마가 반쪽이 된다"
                        )
                    blocked.append(s)
        print()

    if not a.yes:
        print("-" * 88)
        print("[dry-run] 실제로 만들려면 --yes 를 붙일 것.")
        if blocked:
            print(
                f"          그때도 {[s.no for s in blocked]} 번은 --force 없이는 건너뛴다."
            )
        print("=" * 88)
        return 0

    # ── 2. 적용 ──────────────────────────────────────────────────────────
    skipped: list[str] = []  # 할 일이 없어서 건너뛴 것 (ⓐ)
    unfinished: list[str] = []  # 할 일이 남았는데 못 한 것 (ⓑ)
    for s in STEPS:
        if s in blocked:
            print(f"[{s.no}] 건너뜀 — {s.drops} 를 지우게 되어 있고 --force 가 없다")
            (skipped if s.no in satisfied else unfinished).append(s.no)
            continue
        print(f"[{s.no}] {s.target} …")
        try:
            if s.kind == "sql":
                _run_sql(_ROOT / s.target)
            else:
                _run_py(s.target, s.args)
        except Exception as ex:  # noqa: BLE001
            # 애매하면 raise (원칙 1). 여기서 삼키면 반쪽 스키마가 완성으로 보인다.
            print(f"  🔴 실패: {type(ex).__name__}: {ex}")
            print("=" * 88)
            return 1

        bad = _verify(s)
        if bad:
            print("  🔴 명령은 돌았는데 결과가 없다:")
            for b in bad:
                print(f"     · {b}")
            print("=" * 88)
            return 1
        print("  ✅")

    with _connect() as conn:
        after = _tables(conn)
    created = sorted(after - before)

    print("-" * 88)
    print(f"테이블 {len(before)} → {len(after)}개")
    print(f"새로 생긴 것 {len(created)}개: {created or '없음'}")
    if skipped:
        print(
            f"건너뛴 단계 {skipped} — 만들 것이 이미 전부 있어 할 일이 없었다. "
            "다시 만들려면 --force (기존 행이 사라진다)"
        )
    if unfinished:
        print(
            f"🔴 건너뛴 단계 {unfinished} — **아직 없는 것이 있는데** 못 만들었다. "
            "위 사유를 읽고 --force 를 붙일지 정할 것"
        )
    print()
    print("이 스크립트가 **안 한 것** (필요하면 따로):")
    print("  · 경계 3종 행 적재  python scripts/load_region_boundaries.py --commit")
    print("  · 지적도 행 적재    python scripts/load_cadastral.py [--sigungu <코드>] --commit")
    print("    (둘 다 원본 SHP 가 필요하다 — .gitignore 라 clone 에 안 들어온다)")
    print("=" * 88)
    # 🔴 `skipped`(ⓐ)는 rc 에 안 넣는다 — 스키마가 완성인데 실패로 알리면 배포가
    #    매번 빨간불이고, 그러면 아무도 이 종료코드를 안 본다. `unfinished`(ⓑ)만 남긴다.
    return 1 if unfinished else 0


if __name__ == "__main__":
    raise SystemExit(main())
