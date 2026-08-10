"""STEP4 산출물(`<도메인>_topN.geojson`) → `booth_candidates` 테이블.

화면5(공청회)는 `booth_candidates.id` 를 `parcel_id` 로 받아 토론한다. 그런데
`gam4_export.py:109 export_topn()` 은 **파일만** 쓰고 DB 로 가는 배선이 없었다.
그래서 지금까지 그 테이블엔 손으로 넣은 1행뿐이었고, 화면5는 STEP4 결과와
무관한 **항상 같은 점**으로 토론했다. 그 배선을 잇는 게 이 스크립트다.

🔴 몇 개를 넣는가 — **파일에 있는 만큼 전부** 넣는다. 여기에 20 을 박지 않는다.
   개수를 정하는 건 STEP4 의 `--topn`(기본 20)이다. `--spacing` 은 개수가 아니라
   후보점 간격이다(둘은 다른 인자다). 적재기가 N 을 따로 들고 있으면
   `--topn 30` 으로 돌린 날 조용히 20개만 들어간다 — 안 터지고 값만 틀린다.

입력 우선순위(먼저 찾는 것을 쓴다):
  1) `--run <run_id>` → runs/<run_id>/step4/<도메인>_topN.geojson
  2) 정본            → data_임시/step4_output/<도메인>_topN.geojson

`land_id`(후보점이 놓인 필지)는 **공간조인으로 유도**한다. `candidate_lands` 에는
PNU 컬럼이 없어 코드 조인이 불가능하다. 매칭이 안 되면 NULL 로 두고 **몇 건이
안 붙었는지 출력**한다 — 조용히 0으로 채우면 다른 필지를 가리킨다.

`facility_type` 은 감리 확정본(`<도메인>_audit_result_reviewed.json`)의
`facility_inference.facility` 에서 읽는다. 화면5 가 `audit_rules.target_facility`
로 규칙을 고르므로 **같은 어휘**여야 한다. 없으면 멈춘다(추측하지 않는다).

사용:
  python scripts/load_topn_candidates.py 흡연              # 계획만 출력(기본 dry-run)
  python scripts/load_topn_candidates.py 흡연 --yes        # 실제 적재
  python scripts/load_topn_candidates.py 흡연 --run r_20260808_002 --yes
"""

import argparse
import io
import json
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", line_buffering=True
    )

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import psycopg  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# 정본(`runs/` 밖) 산출물을 가리키는 run_id. **`load_audit_data.py` 와 같은 값이어야 한다** —
# 화면5 가 후보점 행의 run_id 로 감리 규칙을 찾기 때문이다. 예전엔 각자 STEP 폴더
# 이름(`step4_output` / `step1_output`)을 넣어 갈려 있었다 (2026-08-10, 사람 승인).
FIXED_RUN_ID = "정본"

# 🔴 기본값을 두지 않는다(2026-08-09, 침해 대응). 없으면 멈춘다.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 🔴 호스트 정규화(localhost→127.0.0.1)와 기본값 금지 판단은 `app/config.py` 한 곳에서
#    한다. 여기서 다시 구현하면 두 벌이 되고 한쪽만 고쳐진다(2026-08-10).
from app.config import DB_CONNECT_TIMEOUT, domain_prefix, settings  # noqa: E402

DSN = settings.DATABASE_URL

# topN 속성 → booth_candidates 고정 컬럼.
# 없으면 NULL 이고, 무엇이 없었는지는 출력한다(있는 척하지 않는다).
COLUMN_MAP = {
    "점수": "score",
    "순위": "rank",
    "면적": "area_m2",
    "내접폭": "width_m",
    "PNU": "pnu",
    "JIBUN": "jibun",
}
# 이 넷은 흡연 도메인 전용 지표이고 topN.geojson 에 없다. NULL 로 남는다.
UNFILLED = ("shops_150m", "dist_transit", "dist_litter", "dist_existing")


