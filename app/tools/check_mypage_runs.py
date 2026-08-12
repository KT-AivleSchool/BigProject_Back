# -*- coding: utf-8 -*-
r"""마이페이지 run 이력 — `GET /pipeline/runs?mine=true` 대조기 (실 DB · 실 Redis · LLM 0회).

    python app\tools\check_mypage_runs.py

무엇을 묻는가
-------------
성공이 아니다. 이 엔드포인트에서 **틀리면 안 터지는 자리**가 넷이다(계약 §3-3).

  ① 🔴 **익명 행이 응답에 들어 있는가.** `mine=true` 라는 이름을 따라
     `WHERE user_id = :me` 로 거르면 프런트의 「로그인 없이 실행된 분석 내역」 구획이
     **영원히 비는데 에러가 안 난다**(원칙 4). 이름이 아니라 계약 문장을 따른다.
     ⚠ 반대쪽도 같이 본다 — **남의 행은 안 나와야 한다.** 「내 것 + 익명」이지
     「전부」가 아니다. 남의 run 이 섞이면 프런트에 갈 구획이 없어 「로그인 없이
     실행된」 쪽에 얹히고, 화면이 사실이 아닌 말을 한다.
  ② **토큰 없음·만료·위조·종류틀림 → 401.** `POST /runs` 는 선택적 인증이라
     같은 경로인데 **메서드마다 규약이 다르다** — 익명으로 떨어뜨리면 모든 행이
     `is_mine: false` 가 되어 「로그인했는데 내 기록이 없는 화면」이 된다.
  ③ **`started_at` 이 9시간 밀리지 않는가.** 러너는 naive 로컬 시각을 쓰고 컬럼은
     TIMESTAMPTZ 다. 중간에 `astimezone(KST)` 같은 걸 한 번 더 태우면 값이 바뀐다.
  ④ **넣은 행을 지우고 지워졌는지까지** 본다. 남의 행 개수는 전후 대조.

🔴 **인증을 목으로 갈아끼우지 않는다.** ② 가 이 대조기의 절반인데 `get_current_user`
   를 override 하면 그 절반이 통째로 사라진다 — 실 계정 2개(나 · 남)를 만들고 실제
   토큰으로 친다.

🔴 **lifespan 을 띄우지 않는다**(`httpx.ASGITransport` 기본). 띄우면 `reap_orphans()`
   가 남이 돌리는 run 을 `failed` 로 닫고 부팅 정리가 `runs/` 산출물을 지운다.

🔴 **쓰는 대조기다.** `users` 2행 · `run_records` 3행을 넣는다. 행은 러너가 쓰는
   **정본 함수**(`run_records.record_run_start`)로 넣는다 — 손으로 INSERT 하면
   ③ 이 물어보려던 시각 경로를 건너뛴다.
"""
import asyncio
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
import redis.asyncio as aioredis  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.api.deps import redis_pool  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
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


async def _register(c, email, pw, username):
    r = await c.post(f"{AUTH}/register",
                     json={"email": email, "password": pw, "username": username})
    if r.status_code != 201:
        raise SystemExit(f"가입 실패({r.status_code}) {r.text[:200]}")
    lg = await c.post(f"{AUTH}/login", json={"email": email, "password": pw})
    if lg.status_code != 200:
        raise SystemExit(f"로그인 실패({lg.status_code}) {lg.text[:200]}")
    return r.json()["id"], lg.json()["access_token"]


