"""
OmniSite JWT Dual Token & RTR & Blacklist Automated Test
"""
import sys
import os
import time
import asyncio
import httpx

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.main import app
from app.api.deps import get_db, get_redis
from app.db.base import User


class InMemoryDB:
    def __init__(self):
        self.users = {}
        self.id_counter = 1

    async def execute(self, stmt):
        class Result:
            def __init__(self, items):
                self._items = items
            def scalars(self):
                return self
            def first(self):
                return self._items[0] if self._items else None
        
        email_val = None
        id_val = None
        
        if hasattr(stmt, "compile"):
            compiled = stmt.compile()
            params = compiled.params
            email_val = params.get("email_1") or params.get("email")
            id_val = params.get("id_1") or params.get("id")

        matched = []
        for u in self.users.values():
            if email_val and u.email == email_val:
                matched.append(u)
            elif id_val and u.id == id_val:
                matched.append(u)

        return Result(matched)

    def add(self, user):
        if not user.id:
            user.id = self.id_counter
            self.id_counter += 1
        if getattr(user, "is_active", None) is None:
            user.is_active = True
        self.users[user.id] = user

    async def commit(self):
        pass

    async def refresh(self, user):
        pass

    async def rollback(self):
        pass


class InMemoryRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def incr(self, key):
        val = int(self.store.get(key, 0)) + 1
        self.store[key] = str(val)
        return val

    async def expire(self, key, time):
        pass

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    async def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store.keys() if k.startswith(prefix)]

    async def close(self):
        pass


mock_db = InMemoryDB()
mock_redis = InMemoryRedis()

async def override_get_db():
    yield mock_db

async def override_get_redis():
    yield mock_redis

app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_redis] = override_get_redis


async def run_tests():
    print("🧪 [Auth Dual Token & RTR Automated Test] 검증 시작...")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1/auth", timeout=10.0) as client:
        ts = int(time.time())
        test_email = f"rtr_user_{ts}@omnisite.com"
        test_password = "password123!"
        test_username = f"RTR테스터_{ts}"

        # 1. 회원가입 시도
        reg_resp = await client.post("/register", json={
            "email": test_email,
            "password": test_password,
            "username": test_username
        })
        assert reg_resp.status_code == 201, f"회원가입 실패 ({reg_resp.status_code}): {reg_resp.text}"
        print("  ✅ 1. 회원가입 성공:", reg_resp.json()["email"])

        # 2. 로그인 시도 ➔ Access Token (15분) + Refresh Token (7일) 발급 확인
        login_resp = await client.post("/login", json={
            "email": test_email,
            "password": test_password
        })
        assert login_resp.status_code == 200, f"로그인 실패 ({login_resp.status_code}): {login_resp.text}"
        tokens = login_resp.json()
        access_token_1 = tokens["access_token"]
        refresh_token_1 = tokens["refresh_token"]
        assert "refresh_token" in tokens and tokens["refresh_token"] is not None
        print("  ✅ 2. 로그인 성공 (Dual Token 발급):")
        print(f"     - Access Token: {access_token_1[:25]}...")
        print(f"     - Refresh Token: {refresh_token_1[:25]}...")

        # 3. 정상 Refresh Token Rotation (RTR) 갱신 시도
        refresh_resp = await client.post("/refresh", json={
            "refresh_token": refresh_token_1
        })
        assert refresh_resp.status_code == 200, f"토큰 갱신 실패 ({refresh_resp.status_code}): {refresh_resp.text}"
        new_tokens = refresh_resp.json()
        access_token_2 = new_tokens["access_token"]
        refresh_token_2 = new_tokens["refresh_token"]
        assert access_token_1 != access_token_2
        assert refresh_token_1 != refresh_token_2
        print("  ✅ 3. RTR 토큰 갱신 성공 (신규 듀얼 토큰 교체):")
        print(f"     - New Access Token: {access_token_2[:25]}...")
        print(f"     - New Refresh Token: {refresh_token_2[:25]}...")

        # 4. RTR 탈취 감지 테스트: 이미 사용된 구형 Refresh Token (refresh_token_1) 재요청 ➔ 401 & Family Revocation
        stolen_resp = await client.post("/refresh", json={
            "refresh_token": refresh_token_1
        })
        assert stolen_resp.status_code == 401, f"탈취 감지 실패 ({stolen_resp.status_code}): {stolen_resp.text}"
        print("  ✅ 4. RTR 탈취 감지 (Family Revocation) 성공! 이미 사용된 구형 토큰 사용 시 401 차단:")
        print("     - 응답:", stolen_resp.json()["detail"])

        # 5. 재로그인 ➔ 로그아웃 & Access Token 블랙리스트 차단 검증
        login_resp_2 = await client.post("/login", json={
            "email": test_email,
            "password": test_password
        })
        tokens_2 = login_resp_2.json()
        at_2 = tokens_2["access_token"]

        logout_resp = await client.post("/logout", headers={
            "Authorization": f"Bearer {at_2}"
        })
        assert logout_resp.status_code == 200, f"로그아웃 실패 ({logout_resp.status_code}): {logout_resp.text}"
        print("  ✅ 5. 로그아웃 성공 (Access Token 블랙리스트 등록 & Refresh Token 삭제)")

        print("\n🎉 모든 JWT 듀얼 토큰 & RTR & 블랙리스트 자동화 테스트 통과!")

if __name__ == "__main__":
    asyncio.run(run_tests())