def resolve_source(domain: str, run_id: str | None) -> tuple[Path, str]:
    # 🔴 파일명은 도메인 폴더명이 아니라 **프리픽스**다('EV_데이터셋' → 'EV').
    #    raw 도메인으로 조립하면 접미사 붙은 도메인에서만 파일을 못 찾는다 —
    #    흡연·성동 으로만 돌려봐서 안 걸렸다(2026-08-10 정정).
    name = f"{domain_prefix(domain)}_topN.geojson"
    if run_id:
        p = ROOT / "runs" / run_id / "step4" / name
        if not p.exists():
            # 조용히 정본으로 흘러가지 않는다 — 부른 사람은 그 run 을 원한 것이다.
            raise SystemExit(f"🔴 {p} 가 없다. run_id 를 확인할 것.")
        return p, run_id

    p = ROOT / "data_임시" / "step4_output" / name
    if not p.exists():
        raise SystemExit(
            f"🔴 {p} 가 없다. STEP4 를 먼저 돌리거나 --run <run_id> 로 지정할 것."
        )
    return p, FIXED_RUN_ID


def resolve_facility(domain: str, run_id: str | None) -> str:
    """대상 시설명. 화면5 가 `audit_rules.target_facility` 로 조회하므로 출처를 맞춘다.

    🔴 `--run` 을 줬으면 시설명도 **그 run 의 확정본**에서 읽는다(2026-08-10).
       예전엔 topN 만 run 폴더에서 읽고 시설명은 정본에서 읽었다 — 업로드로 만든
       도메인은 정본 `step1_output/` 에 파일이 아예 없어(감리가 run 안에서 돈다)
       거기서 멈췄고, 정본이 **있는 경우가 더 나빴다**: 다른 실행의 시설명이
       이 run 의 후보점에 붙는다. 한 run 의 값은 한 곳에서 온다.
    """
    name = f"{domain_prefix(domain)}_audit_result_reviewed.json"
    p = (ROOT / "runs" / run_id / "step1" / name if run_id
         else ROOT / "data_임시" / "step1_output" / name)
    if not p.exists():
        raise SystemExit(
            f"🔴 {p} 가 없다. 시설명을 추측하지 않는다 — STEP1 확정본이 필요하다."
        )
    doc = json.loads(p.read_text(encoding="utf-8"))
    fac = (doc.get("facility_inference") or {}).get("facility")
    if not fac:
        raise SystemExit(f"🔴 {p} 에 facility_inference.facility 가 없다.")
    return fac


def build_rows(doc: dict, domain: str, run_id: str, facility: str) -> list[dict]:
    feats = doc.get("features") or []
    if not feats:
        raise SystemExit("🔴 topN.geojson 에 feature 가 0개다.")

    rows: list[dict] = []
    for i, f in enumerate(feats):
        geom = f.get("geometry") or {}
        if geom.get("type") != "Point":
            # topN 은 Point 다(CLAUDE.md 규약). 폴리곤이 오면 다른 산출물을 읽은 것이다.
            raise SystemExit(
                f"🔴 features[{i}] geometry 가 Point 가 아니다: {geom.get('type')}"
            )
        lon, lat = geom["coordinates"][0], geom["coordinates"][1]
        props = f.get("properties") or {}

        row: dict = {
            "domain": domain,
            "run_id": run_id,
            "facility_type": facility,
            "lon": lon,
            "lat": lat,
            "props_json": json.dumps(props, ensure_ascii=False),
        }
        for src, dst in COLUMN_MAP.items():
            row[dst] = props.get(src)

        # 국유지 여부. `국유_건수` 가 있을 때만 판정한다 —
        # 컬럼이 없는 도메인에서 False 로 채우면 "국유지가 아니다"라고 단정하게 된다.
        cnt = props.get("국유_건수")
        row["is_national"] = (int(cnt) > 0) if cnt is not None else None

        if row.get("rank") is None:
            raise SystemExit(f"🔴 features[{i}] 에 '순위' 가 없다. 정렬 근거가 사라진다.")
        rows.append(row)

    ranks = sorted(r["rank"] for r in rows)
    if ranks != list(range(1, len(rows) + 1)):
        # 1..N 이 아니면 Top-1 이 최상위라는 전제가 깨진다.
        raise SystemExit(f"🔴 '순위' 가 1..{len(rows)} 연속이 아니다: {ranks[:5]}…")
    return rows