async def main():
    ts = int(time.time())
    pw = "check_pw_2026!"
    me_email = f"mypage_me_{ts}@example.com"
    other_email = f"mypage_other_{ts}@example.com"

    # run_id 세 개 — 내 것 · 익명 · 남의 것. 시각을 **일부러 어긋나게** 준다
    # (정렬이 started_at 내림차순인지 보려면 순서가 뒤섞여 있어야 한다).
    rid_mine = f"chk_mypage_mine_{ts}"
    rid_anon = f"chk_mypage_anon_{ts}"
    rid_other = f"chk_mypage_other_{ts}"

    # 🔴 러너가 실제로 넣는 모양 그대로 — **naive 로컬** ISO 문자열이다.
    #    tz 를 여기서 붙이면 ③ 이 물어보려던 경로를 대조기가 미리 없애 버린다.
    base = datetime.now().replace(microsecond=0)
    t_mine = base - timedelta(minutes=1)
    t_anon = base - timedelta(minutes=3)   # 가운데
    t_other = base - timedelta(minutes=2)  # 남의 것이 시각상 익명보다 뒤

    r = aioredis.Redis(connection_pool=redis_pool)
    transport = httpx.ASGITransport(app=app)
    me_id = other_id = None

    async with AsyncSessionLocal() as s:
        users_before = (await s.execute(text("SELECT count(*) FROM users"))).scalar_one()
        rr_before = (await s.execute(text("SELECT count(*) FROM run_records"))).scalar_one()
    print(f"--- 0) 사전 상태  users={users_before}행 · run_records={rr_before}행")

    try:
        async with httpx.AsyncClient(transport=transport, timeout=30.0) as c:
            # ---------------------------------------------------------------
            print("--- 1) 임시 계정 2개 (나 · 남) + 토큰")
            me_id, me_token = await _register(c, me_email, pw, f"마이페이지나_{ts}")
            other_id, other_token = await _register(c, other_email, pw, f"마이페이지남_{ts}")
            chk("계정 둘이 서로 다르다", me_id != other_id, f"-> me={me_id} other={other_id}")

            # ---------------------------------------------------------------
            print("--- 2) 행 3개 투입 (정본 함수로 — 손 INSERT 아님)")
            for rid, uid, t, mode in (
                (rid_mine, me_id, t_mine, "fixture"),
                (rid_anon, None, t_anon, "hitl"),
                (rid_other, other_id, t_other, "full"),
            ):
                doc = {
                    "run_id": rid, "user_id": uid, "domain": "흡연", "mode": mode,
                    "status": "queued", "started_at": t.isoformat(timespec="seconds"),
                    "finished_at": None,
                }
                reason = run_records.record_run_start(doc, "마이페이지 대조")
                chk(f"{rid[-14:]} 기록 성공", reason is None, f"-> {reason}")

            # ---------------------------------------------------------------
            print("--- 3) 인증 — 같은 경로인데 메서드마다 규약이 다르다")
            print("       (POST /runs 는 선택 · GET /runs 는 **필수**)")
            expired = create_access_token({"user_id": me_id, "email": me_email},
                                          expires_delta=timedelta(minutes=-1))
            forged = me_token[:-6] + ("A" * 6 if not me_token.endswith("A" * 6) else "B" * 6)
            wrong_type = create_refresh_token({"user_id": me_id, "email": me_email})

            z = await c.get(f"{PIPE}/runs", params={"mine": "true"})
            chk("헤더 없음 → 401 (익명으로 안 떨어뜨린다)", z.status_code == 401,
                f"-> {z.status_code}")
            for name, bad in (("만료", expired), ("위조", forged),
                              ("종류 틀림(refresh)", wrong_type)):
                z = await c.get(f"{PIPE}/runs", params={"mine": "true"},
                                headers={"Authorization": f"Bearer {bad}"})
                chk(f"{name} 토큰 → 401", z.status_code == 401, f"-> {z.status_code}")

            h = {"Authorization": f"Bearer {me_token}"}
            g = await c.get(f"{PIPE}/runs", params={"mine": "true"}, headers=h)
            chk("유효 토큰 → 200", g.status_code == 200, f"-> {g.status_code} {g.text[:120]}")
            if g.status_code != 200:
                return
            body = g.json()

            # ---------------------------------------------------------------
            print("--- 4) 🔴 누가 들어 있는가 — 이 절이 이 엔드포인트의 핵심이다")
            chk("최상위 키가 `runs`", "runs" in body, f"-> {sorted(body)}")
            runs = body.get("runs", [])
            by_id = {x["run_id"]: x for x in runs}

            chk("내 행이 있다", rid_mine in by_id)
            chk("🔴 **익명 행이 있다** (없으면 프런트 아래 구획이 영원히 빈다)",
                rid_anon in by_id, f"-> {'있음' if rid_anon in by_id else '없음'}")
            chk("🔴 **남의 행은 없다** (「내 것 + 익명」이지 「전부」가 아니다)",
                rid_other not in by_id,
                f"-> {'샜다' if rid_other in by_id else '안 샘'}")

            if rid_mine in by_id and rid_anon in by_id:
                m, a = by_id[rid_mine], by_id[rid_anon]
                chk("내 행 is_mine=true", m["is_mine"] is True, f"-> {m['is_mine']}")
                chk("익명 행 is_mine=false (null 이 아니다)", a["is_mine"] is False,
                    f"-> {a['is_mine']}")
                chk("행 키 7개 정확히", sorted(m) == sorted(
                    ["run_id", "domain", "mode", "status", "started_at",
                     "finished_at", "is_mine"]), f"-> {sorted(m)}")
                chk("domain 그대로", m["domain"] == "흡연", f"-> {m['domain']}")
                chk("mode 그대로", (m["mode"], a["mode"]) == ("fixture", "hitl"),
                    f"-> {m['mode']}·{a['mode']}")
                # 🔴 `last_known_status` 를 이름만 바꿔 담는다. 값을 지어내지 않는다 —
                #    `running`·`awaiting_hitl` 이 여기 없다는 것 자체가 정보다.
                chk("status = last_known_status 그대로(queued)",
                    m["status"] == "queued", f"-> {m['status']}")
                chk("아직 안 끝난 run 은 finished_at=null",
                    m["finished_at"] is None, f"-> {m['finished_at']}")

            # 정렬 — 우리 세 행 중 응답에 있는 둘만 놓고 본다
            #        (남의 행이 안 오므로 mine → anon 순이어야 한다).
            order = [x["run_id"] for x in runs if x["run_id"] in (rid_mine, rid_anon)]
            chk("started_at 내림차순 (프런트는 정렬을 안 한다)",
                order == [rid_mine, rid_anon], f"-> {order}")
            allts = [x["started_at"] for x in runs if x["started_at"]]
            chk("응답 전체가 내림차순", allts == sorted(allts, reverse=True))

            # ---------------------------------------------------------------
            print("--- 5) 🔴 시각 — 9시간 밀리지 않는가")
            got = datetime.fromisoformat(by_id[rid_mine]["started_at"])
            chk("tz 가 붙어 있다 (naive 로 나가면 받는 쪽이 제 시각으로 읽는다)",
                got.tzinfo is not None, f"-> {got.isoformat()}")
            # 넣은 값은 naive 로컬. 같은 **순간**이어야 한다 — 표기는 달라도 된다.
            want = t_mine.astimezone()
            drift = abs((got - want).total_seconds())
            chk("넣은 순간과 같다 (오차 0초)", drift == 0,
                f"-> 차이 {drift}초 · 넣음 {t_mine.isoformat()} · 나옴 {got.isoformat()}")
            chk("9시간(32400초) 밀리지 않았다", drift != 32400, f"-> {drift}초")

            # ---------------------------------------------------------------
            print("--- 6) 인자 — 정의 안 된 값은 조용히 넘기지 않는다")
            b4 = await c.get(f"{PIPE}/runs", params={"mine": "false"}, headers=h)
            chk("mine=false → 400 (같은 응답을 주면 「거르는 줄 알았다」가 된다)",
                b4.status_code == 400, f"-> {b4.status_code}")
            chk("400 detail 이 문자열", isinstance(b4.json().get("detail"), str),
                f"-> {b4.json().get('detail')}")
            b5 = await c.get(f"{PIPE}/runs", params={"mine": "yes"}, headers=h)
            chk("mine=yes → 400", b5.status_code == 400, f"-> {b5.status_code}")
            b6 = await c.get(f"{PIPE}/runs", headers=h)
            chk("mine 누락 → 422 (FastAPI 필수 인자)", b6.status_code == 422,
                f"-> {b6.status_code}")

            # ---------------------------------------------------------------
            print("--- 7) 상한 — 잘랐으면 잘랐다고 말하는가")
            chk("total·limit·truncated 가 있다",
                {"total", "limit", "truncated"} <= set(body),
                f"-> {sorted(body)}")
            chk("기본 limit=100", body.get("limit") == 100, f"-> {body.get('limit')}")
            chk("total ≥ 반환 건수", body.get("total", -1) >= len(runs),
                f"-> total={body.get('total')} runs={len(runs)}")
            chk("안 잘렸으면 truncated=false",
                body.get("truncated") is (body.get("total") > len(runs)),
                f"-> {body.get('truncated')}")

            one = await c.get(f"{PIPE}/runs", params={"mine": "true", "limit": 1},
                              headers=h)
            chk("limit=1 → 1건", one.status_code == 200 and len(one.json()["runs"]) == 1,
                f"-> {one.status_code} {len(one.json().get('runs', []))}건")
            chk("🔴 잘렸으면 truncated=true (안 적으면 사용자는 지워진 줄 안다)",
                one.json().get("truncated") is True, f"-> {one.json().get('truncated')}")
            chk("잘려도 total 은 전체 수", one.json().get("total") == body.get("total"),
                f"-> {one.json().get('total')} vs {body.get('total')}")
            chk("limit=0 → 422", (await c.get(
                f"{PIPE}/runs", params={"mine": "true", "limit": 0},
                headers=h)).status_code == 422)

            # ---------------------------------------------------------------
            print("--- 8) 남의 눈으로 — 같은 익명 행이 저쪽에도 보인다")
            g2 = await c.get(f"{PIPE}/runs", params={"mine": "true"},
                             headers={"Authorization": f"Bearer {other_token}"})
            chk("남도 200", g2.status_code == 200, f"-> {g2.status_code}")
            other_by = {x["run_id"]: x for x in g2.json().get("runs", [])}
            chk("남에게는 자기 행이 is_mine=true",
                other_by.get(rid_other, {}).get("is_mine") is True,
                f"-> {other_by.get(rid_other, {}).get('is_mine')}")
            chk("남에게는 내 행이 안 보인다", rid_mine not in other_by)
            # 🔴 익명 run 은 **누구의 것도 아니므로 둘 다에게 보인다.** 규약이다.
            chk("익명 행은 양쪽에 다 보이고 둘 다 is_mine=false",
                other_by.get(rid_anon, {}).get("is_mine") is False,
                f"-> {other_by.get(rid_anon, {}).get('is_mine')}")

            # ---------------------------------------------------------------
            print("--- 9) 끝난 run — finished_at 과 status 가 따라 바뀌는가")
            doc = {
                "run_id": rid_mine, "user_id": me_id, "domain": "흡연",
                "mode": "fixture", "status": "succeeded",
                "started_at": t_mine.isoformat(timespec="seconds"),
                "finished_at": base.isoformat(timespec="seconds"),
            }
            chk("record_run_end 성공", run_records.record_run_end(doc) is None)
            g3 = await c.get(f"{PIPE}/runs", params={"mine": "true"}, headers=h)
            fin = {x["run_id"]: x for x in g3.json()["runs"]}.get(rid_mine, {})
            chk("status=succeeded", fin.get("status") == "succeeded",
                f"-> {fin.get('status')}")
            chk("finished_at 이 채워졌다", fin.get("finished_at") is not None,
                f"-> {fin.get('finished_at')}")
            if fin.get("finished_at"):
                d2 = abs((datetime.fromisoformat(fin["finished_at"])
                          - base.astimezone()).total_seconds())
                chk("finished_at 도 안 밀린다", d2 == 0, f"-> 차이 {d2}초")

    finally:
        # -------------------------------------------------------------------
        print("--- 10) 정리 — 넣은 것만 지운다")
        async with AsyncSessionLocal() as s:
            await s.execute(
                text("DELETE FROM run_records WHERE run_id = ANY(:r)"),
                {"r": [rid_mine, rid_anon, rid_other]},
            )
            for uid in (me_id, other_id):
                if uid is not None:
                    await s.execute(text("DELETE FROM users WHERE id = :i"), {"i": uid})
            await s.commit()

        async with AsyncSessionLocal() as s:
            left = (await s.execute(
                text("SELECT count(*) FROM run_records WHERE run_id = ANY(:r)"),
                {"r": [rid_mine, rid_anon, rid_other]},
            )).scalar_one()
            users_after = (await s.execute(text("SELECT count(*) FROM users"))).scalar_one()
            rr_after = (await s.execute(text("SELECT count(*) FROM run_records"))).scalar_one()
        chk("넣은 run_records 3행 지워졌다", left == 0, f"-> {left}행 남음")
        chk("users 개수 전후 동일", users_after == users_before,
            f"-> {users_before} → {users_after}")
        chk("run_records 개수 전후 동일", rr_after == rr_before,
            f"-> {rr_before} → {rr_after}")

        await r.aclose()

    print(f"\n=== {ok}/{ok + fail} 통과 ===")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
