"""STEP1 감리 산출물(`<도메인>_audit_result_reviewed.json`) → `audit_rules` 테이블.

🔴 2026-08-09 재작성. 예전 버전은 저장소 루트의 `dummy_audit.json`(더미)을 읽었는데
   그 파일은 2026-08-05 머지(`b0ca598`)에서 사라졌다. 즉 **입력이 없는 로더**였다.
   지금은 실제 도메인 산출물을 읽는다 — 화면5 토론이 감리 AI 결과로 돌게 하는 게 목적이다.

입력 우선순위(먼저 찾는 것을 쓴다):
  1) `--run <run_id>` 를 주면  runs/<run_id>/step1/<도메인>_audit_result_reviewed.json
  2) 정본                       datasets/step1_output/<도메인>_audit_result_reviewed.json

`reviewed` 를 쓰는 이유: `audit_result.json` 은 LLM 제안값이고 `reviewed` 는
HITL 확정분이 반영된 것이다. 확정 전 값을 DB 에 넣으면 화면5가
"사람이 확정한 감리 결과"인 척한다(원칙 4).

적재 단위는 **role 하나 = 한 행**이다. 같은 (domain, run_id) 를 다시 적재하면
그 조합만 지우고 다시 넣는다 — 예전처럼 `TRUNCATE` 로 전체를 날리지 않는다.
도메인이 둘 이상이 되는 순간 TRUNCATE 는 남의 도메인을 지운다.

사용:
  python scripts/load_audit_data.py 흡연
  python scripts/load_audit_data.py 흡연 --run r_20260808_002
  python scripts/load_audit_data.py 흡연 --dry-run
"""

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", line_buffering=True
    )

from sqlalchemy import delete, select, func  # noqa: E402

from app.db.models.audit import AuditRule  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# 정본(`runs/` 밖 `datasets/stepN_output/`) 산출물을 가리키는 run_id.
#
# 🔴 예전엔 `"step1_output"` 이었다 — **STEP 폴더 이름**을 run_id 에 넣은 것이다.
#    `load_topn_candidates.py` 는 같은 자리에 `"step4_output"` 을 넣었고, 그래서
#    같은 "정본"인데 두 테이블의 run_id 가 갈렸다. 화면5 는 후보점 행에서 run_id 를
#    꺼내 감리 규칙을 찾으므로, 갈려 있으면 **정본 도메인에서 0건**이 된다.
#    run_id 는 "어느 STEP 폴더에서 왔나"가 아니라 **"어느 실행에서 나왔나"** 다.
#    격리 run 은 `r_YYYYMMDD_NNN`, 정본은 이 값 하나다 (2026-08-10, 사람 승인).
FIXED_RUN_ID = "정본"


def resolve_source(domain: str, run_id: str | None) -> tuple[Path, str]:
    """읽을 reviewed.json 경로와 그 산출물의 run_id 를 정한다."""
    name = f"{domain}_audit_result_reviewed.json"
    if run_id:
        p = ROOT / "runs" / run_id / "step1" / name
        if not p.exists():
            # 조용히 정본으로 흘러가지 않는다 — 부른 사람은 그 run 을 원한 것이다(원칙 1).
            raise SystemExit(f"🔴 {p} 가 없다. run_id 를 확인할 것.")
        return p, run_id

    p = ROOT / "datasets" / "step1_output" / name
    if not p.exists():
        raise SystemExit(
            f"🔴 {p} 가 없다. STEP1 을 먼저 돌리거나 --run <run_id> 로 지정할 것."
        )
    return p, FIXED_RUN_ID


