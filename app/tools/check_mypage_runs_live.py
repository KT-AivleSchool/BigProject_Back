# -*- coding: utf-8 -*-
r"""마이페이지 run 이력 — **살아 있는 서버**로 27항목.

사용:  python app\tools\check_mypage_runs_live.py [base_url]
       (기본 http://127.0.0.1:8000/api/v1)

🔴 `check_mypage_runs.py`(48항목)와 **묻는 게 다르다.** 저쪽은 `httpx.ASGITransport`
   라 자기 프로세스에 **방금 import 한** 코드를 잰다 — 살아 있는 uvicorn 이 옛
   프로세스여도 초록불이다(2026-08-12 실제 사고: 죽은 토큰에 401 이 아니라 202).
   그래서 항목이 적어도 이 파일이 없으면 **배포됐다**를 말할 수 없다.
   ⚠ 이것도 「지금 이 순간 그 포트가 새 코드다」까지다. 근거의 뿌리는 여전히
   **프로세스 기동 시각 ↔ 파일 mtime** 이고 이건 그 위에 얹는 확인이다.

🔴 묻는 것은 성공만이 아니라 **거절**이다 — GET 은 인증이 필수라(`POST /runs` 는
   선택) 401 네 갈래와 `mine=false` 400 이 절반을 차지한다.
🔴 그리고 **익명 행이 응답에 들어 있는가** — 걸러도 200 이 나오고 화면 아래 구획만
   조용히 빈다(계약 §3-3-1).

`users`·`run_records` 에 **실제로 넣고 지운다**. 끝에 남의 행 개수를 전후로 댄다.
"""
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
import psycopg  # noqa: E402

from app.config import DB_CONNECT_TIMEOUT, settings  # noqa: E402
from app.services import run_records  # noqa: E402
from app.utils.auth_utils import create_access_token, create_refresh_token  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/api/v1").rstrip("/")
ok = fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label} {extra}")
    else:
        fail += 1
        print(f"  [!!] {label} {extra}")


def _dsn():
    u = settings.DATABASE_URL
    for pre in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        if u.startswith(pre):
            u = u.replace(pre, "postgresql://", 1)
    return u


def _q(sql, args=()):
    with psycopg.connect(_dsn(), connect_timeout=DB_CONNECT_TIMEOUT) as cx:
        with cx.cursor() as cur:
            cur.execute(sql, args)
            if cur.description:
                return cur.fetchall()
    return None


ts = int(time.time())
email = f"live_mypage_{ts}@example.com"
pw = "live_pw_2026!"
rid = f"live_mypage_{ts}"
uid = None

users_before = _q("SELECT count(*) FROM users")[0][0]
rr_before = _q("SELECT count(*) FROM run_records")[0][0]
print(f"--- 0) 사전 users={users_before}행 · run_records={rr_before}행")

