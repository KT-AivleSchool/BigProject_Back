# -*- coding: utf-8 -*-
"""업로드 파일명 NFC 정규화 대조 (in-process · Redis·DB 없이 돈다).

묻는 것은 「NFC 로 저장되나」가 **아니다.** 그건 저장 한 줄만 보면 되고 안 틀린다.
묻는 것은 **「옛 NFD 이름이 남아 손이 안 닿는 자리가 생기나」**다 — 맥에서 올린
파일은 자모가 분리된 NFD 로 오고, 윈도/리눅스는 그걸 **다른 파일명**으로 본다.
그래서 이 대조기는 전부 **NFD 파일을 먼저 디스크에 깔아두고** 시작한다.
NFC 로만 깔고 재면 「이미 정규화된 것을 정규화한다」는 항등식이라 아무것도 안 본다.

🔴 서버를 재시작하지 않는다. `TestClient` 로 프로세스 안에서 띄운다.
🔴 진짜 `DATA_ROOT` 를 안 쓴다 — 임시 폴더로 갈아끼운다(남의 도메인을 안 건드린다).
🔴 Redis 는 인메모리 스텁이다. 이 대조가 묻는 것에 진짜 Redis 가 필요 없고,
   도커가 없다고 못 재면 정작 사고가 난 자리를 아무도 안 재게 된다.
   ⚠ 그래서 **TTL·evict 는 이 대조기가 못 본다** — 그건 `check_upload_api.py` 몫이다.

사용:
  python app\\tools\\check_upload_nfc.py
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unicodedata
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", line_buffering=True
    )

# 🔴 config 를 import 하기 **전에** 심는다. `DATA_ROOT` 는 import 시점에 굳는다.
TMP = Path(tempfile.mkdtemp(prefix="nfc_check_"))
os.environ["OMNISITE_DATA_ROOT"] = str(TMP)

from fastapi.testclient import TestClient  # noqa: E402

import app.config as C  # noqa: E402

C.DATA_ROOT = TMP
C.DOMAIN_ROOT = TMP
C.USER_INPUT_ROOT = TMP / "user_input"

import app.api.v1.upload as U  # noqa: E402

U.DATA_ROOT = TMP
U.USER_INPUT_ROOT = TMP / "user_input"

from app.api.deps import get_redis  # noqa: E402


class FakeRedis:
    """이 대조가 쓰는 네 가지만. 없는 메서드를 부르면 터지게 둔다 —
    조용히 통과하는 스텁은 대조기를 항등식으로 만든다."""

    def __init__(self) -> None:
        self.h: dict[str, dict[str, str]] = {}

    async def hset(self, key, field, value):
        self.h.setdefault(key, {})[field] = value

    async def expire(self, key, sec):
        return True

    async def hgetall(self, key):
        return dict(self.h.get(key, {}))

    async def hdel(self, key, *fields):
        b = self.h.setdefault(key, {})
        return sum(1 for f in fields if b.pop(f, None) is not None)


FAKE = FakeRedis()

from app.main import app  # noqa: E402

app.dependency_overrides[get_redis] = lambda: FAKE
client = TestClient(app)

API = "/api/v1/upload"
DOM = "NFC대조"

NFC_NAME = unicodedata.normalize("NFC", "서울시 역사마스터 정보.csv")
NFD_NAME = unicodedata.normalize("NFD", NFC_NAME)
assert NFC_NAME != NFD_NAME, "NFC/NFD 가 같으면 이 대조는 아무것도 안 본다"

_ok = _ng = 0


def chk(label: str, cond: bool, detail: str = "") -> None:
    global _ok, _ng
    if cond:
        _ok += 1
        print(f"  [OK] {label}")
    else:
        _ng += 1
        print(f"  [!!] {label}  {detail}")


data_dir = TMP / "user_input" / DOM / "data"
law_dir = TMP / "user_input" / DOM / "law"
data_dir.mkdir(parents=True)
law_dir.mkdir(parents=True)

# ─────────────────────────────────────────────────────────────────────
print("\n§1 순수 함수")
chk("_nfc 가 NFD 를 합친다", U._nfc(NFD_NAME) == NFC_NAME)
chk("_safe_name 이 NFC 로 낸다", U._safe_name(NFD_NAME) == NFC_NAME)
chk("_safe_name 이 경로 성분을 뗀다", U._safe_name("../x/" + NFD_NAME) == NFC_NAME)

# 이 변경 전에 올라간 파일 = 디스크에 NFD 로 남아 있다.
(data_dir / NFD_NAME).write_text("a,b\n1,2\n", encoding="utf-8")
on_disk_raw = os.listdir(data_dir)
chk(
    "디스크 원문이 NFD 로 남아 있다",
    bool(on_disk_raw) and on_disk_raw[0] != NFC_NAME,
    f"실제={on_disk_raw}",
)
chk(
    "_disk_match 가 NFC 이름으로 NFD 실물을 찾는다",
    U._disk_match(data_dir, NFC_NAME) is not None,
)
chk("_disk_match 가 없는 이름엔 None", U._disk_match(data_dir, "없는파일.csv") is None)

# ─────────────────────────────────────────────────────────────────────
print("\n§2 목록 — 밖으로 나가는 이름은 NFC")
r = client.get(f"{API}/data", params={"domain": DOM})
chk("200", r.status_code == 200, r.text[:200])
files = r.json()["files"]
chk("한 줄이다", len(files) == 1, f"{len(files)}줄")
chk("filename 이 NFC", bool(files) and files[0]["filename"] == NFC_NAME)
chk("배포 원본으로 잡힌다(원장에 없다)", bool(files) and files[0]["source"] == "preexisting")
chk("deletable=false", bool(files) and files[0]["deletable"] is False)

# ─────────────────────────────────────────────────────────────────────
print("\n§3 같은 파일을 다시 올리면 두 줄이 되나")
body = (data_dir / NFD_NAME).read_bytes()
r = client.post(
    f"{API}/data",
    data={"domain": DOM},
    files={"files": (NFD_NAME, body, "text/csv")},
)
chk("업로드 200", r.status_code == 200, r.text[:300])
j = r.json()
chk("저장 이름이 NFC", j["files"][0]["filename"] == NFC_NAME)
chk("replaced=true (같은 파일로 본다)", j["files"][0]["replaced"] is True)
chk("옛 표기를 걷었다고 말한다", j["files"][0]["old_encoding_removed"] is True)
names = os.listdir(data_dir)
chk("디스크에 파일이 하나뿐", len(names) == 1, f"{names}")
chk("그 하나가 NFC", bool(names) and names[0] == NFC_NAME)

r = client.get(f"{API}/data", params={"domain": DOM})
files = r.json()["files"]
chk("목록도 한 줄", len(files) == 1, f"{len(files)}줄")
chk("이제 내가 올린 것", files[0]["source"] == "upload")
chk("deletable=true", files[0]["deletable"] is True)
chk("dataset_id 가 붙는다", files[0]["dataset_id"] == "01")
redis_fields = list(FAKE.h.get(U._REDIS_KEY.format(domain=DOM), {}))
chk("색인 필드도 하나뿐", len(redis_fields) == 1, f"{redis_fields}")

# ─────────────────────────────────────────────────────────────────────
print("\n§4 원장을 NFD 로 위조해도 삭제가 막히지 않는다")
# 이 변경 전 시점에 적힌 원장 키를 재현한다. 여기서 갈리면 「내가 올린 파일」이
# 배포 원본으로 보여 삭제가 409 로 막힌다 — 사용자가 손쓸 방법이 없어진다.
led = U._ledger_path(DOM)
doc = U._ledger_read(DOM)
doc["data"] = {NFD_NAME: doc["data"][NFC_NAME]}
led.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
chk("_ledger_names 가 NFC 로 준다", U._ledger_names(DOM, "data") == {NFC_NAME})
r = client.get(f"{API}/data", params={"domain": DOM})
chk("여전히 upload 로 보인다", r.json()["files"][0]["source"] == "upload")

enc = urllib.parse.quote(NFC_NAME)
r = client.delete(f"{API}/data/{enc}", params={"domain": DOM})
chk("삭제 200 (409 아님)", r.status_code == 200, r.text[:300])
chk("디스크에서 사라졌다", not list(data_dir.iterdir()), f"{list(data_dir.iterdir())}")
chk(
    "원장에서도 사라졌다",
    U._ledger_names(DOM, "data") == set(),
    f"{U._ledger_names(DOM, 'data')}",
)
chk(
    "색인에서도 사라졌다",
    not FAKE.h.get(U._REDIS_KEY.format(domain=DOM)),
    f"{FAKE.h.get(U._REDIS_KEY.format(domain=DOM))}",
)

# ─────────────────────────────────────────────────────────────────────
print("\n§5 NFD 실물을 NFC 이름으로 지운다 (404 가 아니어야 한다)")
(data_dir / NFD_NAME).write_text("a,b\n1,2\n", encoding="utf-8")
U._ledger_mark(DOM, "data", NFD_NAME, {"uploaded_at": "t", "sha256": "s", "size": 8})
r = client.delete(f"{API}/data/{enc}", params={"domain": DOM})
chk("삭제 200", r.status_code == 200, r.text[:300])
chk("실물이 지워졌다", not list(data_dir.iterdir()))

# ─────────────────────────────────────────────────────────────────────
print("\n§6 조례 — 파일 + 추출 캐시(NFD)까지 같이 지운다")
LAW_NFC = unicodedata.normalize("NFC", "용산구 흡연시설 조례.txt")
LAW_NFD = unicodedata.normalize("NFD", LAW_NFC)
(law_dir / LAW_NFD).write_text("제1조(목적) 이 조례는...", encoding="utf-8")
(law_dir / (LAW_NFD + ".txt")).write_text("제1조(목적) 이 조례는...", encoding="utf-8")
U._ledger_mark(DOM, "law", LAW_NFD, {"uploaded_at": "t", "sha256": "s", "size": 8})

r = client.get(f"{API}/regulations", params={"domain": DOM})
chk("목록 200", r.status_code == 200, r.text[:200])
items = r.json()
chk("filename 이 NFC", bool(items) and items[0]["filename"] == LAW_NFC, f"{items}")
chk("deletable=true", bool(items) and items[0]["deletable"] is True)

r = client.delete(
    f"{API}/regulations/{urllib.parse.quote(LAW_NFC)}", params={"domain": DOM}
)
# 🔴 여기 500 은 **벡터 DB 가 안 떠 있어서**다. 404 가 아니라는 것이 이 항목이 묻는
#    것이다 — 404 면 NFD 실물을 못 찾은 것이고, 500 이면 찾아서 지운 뒤 청크 단계에서
#    막힌 것이다. 둘을 접으면 무엇이 고장인지 못 가른다(원칙 1).
db_down = r.status_code == 500 and "청크 삭제에 실패" in r.text
chk("404 가 아니다 (NFD 실물을 찾았다)", r.status_code != 404, r.text[:200])
chk("삭제 200 또는 벡터DB 부재 500", r.status_code == 200 or db_down, r.text[:200])
if r.status_code == 200:
    chk("추출 캐시도 지웠다고 말한다", r.json()["extract_cache_removed"] is True)
else:
    print("  [--] 추출 캐시 보고는 벡터 DB 가 있어야 본다 (지금은 폴더로 확인)")
left = list(law_dir.iterdir())
chk("law 폴더가 비었다 (원본 + NFD 추출 캐시 둘 다)", not left, f"{left}")

# ─────────────────────────────────────────────────────────────────────
print("\n§7 배제 — 배포 원본은 여전히 409")
(data_dir / "배포원본.csv").write_text("a\n1\n", encoding="utf-8")
r = client.delete(
    f"{API}/data/{urllib.parse.quote('배포원본.csv')}", params={"domain": DOM}
)
chk("409", r.status_code == 409, f"{r.status_code} {r.text[:200]}")
chk("파일이 남아 있다", (data_dir / "배포원본.csv").is_file())

# ─────────────────────────────────────────────────────────────────────
print("\n§8 도메인 목록 — preexisting 개수가 NFD 때문에 부풀지 않는다")
(data_dir / NFD_NAME).write_text("a,b\n1,2\n", encoding="utf-8")
U._ledger_mark(DOM, "data", NFC_NAME, {"uploaded_at": "t", "sha256": "s", "size": 8})
r = client.get(f"{API}/domains", params={"root": "upload"})
chk("200", r.status_code == 200, r.text[:200])
body_j = r.json()
rows = body_j["domains"] if isinstance(body_j, dict) else body_j
row = next((d for d in rows if d["domain"] == DOM), None)
chk("도메인이 보인다", row is not None)
chk(
    "배포 원본은 1개(배포원본.csv)뿐",
    row is not None and row["preexisting_files"] == 1,
    f"{row}",
)

print(f"\n{'=' * 60}\n  {_ok + _ng} 항목 중 {_ok} 통과 · {_ng} 실패\n{'=' * 60}")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _ng else 0)