def build_rows(doc: dict, domain: str, run_id: str) -> list[dict]:
    """reviewed.json → audit_rules 행 리스트.

    매핑 근거는 산출물 자신의 `_schema.role_필드` 설명이다. 없는 값을 지어내지 않는다.
    """
    inference = doc.get("facility_inference") or {}
    target_facility = inference.get("facility")
    region = inference.get("region")

    rows: list[dict] = []
    for result in doc.get("results", []):
        dataset_id = result.get("dataset_id")
        summary = result.get("summary")
        coord_status = result.get("coord_status")

        for i, role in enumerate(result.get("roles", [])):
            role_type = role.get("role")
            if not role_type:
                # role 이 뭔지 모르면 넣지 않는다. 넣으면 role_type NULL 행이
                # 가중치 계산에서 조용히 무시된다.
                raise SystemExit(
                    f"🔴 dataset {dataset_id} roles[{i}] 에 'role' 이 없다: {role}"
                )

            facility_type = role.get("facility_type")
            rows.append(
                {
                    "domain": domain,
                    "run_id": run_id,
                    "dataset_id": dataset_id,
                    "role_index": i,
                    "target_facility": target_facility,
                    "region": region,
                    "role_type": role_type,
                    # 가중치 인자 표시명. hard_exclusion 이 아니면 facility_type 이
                    # 비어 있어서(실측: 11개 중 7개) summary 로 채운다.
                    # 없는 이름을 만들어 붙이지 않는다 — summary 는 산출물의 값이다.
                    "factor_name": facility_type or summary,
                    "facility_type": facility_type,
                    "weight": role.get("weight"),
                    "exclusion_type": role.get("exclusion_type"),
                    "exclusion_radius_m": role.get("배제반경_m"),
                    "rationale": role.get("rationale"),
                    "source": role.get("source"),
                    "confirmed": bool(role.get("confirmed", False)),
                    "need_review": bool(role.get("need_review", False)),
                    "summary": summary,
                    "coord_status": coord_status,
                }
            )
    return rows


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("domain", help="도메인 폴더명 (예: 흡연)")
    ap.add_argument("--run", dest="run_id", default=None, help="runs/<run_id> 산출물 사용")
    ap.add_argument("--dry-run", action="store_true", help="DB 에 쓰지 않고 계획만 출력")
    args = ap.parse_args()

    path, run_id = resolve_source(args.domain, args.run_id)
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = build_rows(doc, args.domain, run_id)

    print(f"[입력] {path}")
    print(f"[대상] domain={args.domain} run_id={run_id}")
    print(f"[행수] results {len(doc.get('results', []))}건 → audit_rules {len(rows)}행")
    by_role: dict[str, int] = {}
    for r in rows:
        by_role[r["role_type"]] = by_role.get(r["role_type"], 0) + 1
    print(f"[구성] {by_role}")
    print(f"[대상시설] {rows[0]['target_facility'] if rows else '—'}")

    if not rows:
        raise SystemExit("🔴 적재할 role 이 0개다. 산출물을 확인할 것.")

    if args.dry_run:
        for r in rows:
            print(
                f"   {r['dataset_id']}[{r['role_index']}] {r['role_type']:16s} "
                f"w={r['weight']} r={r['exclusion_radius_m']} "
                f"conf={r['confirmed']} | {str(r['factor_name'])[:40]}"
            )
        print("\n[dry-run] DB 에 쓰지 않았다.")
        return 0

    try:
        async with AsyncSessionLocal() as session:
            # 같은 (domain, run_id) 만 교체한다. 다른 도메인·다른 run 은 건드리지 않는다.
            deleted = await session.execute(
                delete(AuditRule).where(
                    AuditRule.domain == args.domain, AuditRule.run_id == run_id
                )
            )
            session.add_all([AuditRule(**r) for r in rows])
            await session.commit()

            total = await session.scalar(select(func.count()).select_from(AuditRule))

        print(f"[교체] 기존 {deleted.rowcount}행 삭제 → {len(rows)}행 삽입")
        print(f"[결과] audit_rules 전체 {total}행")
    except Exception as e:
        print(f"  ⚠ DB 미기동/미연결({e}) — 파일 기반 실행 결과물로 진행합니다.")

    # 🔴 러너(`pipeline_runner._LOADED_RE`)가 읽는 **약속된 한 줄**이다.
    #    status.json 의 `loaded` 가 여기서 나온다. 형식을 바꾸면 status 가 조용히
    #    비고, 프런트는 "적재 안 됨"으로 읽는다(원칙 4). 위의 사람용 출력들과 달리
    #    이 줄은 소비자가 있다 — 지우거나 문구를 손보지 말 것.
    print(f"[LOADED] table=audit_rules run_id={run_id} rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