try:
    with httpx.Client(timeout=30.0) as c:
        print("--- 1) 실 서버 계정 발급")
        r = c.post(f"{BASE}/auth/register",
                   json={"email": email, "password": pw, "username": f"라이브_{ts}"})
        if r.status_code != 201:
            raise SystemExit(f"가입 실패 {r.status_code} {r.text[:200]}")
        uid = r.json()["id"]
        lg = c.post(f"{BASE}/auth/login", json={"email": email, "password": pw})
        if lg.status_code != 200:
            raise SystemExit(f"로그인 실패 {lg.status_code} {lg.text[:200]}")
        token = lg.json()["access_token"]
        print(f"      user_id={uid}")

        print("--- 2) 내 행 1개 투입 (정본 record_run_start)")
        doc = {
            "run_id": rid, "user_id": uid, "domain": "흡연", "mode": "fixture",
            "status": "queued",
            "started_at": datetime.now().replace(microsecond=0).isoformat(timespec="seconds"),
            "finished_at": None,
        }
        reason = run_records.record_run_start(doc, "실서버 대조")
        chk("행 기록 성공", reason is None, f"-> {reason}")

        print("--- 3) 인증 — GET 은 필수다")
        z = c.get(f"{BASE}/pipeline/runs", params={"mine": "true"})
        chk("헤더 없음 → 401", z.status_code == 401, f"-> {z.status_code}")
        expired = create_access_token({"user_id": uid, "email": email},
                                      expires_delta=timedelta(minutes=-1))
        forged = token[:-6] + ("A" * 6 if not token.endswith("A" * 6) else "B" * 6)
        wrong = create_refresh_token({"user_id": uid, "email": email})
        for name, bad in (("만료", expired), ("위조", forged), ("refresh를access자리", wrong)):
            z = c.get(f"{BASE}/pipeline/runs", params={"mine": "true"},
                      headers={"Authorization": f"Bearer {bad}"})
            chk(f"{name} → 401", z.status_code == 401, f"-> {z.status_code}")

        h = {"Authorization": f"Bearer {token}"}
        z = c.get(f"{BASE}/pipeline/runs", params={"mine": "false"}, headers=h)
        chk("mine=false → 400", z.status_code == 400, f"-> {z.status_code}")
        chk("400 detail 이 문자열", isinstance(z.json().get("detail"), str),
            f"-> {z.json().get('detail')}")

        print("--- 4) 유효 토큰")
        g = c.get(f"{BASE}/pipeline/runs", params={"mine": "true"}, headers=h)
        chk("200", g.status_code == 200, f"-> {g.status_code} {g.text[:160]}")
        if g.status_code != 200:
            raise SystemExit("이후 항목은 볼 수 없다")
        body = g.json()
        chk("최상위 키 4개", sorted(body) == ["limit", "runs", "total", "truncated"],
            f"-> {sorted(body)}")
        runs = body["runs"]
        chk("limit 기본 100", body["limit"] == 100, f"-> {body['limit']}")
        chk("total 이 정수", isinstance(body["total"], int), f"-> {body['total']}")
        chk("truncated 는 total>len(runs)",
            body["truncated"] == (body["total"] > len(runs)),
            f"-> total={body['total']} len={len(runs)} truncated={body['truncated']}")

        keys = {"run_id", "domain", "mode", "status", "started_at", "finished_at", "is_mine"}
        chk("행 키 7개 정확 일치", all(set(x) == keys for x in runs),
            f"-> {sorted(runs[0]) if runs else '없음'}")

        mine_rows = [x for x in runs if x["is_mine"]]
        anon_rows = [x for x in runs if not x["is_mine"]]
        chk("🔴 익명 행이 응답에 들어 있다", len(anon_rows) > 0, f"-> {len(anon_rows)}건")
        chk("내 행이 들어 있다", any(x["run_id"] == rid for x in runs),
            f"-> is_mine {len(mine_rows)}건")
        got = next((x for x in runs if x["run_id"] == rid), None)
        chk("내 행 is_mine=true", bool(got and got["is_mine"]))
        chk("내 행 status 는 last_known_status 그대로",
            bool(got and got["status"] == "queued"), f"-> {got and got['status']}")
        chk("내 행 finished_at=null", bool(got and got["finished_at"] is None))
        chk("started_at 에 tz 오프셋이 붙어 있다",
            bool(got and ("+" in got["started_at"][10:] or got["started_at"].endswith("Z"))),
            f"-> {got and got['started_at']}")

        starts = [x["started_at"] for x in runs]
        chk("started_at 내림차순", starts == sorted(starts, reverse=True))

        print("--- 5) ?limit= 이 실제로 먹는가")
        g2 = c.get(f"{BASE}/pipeline/runs", params={"mine": "true", "limit": 1}, headers=h)
        chk("limit=1 → 1행", g2.status_code == 200 and len(g2.json()["runs"]) == 1,
            f"-> {g2.status_code} {len(g2.json().get('runs', []))}행")
        chk("limit=1 이면 truncated=true", g2.json()["truncated"] is True)
        chk("limit=1 이어도 total 은 전체", g2.json()["total"] == body["total"],
            f"-> {g2.json()['total']} vs {body['total']}")
        z = c.get(f"{BASE}/pipeline/runs", params={"mine": "true", "limit": 501}, headers=h)
        chk("limit=501 → 422", z.status_code == 422, f"-> {z.status_code}")
        z = c.get(f"{BASE}/pipeline/runs", headers=h)
        chk("mine 생략 → 422", z.status_code == 422, f"-> {z.status_code}")
finally:
    print("--- 6) 정리")
    if uid is not None:
        _q("DELETE FROM run_records WHERE run_id = %s", (rid,))
        _q("DELETE FROM users WHERE id = %s", (uid,))
    users_after = _q("SELECT count(*) FROM users")[0][0]
    rr_after = _q("SELECT count(*) FROM run_records")[0][0]
    chk("users 전후 동일", users_before == users_after, f"-> {users_before} → {users_after}")
    chk("run_records 전후 동일", rr_before == rr_after, f"-> {rr_before} → {rr_after}")

print(f"\n{ok}/{ok + fail}")
sys.exit(1 if fail else 0)
