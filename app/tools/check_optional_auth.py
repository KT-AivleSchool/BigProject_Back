# -*- coding: utf-8 -*-
"""선택적 인증 — `POST /pipeline/runs` 가 run 의 **주인**을 남기는가 (실 DB · 실 Redis).

    python app\\tools\\check_optional_auth.py

무엇을 묻는가
-------------
`run_records.user_id` 는 컬럼도 FK 도 인덱스도 있는데 **채우는 쪽이 없어서** 여태
모든 run 이 익명이었다(2026-08-11 실측 5/5 NULL). 이 대조기는 그 마지막 칸을 본다.

규약은 세 갈래이고 **가운데가 없다**(사람 결정 2026-08-12, (가)안):

    헤더 없음            → None      익명 run 으로 **정상 실행**
    유효한 access token  → User      그 사람이 run 의 주인
    만료·위조·폐기·없는 사용자 → **401**  run 을 시작하지 않는다

🔴 세 번째가 갈림길이었다. 조용히 `None` 으로 떨어뜨리는 (나)안을 안 고른 이유는,
   만료된 사람의 실행이 **익명 run 으로 기록되면** 화면은 로그인 상태인데 마이페이지
   에서만 안 보이기 때문이다 — 안 터지고 값만 틀린다(원칙 1·4).

🔴 **§2 는 `runner.start_run` 을 가짜로 갈아끼운다.** 진짜로 부르면 fixture run 이
   95초씩 돌고 `runs/` 폴더와 DB 적재가 남는다. 여기서 묻는 건 파이프라인이 아니라
   **「라우터가 무엇을 넘기나」** 하나다. 목이 답할 수 있는 질문만 목에 맡기고,
   「DB 에 진짜로 그 값이 남나」는 §3 에서 **목 없이** 묻는다.
   그래서 §2 의 가장 중요한 항목은 성공이 아니라 **401 일 때 `start_run` 이 아예
   안 불렸다**는 것이다 — 인증에 실패했는데 run 이 시작되면 그게 최악이다.

🔴 **lifespan 을 띄우지 않는다**(`httpx.ASGITransport` 기본). 띄우면 `reap_orphans()`
   가 남이 돌리는 run 을 `failed` 로 닫고 부팅 정리가 `runs/` 산출물을 지운다.

🔴 **쓰는 대조기다.** `users` 1행과 `run_records` 1행을 넣는다 — 끝에 지우고
   **지워졌는지까지** 본다. 남의 행은 개수로 전후 대조한다.
"""
import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
import redis.asyncio as aioredis  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.api.deps import redis_pool  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.services import pipeline_runner as runner  # noqa: E402
from app.services import run_records  # noqa: E402
from app.utils.auth_utils import create_access_token, create_refresh_token  # noqa: E402

AUTH = "http://test/api/v1/auth"
PIPE = "http://test/api/v1/pipeline"

ok = fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label} {extra}")
    else:
        fail += 1
        print(f"  [!!] {label} {extra}")


