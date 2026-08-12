r"""게시판 `/api/v1/posts` 대조기 — 22항목 (실 DB · LLM 0회).

사용:  python app\tools\check_posts.py

🔴 묻는 것은 「글이 써지는가」가 아니라 **조용한 실패가 없는가**다.
   ① 모르는 `search_type`·`sort_by`·`order` 가 무시되지 않고 400 인가
      (예전엔 `else` 가 없어 검색 조건이 통째로 빠진 채 **200 + 전체 목록**이 나갔다)
   ② 첨부 삭제가 **commit 뒤**인가 — 행은 남고 파일만 사라지는 방향으로 실패하지 않는가
   ③ 수정에도 작성과 **같은** 공백 검사가 걸리는가
🔴 `posts` 에 실제로 넣고 지운다. 끝에 **전후 행 수를 대조**한다(남의 글은 안 건드린다).
"""
import asyncio
import sys
from pathlib import Path

# `python app\tools\check_posts.py` 로 부르면 `sys.path[0]` 이 `app/tools` 라
# `import app…` 이 안 된다 — 두 단계 위(저장소 루트)를 넣는다.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.api.deps import get_current_user  # noqa: E402
from app.config import BASE_DIR, settings  # noqa: E402
from app.db.base import Post, User  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402

# 🔴 starlette `TestClient` 는 `with` 없이 쓰면 **요청마다 새 이벤트 루프**를 만든다.
#    전역 엔진은 커넥션을 풀에 남기므로 두 번째 요청이 죽은 루프의 소켓을 잡는다
#    (`AttributeError: 'NoneType' object has no attribute 'send'`).
#    `with TestClient(app)` 로 묶으면 lifespan 이 돌아 `reap_orphans`·`prune_runs` 가
#    **남의 run 을 건드린다** → 대신 **NullPool 엔진으로 get_db 를 갈아끼운다**.
_url = settings.DATABASE_URL
if _url.startswith("postgresql://"):
    _url = _url.replace("postgresql://", "postgresql+asyncpg://", 1)
test_engine = create_async_engine(_url, poolclass=NullPool)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)


async def _override_get_db():
    async with TestSession() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


def db_query(coro_fn):
    async def _run():
        async with TestSession() as s:
            return await coro_fn(s)

    return asyncio.run(_run())


ok = fail = 0


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  OK   {name} {extra}")
    else:
        fail += 1
        print(f"  FAIL {name} {extra}")


async def _first_user(s):
    return (await s.execute(select(User).order_by(User.id).limit(1))).scalar_one_or_none()


async def _count_posts(s):
    return (await s.execute(select(func.count(Post.id)))).scalar_one()


def count_posts():
    return db_query(_count_posts)


user = db_query(_first_user)
if user is None:
    raise SystemExit("users 테이블이 비어 있다 — 대조 불가")
print(f"[대조 계정] id={user.id} email={user.email}")

before = count_posts()
print(f"[사전 posts 행수] {before}")

app.dependency_overrides[get_current_user] = lambda: user
app.dependency_overrides[get_db] = _override_get_db
c = TestClient(app)

print("\n§1 모르는 열거값은 400 + 한 문장")
for q, key in [
    ("?search_type=titlee&search_query=x", "search_type"),
    ("?sort_by=date", "sort_by"),
    ("?order=up", "order"),
]:
    r = c.get("/api/v1/posts" + q)
    d = r.json().get("detail")
    chk(f"{key} 오타 → 400", r.status_code == 400, f"({r.status_code})")
    chk(f"{key} detail 이 문자열", isinstance(d, str), f"({type(d).__name__}: {d})")

print("\n§2 빈 문자열은 기본값 (멀쩡하던 호출을 깨지 않는다)")
r = c.get("/api/v1/posts?search_type=&sort_by=&order=")
chk("빈 값 → 200", r.status_code == 200, f"({r.status_code})")
r = c.get("/api/v1/posts?sort_by=author_name&order=ASC")
chk("author_name/대문자 ASC → 200", r.status_code == 200, f"({r.status_code})")

print("\n§3 작성 → 첨부 저장 → 삭제 시 파일도 사라진다")
r = c.post(
    "/api/v1/posts",
    data={"title": "  대조용 글  ", "content": "  본문  "},
    files={"file": ("검증.txt", b"hello", "text/plain")},
)
chk("작성 201", r.status_code == 201, f"({r.status_code} {r.text[:120]})")
pid = r.json()["id"]
chk("title 이 strip 됨", r.json()["title"] == "대조용 글", f"({r.json()['title']!r})")

def path_of(pid):
    async def _q(s):
        return (await s.execute(select(Post.file_path).where(Post.id == pid))).scalar_one_or_none()
    return db_query(_q)

rel = path_of(pid)
full = BASE_DIR / rel
chk("디스크에 파일 존재", full.is_file(), f"({rel})")

print("\n§4 수정 — 공백만 보내면 400 (작성과 같은 규칙)")
r = c.put(f"/api/v1/posts/{pid}", data={"title": "   ", "content": "x"})
chk("빈 제목 → 400", r.status_code == 400, f"({r.status_code})")
r = c.put(f"/api/v1/posts/{pid}", data={"title": "x", "content": "   "})
chk("빈 본문 → 400", r.status_code == 400, f"({r.status_code})")
chk("400 이후에도 원본 제목 유지", c.get(f"/api/v1/posts/{pid}").json()["title"] == "대조용 글")

print("\n§5 첨부 교체 — 옛 파일이 사라지고 새 파일이 생긴다")
r = c.put(
    f"/api/v1/posts/{pid}",
    data={"title": "대조용 글", "content": "본문"},
    files={"file": ("두번째.txt", b"world", "text/plain")},
)
chk("교체 200", r.status_code == 200, f"({r.status_code})")
rel2 = path_of(pid)
chk("경로가 바뀜", rel2 != rel, f"({rel} → {rel2})")
chk("옛 파일 삭제됨", not full.is_file())
chk("새 파일 존재", (BASE_DIR / rel2).is_file())

print("\n§6 삭제 — 행과 파일이 같이 사라진다")
r = c.delete(f"/api/v1/posts/{pid}")
chk("삭제 200", r.status_code == 200, f"({r.status_code})")
chk("행 사라짐", c.get(f"/api/v1/posts/{pid}").status_code == 404)
chk("파일 사라짐", not (BASE_DIR / rel2).is_file())

after = count_posts()
chk("남의 행 개수 전후 동일", before == after, f"({before} → {after})")

print(f"\n{ok}/{ok + fail}")
sys.exit(1 if fail else 0)
