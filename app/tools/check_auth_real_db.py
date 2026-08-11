# -*- coding: utf-8 -*-
"""`/auth/*` 4개를 **실 DB · 실 Redis** 로 대조 (in-process ASGI · LLM 0회).

    python app\\tools\\check_auth_real_db.py

기존 `check_auth_dual_token.py` 는 `get_db`·`get_redis` 를 **InMemoryDB/Redis 목**으로
갈아끼운다. 그건 「라우터 로직이 맞나」만 본다 — 실제로 확인이 안 되던 것들:

  · `users` 테이블이 ORM 과 맞는가 (목은 dict 라 컬럼이 틀려도 통과한다)
  · Redis 키에 **TTL 이 실제로 붙는가** (목의 `expire` 는 `pass` 다)
  · `keys()` 패턴이 실 Redis 에서 같은 집합을 주는가 (목은 `startswith` 다)
  · bcrypt 해시가 DB 왕복 후에도 검증되는가

🔴 **lifespan 을 띄우지 않는다**(`httpx.ASGITransport` 는 기본이 그렇다).
   띄우면 `reap_orphans()` 가 남이 돌리는 run 을 `failed` 로 닫고
   부팅 정리(`prune_runs`)가 `runs/` 의 산출물을 지운다. 이 대조는 auth 만 본다.

🔴 **쓰는 대조기다.** `users` 에 한 행을 넣고 Redis 에 키를 만든다 —
   끝에 반드시 지우고, 지워졌는지까지 확인한다(마지막 항목).
   기존 사용자 행은 손대지 않는다(테스트 이메일로만 지운다).
"""
import asyncio
import sys
import time
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
import redis.asyncio as aioredis  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.api.deps import redis_pool  # noqa: E402
from app.config import settings  # noqa: E402
from app.db.base import User  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.utils.auth_utils import decode_token  # noqa: E402

BASE = "http://test/api/v1/auth"