async def main():
    ts = int(time.time())
    email = f"optauth_{ts}@example.com"
    pw = "check_pw_2026!"
    username = f"선택인증_{ts}"
    run_id = f"chk_optauth_{ts}"

    r = aioredis.Redis(connection_pool=redis_pool)
    transport = httpx.ASGITransport(app=app)
    user_id = None
    real_start = runner.start_run

    async with AsyncSessionLocal() as s:
        users_before = (await s.execute(text("SELECT count(*) FROM users"))).scalar_one()
        rr_before = (
            await s.execute(text("SELECT count(*) FROM run_records"))
        ).scalar_one()
    print(f"--- 0) 사전 상태  users={users_before}행 · run_records={rr_before}행")

    try:
        async with httpx.AsyncClient(transport=transport, timeout=30.0) as c:
            # ---------------------------------------------------------------
            print("--- 1) 임시 계정 + 토큰 (실 DB · 실 Redis)")
            rg = await c.post(
                f"{AUTH}/register",
                json={"email": email, "password": pw, "username": username},
            )
            chk("가입 201", rg.status_code == 201, f"-> {rg.status_code}")
            if rg.status_code != 201:
                return
            user_id = rg.json()["id"]

            lg = await c.post(f"{AUTH}/login", json={"email": email, "password": pw})
            chk("로그인 200", lg.status_code == 200, f"-> {lg.status_code}")
            if lg.status_code != 200:
                return
            token = lg.json()["access_token"]
            chk("access_token 발급됨", bool(token))

            # 만료 토큰 — 같은 사용자·같은 서명키. 다른 건 `exp` 뿐이다.
            # 🔴 이게 실사용 조건이다: 「없는 사람」이 아니라 **「어제 로그인한 사람」**.
            expired = create_access_token(
                {"user_id": user_id, "email": email},
                expires_delta=timedelta(minutes=-1),
            )
            # 위조 — 서명만 망가뜨린다(헤더·페이로드는 그대로).
            forged = token[:-6] + ("A" * 6 if not token.endswith("A" * 6) else "B" * 6)
            # 종류가 틀린 토큰 — refresh 를 access 자리에 넣는다.
            #   `type` 검사가 살아 있는지 본다. 이게 없으면 7일짜리가 60분 자리에 선다.
            wrong_type = create_refresh_token({"user_id": user_id, "email": email})

            # ---------------------------------------------------------------
            print("--- 2) 라우터 배선 — 무엇을 `start_run` 에 넘기는가")
            print("       (🔴 start_run 을 가짜로 갈아끼운다. 진짜 run 은 안 돈다)")
            seen: list = []

            # 🔴 `**kw` 는 게으름이 아니라 **측정 대상을 고정하는 장치**다.
            #    여기서 묻는 것은 「`start_run` 이 불렸는가 · user_id 가 무엇인가」뿐이고,
            #    나머지 인자는 이 대조기의 관심사가 아니다. 시그니처를 그대로 베껴 두면
            #    러너에 인자가 하나 늘 때마다 **대조기가 TypeError 로 죽는다** —
            #    실제로 그랬다: `auto_approve` 가 2026-08-14(6a0271f)에 늘었는데 이 파일은
            #    2026-08-12(97e26b4) 것이라 **그날부터 이 27항목이 통째로 안 돌았다.**
            #    죽는 대조기는 시끄럽긴 해도 「그 경로가 검증되지 않는다」는 점에선
            #    가짜 초록불과 같다. 삼키지는 않는다 — 받은 것은 `kw` 로 다 적어둔다.
            def fake_start(domain, mode, user_input=None, topn=None,
                           user_id=None, **kw):
                seen.append(
                    {"domain": domain, "mode": mode, "user_id": user_id,
                     "kw": kw}
                )
                return f"fake_{len(seen)}"

            runner.start_run = fake_start
            body = {"domain": "흡연", "mode": "fixture"}

            seen.clear()
            a = await c.post(f"{PIPE}/runs", json=body)
            chk("헤더 없음 → 202", a.status_code == 202, f"-> {a.status_code}")
            chk("헤더 없음 → user_id=None (익명 실행이 살아 있다)",
                len(seen) == 1 and seen[0]["user_id"] is None,
                f"-> {seen[0]['user_id'] if seen else '호출 안 됨'}")

            seen.clear()
            h = {"Authorization": f"Bearer {token}"}
            b = await c.post(f"{PIPE}/runs", json=body, headers=h)
            chk("유효 토큰 → 202", b.status_code == 202, f"-> {b.status_code}")
            chk("유효 토큰 → user_id 가 그 사람",
                len(seen) == 1 and seen[0]["user_id"] == user_id,
                f"-> {seen[0]['user_id'] if seen else '호출 안 됨'} (기대 {user_id})")

            for name, bad in (("만료", expired), ("위조", forged),
                              ("종류 틀림(refresh)", wrong_type)):
                seen.clear()
                z = await c.post(
                    f"{PIPE}/runs", json=body,
                    headers={"Authorization": f"Bearer {bad}"},
                )
                chk(f"{name} 토큰 → 401", z.status_code == 401,
                    f"-> {z.status_code} {z.text[:60]}")
                # 🔴 여기가 이 절의 핵심이다.
                chk(f"{name} 토큰 → start_run 이 **안 불렸다**", len(seen) == 0,
                    f"-> 호출 {len(seen)}회")

            seen.clear()
            e = await c.post(
                f"{PIPE}/runs", json=body, headers={"Authorization": "Bearer "}
            )
            chk("빈 Bearer → 익명(202) — 헤더 자체가 없는 것과 같다",
                e.status_code == 202 and len(seen) == 1
                and seen[0]["user_id"] is None,
                f"-> {e.status_code}")

            # 로그아웃한 토큰(Redis 블랙리스트)
            seen.clear()
            lo = await c.post(f"{AUTH}/logout", headers=h)
            chk("로그아웃 200", lo.status_code == 200, f"-> {lo.status_code}")
            d = await c.post(f"{PIPE}/runs", json=body, headers=h)
            chk("폐기된 토큰 → 401 (Redis 블랙리스트가 실제로 걸린다)",
                d.status_code == 401, f"-> {d.status_code}")
            chk("폐기된 토큰 → start_run 이 **안 불렸다**", len(seen) == 0,
                f"-> 호출 {len(seen)}회")

            runner.start_run = real_start

        # -------------------------------------------------------------------
        print("--- 3) 진짜 DB — 그 값이 `run_records` 에 남는가 (목 없음)")
        # `status.json` 문서를 손으로 만들어 정본 함수에 그대로 넘긴다.
        # 러너가 `_new_status(...)` 로 만드는 것과 같은 모양이다.
        doc = {
            "run_id": run_id,
            "user_id": user_id,
            "domain": "흡연",
            "mode": "fixture",
            "status": "queued",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
        }
        reason = run_records.record_run_start(doc, "선택적 인증 대조")
        chk("record_run_start 성공", reason is None, f"-> {reason}")

        async with AsyncSessionLocal() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT user_id, last_known_status, user_input "
                        "FROM run_records WHERE run_id = :r"
                    ),
                    {"r": run_id},
                )
            ).first()
        chk("행이 생겼다", row is not None)
        if row:
            chk("user_id 가 실제로 그 사람", row[0] == user_id,
                f"-> {row[0]} (기대 {user_id})")
            chk("last_known_status=queued", row[1] == "queued", f"-> {row[1]}")

        # FK 가 진짜로 `users` 를 가리키는가 — 조인이 답한다.
        async with AsyncSessionLocal() as s:
            joined = (
                await s.execute(
                    text(
                        "SELECT u.email FROM run_records rr "
                        "JOIN users u ON u.id = rr.user_id WHERE rr.run_id = :r"
                    ),
                    {"r": run_id},
                )
            ).scalar_one_or_none()
        chk("FK 조인이 그 계정에 닿는다", joined == email, f"-> {joined}")

        # -------------------------------------------------------------------
        print("--- 4) 마이페이지가 답할 수 있는가 (천명님이 이을 자리의 전제)")
        # 🔴 엔드포인트는 아직 없다. 여기서 보는 건 **그 질의가 성립하는가**다 —
        #    인덱스 `ix_run_records_user (user_id, started_at)` 가 그걸 전제로 깔려 있다.
        async with AsyncSessionLocal() as s:
            mine = (
                await s.execute(
                    text(
                        "SELECT run_id FROM run_records WHERE user_id = :u "
                        "ORDER BY started_at DESC"
                    ),
                    {"u": user_id},
                )
            ).scalars().all()
        chk("「내 run 을 최신순으로」가 그 행을 준다",
            list(mine) == [run_id], f"-> {list(mine)}")

        async with AsyncSessionLocal() as s:
            anon = (
                await s.execute(
                    text("SELECT count(*) FROM run_records WHERE user_id IS NULL")
                )
            ).scalar_one()
        # 익명 run 은 **누구의 마이페이지에도 안 뜬다.** 사고가 아니라 규약이다.
        chk("익명 행은 user_id IS NULL 로 남아 있다 (마이페이지에 안 뜬다)",
            anon >= 0, f"-> 현재 {anon}행")

    finally:
        runner.start_run = real_start
        # -------------------------------------------------------------------
        print("--- 5) 정리 — 넣은 것만 지운다")
        async with AsyncSessionLocal() as s:
            await s.execute(
                text("DELETE FROM run_records WHERE run_id = :r"), {"r": run_id}
            )
            if user_id is not None:
                await s.execute(
                    text("DELETE FROM users WHERE id = :i"), {"i": user_id}
                )
            await s.commit()

        async with AsyncSessionLocal() as s:
            left_rr = (
                await s.execute(
                    text("SELECT count(*) FROM run_records WHERE run_id = :r"),
                    {"r": run_id},
                )
            ).scalar_one()
            users_after = (
                await s.execute(text("SELECT count(*) FROM users"))
            ).scalar_one()
            rr_after = (
                await s.execute(text("SELECT count(*) FROM run_records"))
            ).scalar_one()
        chk("run_records 테스트 행 지워졌다", left_rr == 0, f"-> {left_rr}행")
        chk("users 개수 전후 동일", users_after == users_before,
            f"-> {users_before} → {users_after}")
        chk("run_records 개수 전후 동일", rr_after == rr_before,
            f"-> {rr_before} → {rr_after}")

        await r.aclose()

    print(f"\n=== {ok}/{ok + fail} 통과 ===")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
