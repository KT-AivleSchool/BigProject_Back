# -*- coding: utf-8 -*-
"""
runs/ 무거운 산출물 정리 (수동 실행)
====================================
  계획만 출력(기본):  python app/tools/prune_runs.py
  실제 삭제:          python app/tools/prune_runs.py --yes
  보관 개수 바꾸기:   python app/tools/prune_runs.py --keep 10

본체는 `app/services/run_pruner.py` 다. **부팅 훅과 이 CLI 가 같은 함수를 쓴다** —
따로 구현하면 "손으로 돌린 것"과 "부팅 때 돈 것"이 다르게 동작한다.

무엇을 지우는지·무엇을 보호하는지는 본체 모듈 docstring 에 실측과 함께 적혀 있다.
요약하면 **폴더를 통째로 지우지 않고** `.gpkg`·`.parquet` 만 지운다(용량의 96.8%).
`status.json`·`run.log`·`topN.geojson` 은 남으므로 「그 run 이 무슨 값을 냈나」는
그대로 읽힌다.

저장소 관례(`reset_db_redis.py`·`load_topn_candidates.py`)대로 **계획만 출력이 기본**이다.
계획은 지울 것뿐 아니라 **안 지우는 것과 그 이유**도 같이 낸다 — 왜 안 지웠는지를
사람이 다시 캐야 하면 계획이 아니다.
"""

import argparse
import asyncio
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.services import run_pruner as P  # noqa: E402


def _print_plan(res: dict) -> int:
    print(f"보관 최근 {res['keep']}개")
    print("-" * 78)
    prune_bytes = prune_files = 0
    for it in res["items"]:
        mark = "지움" if it["action"] == "prune" else "남김"
        print(f"  {it['run_id']:<18s} {mark}  {len(it['files']):>3d}개 "
              f"{it['bytes'] / 2**20:>8.1f} MB  {it['reason']}")
        if it["action"] == "prune":
            prune_bytes += it["bytes"]
            prune_files += len(it["files"])
    print("-" * 78)
    runs = sum(1 for i in res["items"] if i["action"] == "prune")
    print(f"지울 대상: run {runs}개 · 파일 {prune_files}개 · "
          f"{prune_bytes / 2**20:.1f} MB")
    return runs


async def _main() -> int:
    ap = argparse.ArgumentParser(description="runs/ 의 .gpkg·.parquet 정리")
    ap.add_argument("--keep", type=int, default=None,
                    help=f"보관할 최근 run 개수 (기본 {P.keep_count()}, "
                         "OMNISITE_RUNS_KEEP 로도 지정)")
    ap.add_argument("--yes", action="store_true", help="실제로 삭제한다")
    args = ap.parse_args()

    if args.keep is not None and args.keep < 1:
        # 0 이면 전부 지운다는 뜻이 된다. 기본값으로 넘어가면 안 된다(원칙 1).
        print("🔴 --keep 은 1 이상이어야 한다.")
        return 2

    # dry_run 여부와 무관하게 계획을 **먼저 전부 출력**한다. 지운 뒤에 보여주면
    # 사람이 보는 시점엔 이미 되돌릴 수 없다.
    try:
        res = await P.prune_now(keep=args.keep, dry_run=True)
    finally:
        # 안 닫으면 루프가 닫힌 뒤 asyncpg 커넥션이 남아 경고를 뱉는다.
        from app.db.session import engine
        await engine.dispose()
    n = _print_plan(res)

    if not args.yes:
        print("\n계획만 출력했다. 실제로 지우려면 --yes 를 붙인다.")
        return 0
    if n == 0:
        print("\n지울 것이 없다.")
        return 0

    applied = P.apply(res["items"], keep=res["keep"])
    print(f"\n✅ 삭제: run {applied['runs']}개 · 파일 {applied['files']}개 · "
          f"{applied['bytes'] / 2**20:.1f} MB 회수")
    print("   지운 사실은 각 run 의 status.json `pruned` 에 남겼다.")
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(_main())
    except Exception as e:
        # 실패를 print 하고 rc=0 으로 끝나면 지워진 줄 알고 다음을 밟는다(원칙 1).
        print(f"🔴 정리 실패: {type(e).__name__}: {e}")
        raise
    sys.exit(rc)