INSERT_SQL = """
INSERT INTO booth_candidates
    (domain, run_id, facility_type, pnu, jibun, area_m2, width_m,
     is_national, score, rank, props_json, geom, land_id)
VALUES
    (%(domain)s, %(run_id)s, %(facility_type)s, %(pnu)s, %(jibun)s,
     %(area_m2)s, %(width_m)s, %(is_national)s, %(score)s, %(rank)s,
     %(props_json)s::jsonb,
     ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326),
     (SELECT cl.id FROM candidate_lands cl
       WHERE ST_Contains(cl.geom, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326))
       LIMIT 1))
RETURNING id, rank, land_id
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("domain", help="도메인 폴더명 (예: 흡연)")
    ap.add_argument("--run", dest="run_id", default=None, help="runs/<run_id> 산출물 사용")
    ap.add_argument(
        "--yes", action="store_true", help="실제로 적재한다 (기본은 계획만 출력)"
    )
    args = ap.parse_args()

    path, run_id = resolve_source(args.domain, args.run_id)
    facility = resolve_facility(args.domain, args.run_id)
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = build_rows(doc, args.domain, run_id, facility)

    present = set()
    for r in rows:
        present |= {k for k, v in r.items() if v is not None}
    missing = [dst for dst in COLUMN_MAP.values() if dst not in present]

    print(f"[입력] {path}")
    print(f"[대상] domain={args.domain} run_id={run_id} facility={facility}")
    print(f"[행수] Top-N **{len(rows)}행** (개수는 STEP4 --topn 이 정한다. 여기 상수 없음)")
    print(f"[점수] 1위 {rows[0]['score']} … {len(rows)}위 {rows[-1]['score']}")
    if missing:
        print(f"[비어있음] topN 에 없어 NULL 로 두는 컬럼: {missing}")
    print(f"[비어있음] 도메인 지표(topN 밖): {list(UNFILLED)}")

    if not args.yes:
        for r in rows[:5]:
            print(
                f"   #{r['rank']:2d} score={r['score']} PNU={r['pnu']} "
                f"({r['lon']:.6f}, {r['lat']:.6f})"
            )
        print(f"   … 이하 {max(0, len(rows) - 5)}행")
        print("\n[dry-run] DB 에 쓰지 않았다. 적재하려면 --yes 를 붙일 것.")
        return 0

    # connect_timeout 을 명시한다 — 없으면 도커가 죽었을 때 libpq 가 260초를
    # 기다리고(실측), 사용자에겐 "느린 스크립트"로 보인다.
    with psycopg.connect(DSN, connect_timeout=DB_CONNECT_TIMEOUT) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'booth_candidates'"
            )
            have = {r[0] for r in cur.fetchall()}
            need = {"domain", "run_id", "facility_type", "pnu", "jibun", "props_json"}
            if not need.issubset(have):
                raise SystemExit(
                    f"🔴 booth_candidates 에 {sorted(need - have)} 가 없다. "
                    "먼저 적용할 것: docker exec -i omnisite-postgres-db "
                    "psql -U postgres -d omnisite < schema_step4_topn.sql"
                )

            # 같은 (domain, run_id) 만 교체한다. 다른 도메인·손으로 넣은 행은 안 건드린다.
            # 🔴 conflict_simulations 가 ON DELETE CASCADE 로 매달려 있다 —
            #    지우면 그 후보점의 토론 결과도 같이 사라진다. 그래서 몇 건이
            #    딸려 나가는지 **지우기 전에** 센다.
            cur.execute(
                "SELECT count(*) FROM conflict_simulations cs "
                "JOIN booth_candidates bc ON bc.id = cs.parcel_id "
                "WHERE bc.domain = %s AND bc.run_id = %s",
                (args.domain, run_id),
            )
            cascaded = cur.fetchone()[0]
            if cascaded:
                print(f"⚠ 기존 행에 매달린 conflict_simulations {cascaded}건이 함께 지워진다")

            cur.execute(
                "DELETE FROM booth_candidates WHERE domain = %s AND run_id = %s",
                (args.domain, run_id),
            )
            deleted = cur.rowcount

            inserted = []
            for r in rows:
                cur.execute(INSERT_SQL, r)
                inserted.append(cur.fetchone())
        conn.commit()

    no_land = [i for i, _rk, land in inserted if land is None]
    print(f"[교체] 기존 {deleted}행 삭제 → {len(inserted)}행 삽입")
    print(f"[land_id] 공간조인 성공 {len(inserted) - len(no_land)} / 실패(NULL) {len(no_land)}")
    top1 = next((i for i, rk, _ in inserted if rk == 1), None)
    print(f"[TOP1] booth_candidates.id = {top1}  ← 화면5 가 쓸 parcel_id")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
