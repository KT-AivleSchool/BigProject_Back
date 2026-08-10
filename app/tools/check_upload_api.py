# -*- coding: utf-8 -*-
"""업로드 API 실측 점검 (in-process).

🔴 서버를 재시작하지 않는다. 살아 있는 uvicorn 은 그대로 두고
   `fastapi.testclient.TestClient` 로 앱을 프로세스 안에서 띄워 검증한다.
   (러너 함수를 out-of-process 로 부르면 남의 run 을 밟는다 — CLAUDE.md 참조)

시험용 도메인(`_업로드점검`)을 만들고 끝나면 지운다. 실도메인은 건드리지 않는다.
🔴 조례 적재 검증은 **임베딩 API 를 호출한다**(작은 조례 1건). `--no-ingest` 로 뺄 수 있다.

사용:
  python app\\tools\\check_upload_api.py            # 전체
  python app\\tools\\check_upload_api.py --no-ingest  # 벡터 적재·검색 제외 (LLM 0회)
"""

from __future__ import annotations

import argparse
import io
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", line_buffering=True
    )

TEST_DOMAIN = "_업로드점검"
FACILITY = "점검용시설"  # 실제 시드 태그와 겹치지 않게 — 겹치면 진짜 검색을 오염시킨다

_ok = _ng = 0