# 🔴 **설계값이다 — 설정값이 아니다.** `settings.ACCESS_TOKEN_EXPIRE_MINUTES` 를
#    쓰면 재는 자와 재어지는 자가 같아져 `.env` 에 뭘 적어도 통과한다(가짜 초록불).
#    여기 손으로 적힌 숫자와 설정값이 갈리는 순간이 곧 사고 발견 시점이다.
_DESIGN_ACCESS_MIN = 60
_DESIGN_REFRESH_DAYS = 7

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
    # 🔴 `@omnisite.local` 은 422 다 — pydantic `EmailStr`(email-validator)이
    #    `.local`·`.test` 같은 특수용도 TLD 를 거절한다. `example.com` 은 예약이라 통과한다.
    email = f"authchk_{ts}@example.com"
    pw = "check_pw_2026!"
    username = f"대조_{ts}"

    r = aioredis.Redis(connection_pool=redis_pool)
    transport = httpx.ASGITransport(app=app)
    user_id = None
    blacklisted = []

    # 기존 사용자 수 — 끝에 같은 값으로 돌아와야 한다.
    async with AsyncSessionLocal() as s:
        before = (await s.execute(text("SELECT count(*) FROM users"))).scalar_one()
    print(f"--- 0) 사전 상태  users={before}행 · 테스트 계정 {email}")

    try:
        async with httpx.AsyncClient(
            transport=transport, base_url=BASE, timeout=30.0
        ) as c:
            print("--- 1) 회원가입 (실 DB 에 INSERT)")
            rr = await c.post(
                "/register",
                json={"email": email, "password": pw, "username": username},
            )
            chk("201", rr.status_code == 201, f"-> {rr.status_code} {rr.text[:80]}")
            if rr.status_code != 201:
                return
            body = rr.json()
            user_id = body.get("id")
            chk("응답에 id", isinstance(user_id, int), f"-> {user_id}")
            chk("email 반환", body.get("email") == email)
            chk("username 반환", body.get("username") == username)
            chk("해시는 응답에 없다", "hashed_password" not in body)

            # 🔴 목이 못 보던 자리 — 진짜로 행이 생겼고 컬럼이 ORM 과 맞는가.
            async with AsyncSessionLocal() as s:
                row = (
                    await s.execute(select(User).where(User.email == email))
                ).scalars().first()
            chk("DB 에 실제 행", row is not None)
            if row:
                chk("DB id == 응답 id", row.id == user_id, f"{row.id} == {user_id}")
                chk("is_active 기본 True", row.is_active is True)
                chk("bcrypt 해시 저장됨", row.hashed_password.startswith("$2"))
                chk("평문 저장 아님", row.hashed_password != pw)

            print("--- 2) 같은 이메일 재가입 (UNIQUE 는 DB 제약이다)")
            r2 = await c.post(
                "/register", json={"email": email, "password": pw, "username": "x"}
            )
            chk("400", r2.status_code == 400, f"-> {r2.status_code}")
            async with AsyncSessionLocal() as s:
                n = (
                    await s.execute(
                        text("SELECT count(*) FROM users WHERE email = :e"),
                        {"e": email},
                    )
                ).scalar_one()
            chk("행이 늘지 않았다", n == 1, f"-> {n}행")

            print("--- 3) 로그인 실패 카운터 (실 Redis)")
            f1 = await c.post("/login", json={"email": email, "password": "틀린값"})
            chk("1회 401", f1.status_code == 401, f"-> {f1.status_code}")
            cnt = await r.get(f"lockout:count:{email}")
            chk("count 키 = 1", cnt == "1", f"-> {cnt}")
            ttl = await r.ttl(f"lockout:count:{email}")
            # 목의 `expire` 는 pass 라 여기서만 드러난다. 무TTL 이면 -1 이다.
            chk("count 키에 TTL 있음(≤300)", 0 < ttl <= 300, f"-> {ttl}s")

            await c.post("/login", json={"email": email, "password": "틀린값"})
            f3 = await c.post("/login", json={"email": email, "password": "틀린값"})
            chk("3회 401", f3.status_code == 401, f"-> {f3.status_code}")
            chk(
                "3회째는 남은 횟수를 말한다",
                "3회 실패" in f3.json().get("detail", ""),
                f"-> {f3.json().get('detail', '')[:50]}",
            )

            print("--- 4) 로그인 성공 → 듀얼 토큰 + 실패 카운터 리셋")
            lr = await c.post("/login", json={"email": email, "password": pw})
            chk("200", lr.status_code == 200, f"-> {lr.status_code} {lr.text[:80]}")
            if lr.status_code != 200:
                return
            tok = lr.json()
            at1, rt1 = tok["access_token"], tok["refresh_token"]
            chk("count 키 삭제됨", await r.get(f"lockout:count:{email}") is None)
            pa, pr = decode_token(at1), decode_token(rt1)
            chk("access type", pa.get("type") == "access", f"-> {pa.get('type')}")
            chk("refresh type", pr.get("type") == "refresh", f"-> {pr.get('type')}")
            chk("user_id 실려 있다", pa.get("user_id") == user_id)
            life_a = (pa["exp"] - pa["iat"]) / 60
            life_r = (pr["exp"] - pr["iat"]) / 86400
            # ⓐ 구현 정합 — 토큰이 설정값대로 발급되는가.
            chk(
                "access 수명 == 설정값",
                abs(life_a - settings.ACCESS_TOKEN_EXPIRE_MINUTES) < 0.1,
                f"-> {life_a:.1f}분 (설정 {settings.ACCESS_TOKEN_EXPIRE_MINUTES})",
            )
            chk(
                "refresh 수명 == 설정값",
                abs(life_r - settings.REFRESH_TOKEN_EXPIRE_DAYS) < 0.01,
                f"-> {life_r:.2f}일 (설정 {settings.REFRESH_TOKEN_EXPIRE_DAYS})",
            )
            # ⓑ 설정 정합 — 🔴 위 둘만 보면 **자기 자신과 비교**라 항상 통과한다
            #    (`.env` 에 뭘 적어도 초록불이다. 「대조기가 없는 키를 읽음」의 사촌).
            #    듀얼 토큰이 성립하려면 access 가 refresh 보다 **짧아야** 한다 —
            #    같으면 짧은 access + 회전 refresh 라는 구조 자체가 없다.
            chk(
                "access < refresh (듀얼 토큰의 전제)",
                life_a * 60 < life_r * 86400,
                f"-> access {life_a:.0f}분 vs refresh {life_r * 1440:.0f}분",
            )
            # 설계값은 `app/config.py` 기본값 · `.env.example` · `.env` **셋이 같아야**
            # 한다. 하나만 다르면 설정 사고다 — 실제로 2026-08-11 까지 `.env` 만
            # 10080(옛 단일 토큰 값)이라 위 ⓑ 불변식이 깨져 있었다.
            # 🔴 여기 숫자를 바꿀 땐 저 세 곳을 **같이** 바꾼다. 대조기만 고치면
            #    대조기가 사라진 설계값을 계속 요구하고, 세 곳만 고치면 대조기가
            #    영원히 빨간불이라 아무도 안 본다.
            # 60분: PR #221 은 15분이었으나 프런트에 refresh 재발급 로직이 있는지
            # 확인되지 않아 올렸다(2026-08-11, 사람 결정). 확인되면 15로 되돌린다.
            chk(
                f"access 설계값 {_DESIGN_ACCESS_MIN}분",
                settings.ACCESS_TOKEN_EXPIRE_MINUTES == _DESIGN_ACCESS_MIN,
                f"-> {settings.ACCESS_TOKEN_EXPIRE_MINUTES}분 (.env 의 ACCESS_TOKEN_EXPIRE_MINUTES)",
            )
            # refresh 도 같이 본다. 🔴 access 만 대조하면 **refresh 를 내리는 것으로도**
            #    ⓑ 불변식을 만족시킬 수 있다 — 그건 만족이 아니라 로그인 유지 시간이
            #    줄어든 것이다(작업 유지를 정하는 건 refresh 쪽이다).
            chk(
                f"refresh 설계값 {_DESIGN_REFRESH_DAYS}일",
                settings.REFRESH_TOKEN_EXPIRE_DAYS == _DESIGN_REFRESH_DAYS,
                f"-> {settings.REFRESH_TOKEN_EXPIRE_DAYS}일",
            )
            k1 = f"refresh_token:{user_id}:{pr['jti']}"
            chk("Redis 에 refresh 키", await r.exists(k1) == 1, f"-> {k1[:40]}…")
            kt = await r.ttl(k1)
            want = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400
            chk("refresh 키 TTL == 7일", want - 5 <= kt <= want, f"-> {kt}s (want {want})")

            print("--- 5) RTR — 갱신하면 옛 키가 사라진다")
            fr = await c.post("/refresh", json={"refresh_token": rt1})
            chk("200", fr.status_code == 200, f"-> {fr.status_code} {fr.text[:80]}")
            nt = fr.json()
            at2, rt2 = nt["access_token"], nt["refresh_token"]
            chk("access 교체됨", at1 != at2)
            chk("refresh 교체됨", rt1 != rt2)
            k2 = f"refresh_token:{user_id}:{decode_token(rt2)['jti']}"
            chk("옛 refresh 키 삭제됨", await r.exists(k1) == 0)
            chk("새 refresh 키 존재", await r.exists(k2) == 1)

            print("--- 6) access 토큰을 /refresh 에 넣으면 거절")
            ra = await c.post("/refresh", json={"refresh_token": at2})
            chk("401", ra.status_code == 401, f"-> {ra.status_code}")
            chk(
                "사유가 type 이라고 말한다",
                "Refresh Token이 아닙니다" in ra.json().get("detail", ""),
                f"-> {ra.json().get('detail', '')[:40]}",
            )
            # 🔴 이 갈래는 Redis 를 안 건드려야 한다 — 건드리면 정상 세션이 죽는다.
            chk("정상 세션은 그대로", await r.exists(k2) == 1)

            print("--- 7) 쓴 refresh 재사용 → Family Revocation")
            # 살아 있는 세션이 둘일 때 **둘 다** 지워지는지 본다(목의 startswith 로는
            # 확인이 안 된다). 두 번째 로그인으로 세션을 하나 더 만든다.
            lr2 = await c.post("/login", json={"email": email, "password": pw})
            k3 = f"refresh_token:{user_id}:{decode_token(lr2.json()['refresh_token'])['jti']}"
            chk("세션 2개", await r.exists(k2) == 1 and await r.exists(k3) == 1)
            st = await c.post("/refresh", json={"refresh_token": rt1})  # 이미 쓴 것
            chk("401", st.status_code == 401, f"-> {st.status_code}")
            live = await r.keys(f"refresh_token:{user_id}:*")
            chk("그 유저 refresh 키 전멸", len(live) == 0, f"-> {len(live)}개 남음")
            chk("k2 도 삭제", await r.exists(k2) == 0)
            chk("k3 도 삭제", await r.exists(k3) == 0)

            print("--- 8) 로그아웃 — 블랙리스트 + refresh 정리")
            lr3 = await c.post("/login", json={"email": email, "password": pw})
            at3 = lr3.json()["access_token"]
            jti3 = decode_token(at3)["jti"]
            blacklisted.append(jti3)
            k4 = f"refresh_token:{user_id}:{decode_token(lr3.json()['refresh_token'])['jti']}"
            lo = await c.post("/logout", headers={"Authorization": f"Bearer {at3}"})
            chk("200", lo.status_code == 200, f"-> {lo.status_code}")
            chk("blacklist 키 생김", await r.exists(f"blacklist:{jti3}") == 1)
            bt = await r.ttl(f"blacklist:{jti3}")
            cap = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
            # TTL 은 **남은 만료 시간**이지 15분 고정이 아니다.
            chk("blacklist TTL ≤ access 수명", 0 < bt <= cap, f"-> {bt}s (cap {cap})")
            chk("refresh 키 정리됨", await r.exists(k4) == 0)

            print("--- 9) 블랙리스트된 access 는 다시 못 쓴다")
            lo2 = await c.post("/logout", headers={"Authorization": f"Bearer {at3}"})
            chk("401", lo2.status_code == 401, f"-> {lo2.status_code}")
            chk("헤더 없으면 401", (await c.post("/logout")).status_code == 401)

            print("--- 10) 5회 실패 → 계정 잠금 403")
            for _ in range(5):
                last = await c.post(
                    "/login", json={"email": email, "password": "틀린값"}
                )
            chk("5회째 403", last.status_code == 403, f"-> {last.status_code}")
            chk("block 키 생김", await r.exists(f"lockout:block:{email}") == 1)
            bt2 = await r.ttl(f"lockout:block:{email}")
            chk("block TTL ≤300", 0 < bt2 <= 300, f"-> {bt2}s")
            chk("count 키는 리셋됨", await r.exists(f"lockout:count:{email}") == 0)
            # 🔴 잠긴 뒤엔 **비밀번호가 맞아도** 막혀야 한다. 안 막히면 잠금이 아니다.
            good = await c.post("/login", json={"email": email, "password": pw})
            chk("맞는 비번도 403", good.status_code == 403, f"-> {good.status_code}")

    finally:
        # ── 정리. 실패해도 반드시 지운다 ────────────────────────────────────
        print("--- 11) 정리")
        keys = [f"lockout:count:{email}", f"lockout:block:{email}"]
        keys += [f"blacklist:{j}" for j in blacklisted]
        if user_id:
            keys += await r.keys(f"refresh_token:{user_id}:*")
        if keys:
            await r.delete(*keys)
        async with AsyncSessionLocal() as s:
            await s.execute(text("DELETE FROM users WHERE email = :e"), {"e": email})
            await s.commit()
            after = (await s.execute(text("SELECT count(*) FROM users"))).scalar_one()
        chk("테스트 계정 삭제됨", after == before, f"-> {after}행 (before {before})")
        left = [k for k in keys if await r.exists(k)]
        chk("Redis 잔여 키 0", not left, f"-> {left}")
        await r.aclose()

    print(f"\n{ok + fail}항목 중 {ok} 통과 · {fail} 실패")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