def chk(name: str, cond: bool, detail: str = "") -> None:
    global _ok, _ng
    if cond:
        _ok += 1
        print(f"  [OK] {name}" + (f"  {detail}" if detail else ""))
    else:
        _ng += 1
        print(f"  [NG] {name}  {detail}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ingest", action="store_true", help="벡터 DB 적재·검색 생략")
    args = ap.parse_args()
    ingest = not args.no_ingest

    from fastapi.testclient import TestClient

    from app.config import DOMAIN_ROOT
    from app.main import app

    test_root = Path(str(DOMAIN_ROOT)) / TEST_DOMAIN
    if test_root.exists():
        shutil.rmtree(test_root)

    law_src = Path(str(DOMAIN_ROOT)) / "흡연" / "law" / "F1_용산구_간접흡연조례.txt"
    if not law_src.is_file():
        print(f"🔴 시험용 조례 원본이 없다: {law_src}")
        return 2
    law_bytes = law_src.read_bytes()

    # 🔴 TestClient 는 요청마다 **새 이벤트 루프**를 연다(컨텍스트 매니저로 쓰면
    #    lifespan 이 돌아 `reap_orphans()` 가 남의 run 을 닫는다 — 그래서 안 쓴다).
    #    그런데 `app.api.deps.redis_pool` 은 모듈 전역이라 앞 루프에서 만든 커넥션을
    #    다음 요청에 그대로 물려준다 → `'NoneType' object has no attribute 'send'`.
    #    실서버(uvicorn, 루프 1개)에는 없는 문제라 **점검 쪽만** 요청마다 새로 만든다.
    import redis.asyncio as aioredis

    from app.api.deps import get_redis
    from app.config import settings as _settings

    async def _fresh_redis():
        c = aioredis.Redis.from_url(_settings.REDIS_URL, decode_responses=True)
        try:
            yield c
        finally:
            await c.aclose()

    app.dependency_overrides[get_redis] = _fresh_redis

    client = TestClient(app)
    base = "/api/v1/upload"

    try:
        print("\n== 1. 도메인 필수·경로 격리 ==")
        r = client.post(
            f"{base}/regulation",
            data={"domain": TEST_DOMAIN, "facility_type": FACILITY},
            files={"files": ("a.txt", b"x", "text/plain")},
        )
        chk("없는 도메인은 400", r.status_code == 400, f"status={r.status_code}")

        r = client.post(
            f"{base}/regulation",
            data={"domain": "../흡연", "facility_type": FACILITY},
            files={"files": ("a.txt", b"x", "text/plain")},
        )
        chk("도메인 경로조작 400", r.status_code == 400, f"status={r.status_code}")

        r = client.post(
            f"{base}/regulation",
            data={
                "domain": TEST_DOMAIN,
                "facility_type": FACILITY,
                "create_domain": "true",
                "ingest": "false",
            },
            files={"files": ("../../evil.txt", law_bytes, "text/plain")},
        )
        chk("파일명 경로조작 무해화", r.status_code == 200, f"status={r.status_code}")
        chk(
            "저장 위치가 도메인 law/ 안",
            (test_root / "law" / "evil.txt").is_file()
            and not (ROOT / "evil.txt").exists(),
        )

        print("\n== 2. 확장자 정책 ==")
        r = client.post(
            f"{base}/regulation",
            data={"domain": TEST_DOMAIN, "facility_type": FACILITY, "ingest": "false"},
            files={"files": ("조례.hwp", b"x", "application/x-hwp")},
        )
        chk(
            ".hwp 는 400 + 안내",
            r.status_code == 400 and "hwpx" in r.text,
            f"status={r.status_code}",
        )

        r = client.post(
            f"{base}/data",
            data={"domain": TEST_DOMAIN},
            files={"files": ("_숨김.csv", b"a,b\n1,2\n", "text/csv")},
        )
        chk(
            "'_' 로 시작하는 데이터는 400 (프로파일러가 건너뛴다)",
            r.status_code == 400,
            f"status={r.status_code}",
        )

        print("\n== 3. facility_type 출처 ==")
        r = client.post(
            f"{base}/regulation",
            data={"domain": TEST_DOMAIN, "ingest": "false"},
            files={"files": ("b.txt", law_bytes, "text/plain")},
        )
        chk(
            "감리 확정본도 요청값도 없으면 400 (추측 안 함)",
            r.status_code == 400,
            f"status={r.status_code}",
        )

        r = client.post(
            f"{base}/regulation",
            data={"domain": "흡연", "ingest": "false"},
            files={
                "files": (
                    "__점검_임시.txt",
                    "제1조(목적) 점검용.\n".encode("utf-8"),
                    "text/plain",
                )
            },
        )
        ok = r.status_code == 200 and r.json()["facility_type_source"] == "audit_reviewed"
        chk(
            "흡연 도메인은 감리 확정본에서 자동 판별",
            ok,
            f"status={r.status_code} {r.json().get('facility_type') if r.status_code == 200 else r.text[:120]}",
        )
        if r.status_code == 200:
            client.delete(f"{base}/regulations/__점검_임시.txt?domain=흡연")
            chk(
                "점검 잔재 제거됨",
                not (Path(str(DOMAIN_ROOT)) / "흡연" / "law" / "__점검_임시.txt").exists(),
            )

        print("\n== 4. 데이터 업로드 · dataset_id 재부여 ==")
        r = client.post(
            f"{base}/data",
            data={"domain": TEST_DOMAIN},
            files=[
                ("files", ("b_두번째.csv", b"c1,c2\n1,2\n", "text/csv")),
                ("files", ("a_첫번째.csv", b"c1,c2\n3,4\n", "text/csv")),
            ],
        )
        j = r.json() if r.status_code == 200 else {}
        chk(
            "데이터 다중 업로드 200",
            r.status_code == 200,
            f"status={r.status_code} {r.text[:200]}",
        )
        chk(
            "dataset_id 는 가나다순",
            j.get("dataset_map") == {"01": "a_첫번째.csv", "02": "b_두번째.csv"},
            str(j.get("dataset_map")),
        )

        r = client.post(
            f"{base}/data",
            data={"domain": TEST_DOMAIN},
            files={"files": ("0_먼저.csv", b"c1\n9\n", "text/csv")},
        )
        j = r.json()
        chk(
            "앞 번호로 끼어들면 renumbered 로 알린다",
            len(j.get("renumbered", [])) == 3 and j.get("warning"),
            f"renumbered={len(j.get('renumbered', []))}",
        )

        r = client.get(f"{base}/data", params={"domain": TEST_DOMAIN})
        j = r.json()
        chk("Redis 색인 조회 200", r.status_code == 200, f"status={r.status_code}")
        chk("파일 3건 · dataset 3건", j.get("dataset_count") == 3 and len(j["files"]) == 3)
        chk(
            "sha256 이 메타에 남는다",
            bool(j.get("files")) and all(f.get("sha256") for f in j["files"]),
        )

        # 디스크에서 직접 지워도 색인이 거짓말하지 않는지
        (test_root / "data" / "0_먼저.csv").unlink(missing_ok=True)
        j = client.get(f"{base}/data", params={"domain": TEST_DOMAIN}).json()
        chk(
            "디스크와 어긋난 Redis 항목은 제거된다",
            j.get("redis_stale_removed") == ["0_먼저.csv"] and j["dataset_count"] == 2,
            str(j.get("redis_stale_removed")),
        )

        if not ingest:
            print("\n(--no-ingest: 벡터 적재·검색 검증 생략)")
        else:
            print("\n== 5. 조례 적재 · 검색 필터 ==")
            r = client.post(
                f"{base}/regulation",
                data={"domain": TEST_DOMAIN, "facility_type": FACILITY},
                files={"files": ("점검조례.txt", law_bytes, "text/plain")},
            )
            j = r.json() if r.status_code == 200 else {}
            chk("조례 적재 200", r.status_code == 200, r.text[:160])
            f0 = (j.get("files") or [{}])[0]
            chk("청크 생성", f0.get("chunks", 0) > 0, f"chunks={f0.get('chunks')}")
            chk(
                "조문·규제조문 계수",
                f0.get("articles", 0) > 0,
                f"조문={f0.get('articles')} 규제={f0.get('regulatory_articles')} "
                f"입지규정={f0.get('has_siting_provision')}",
            )

            r = client.get(f"{base}/regulations", params={"domain": TEST_DOMAIN})
            items = r.json()
            got = next((i for i in items if i["filename"] == "점검조례.txt"), None)
            chk(
                "목록에 청크 수가 보인다",
                bool(got) and got["chunks_in_vector_db"] == f0.get("chunks"),
                str(got),
            )

            # 재업로드 시 옛 청크가 남지 않는가 (같은 조문 이중 인용 방지)
            r2 = client.post(
                f"{base}/regulation",
                data={"domain": TEST_DOMAIN, "facility_type": FACILITY},
                files={"files": ("점검조례.txt", law_bytes, "text/plain")},
            )
            f1 = r2.json()["files"][0]
            chk(
                "재업로드 시 옛 청크 삭제",
                f1.get("deleted_old_chunks") == f0.get("chunks"),
                f"deleted={f1.get('deleted_old_chunks')} / 이전={f0.get('chunks')}",
            )

            import asyncio

            from app.core.sim_ai.vector_db import get_vector_db

            vdb = get_vector_db()

            async def _probe():
                q = "흡연부스 설치 기준 이격거리"
                mine = await vdb.statutes_store.asimilarity_search_with_relevance_scores(
                    q, k=5, filter={"facility_type": FACILITY}
                )
                other = await vdb.statutes_store.asimilarity_search_with_relevance_scores(
                    q, k=15, filter={"facility_type": "흡연부스"}
                )
                nofilter = (
                    await vdb.statutes_store.asimilarity_search_with_relevance_scores(
                        q, k=15
                    )
                )
                return mine, other, nofilter

            mine, other, nofilter = asyncio.run(_probe())
            chk(
                f"filter='{FACILITY}' 는 업로드분만",
                bool(mine)
                and all(d.metadata.get("facility_type") == FACILITY for d, _ in mine),
                f"{len(mine)}건",
            )
            chk(
                "filter='흡연부스' 에 업로드분이 안 섞인다",
                all(d.metadata.get("facility_type") == "흡연부스" for d, _ in other),
                f"{len(other)}건",
            )
            leaked = sum(
                1 for d, _ in nofilter if d.metadata.get("facility_type") != "흡연부스"
            )
            chk(
                "필터 없으면 다른 시설 조례가 섞인다 (필터가 왜 필요한지)",
                leaked > 0,
                f"무필터 15건 중 타시설 {leaked}건",
            )

            r = client.delete(
                f"{base}/regulations/점검조례.txt", params={"domain": TEST_DOMAIN}
            )
            chk(
                "삭제 시 파일·캐시·청크 동시 제거",
                r.status_code == 200 and r.json()["vector_chunks_removed"] > 0,
                r.text[:160],
            )

    finally:
        # 시험 도메인 정리 — 남기면 다음 사람이 실도메인으로 오해한다
        if ingest:
            try:
                from app.core.sim_ai.vector_db import get_vector_db

                n = get_vector_db().delete_statute_chunks(domain=TEST_DOMAIN)
                if n:
                    print(f"\n(정리) 잔여 청크 {n}건 삭제")
            except Exception as e:
                print(f"\n⚠ 잔여 청크 정리 실패: {e}")
        try:
            import asyncio

            import redis.asyncio as aioredis

            from app.config import settings

            async def _flush():
                c = aioredis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
                await c.delete(f"omnisite:upload:{TEST_DOMAIN}:data")
                await c.aclose()

            asyncio.run(_flush())
        except Exception as e:
            print(f"⚠ Redis 정리 실패: {e}")
        if test_root.exists():
            shutil.rmtree(test_root)
            print(f"(정리) {test_root} 삭제")

    print(f"\n결과: {_ok + _ng} 항목 중 통과 {_ok} / 실패 {_ng}")
    return 0 if _ng == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
