# -*- coding: utf-8 -*-
"""B 다인 토론(`/stakeholders/*`) 어댑터 대조 (in-process TestClient).

    python app\\tools\\check_stakeholders.py

🔴 **LLM 채팅을 부르지 않는다.** 토론 스트림은 **거절 경로만** 친다 — 정상 경로를
   치면 5분짜리 다인 토론이 돈다. (조례 검색 임베딩은 부른다.)
"""
import asyncio
import sys
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.api.v1.stakeholders import (  # noqa: E402
    _map_persona,
    _topic_from,
    normalize_chunk,
    persist_hearing_b,
    persona_prefixes,
)
from app.db.models.simulation import HearingResultB  # noqa: E402
from app.db.session import AsyncSessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.candidate_context import (  # noqa: E402
    basis_snapshot,
    build_site_context,
    site_jibun,
)

ok = fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label} {extra}")
    else:
        fail += 1
        print(f"  [!!] {label} {extra}")


print("--- 1) 페르소나 매핑 (generate 응답 형태를 그대로)")
gen_shape = {
    "display_name": "이태원 상인회 대표",
    "stakeholder_type": "지역상인",
    "relationship_to_topic": "점포 앞 흡연부스 설치 영향",
    "importance_grade": "A",
    "keywords": ["매출", "보행"],
}
m = _map_persona(0, gen_shape)
chk("display_name 보존", m["display_name"] == "이태원 상인회 대표", m["display_name"])
chk("stakeholder_type 보존", m["stakeholder_type"] == "지역상인", m["stakeholder_type"])
chk("relationship 보존", m["relationship_to_topic"].startswith("점포 앞"))
chk("interests = keywords", m["interests"] == ["매출", "보행"])

chk("등급 없으면 '미상' — C 로 채우지 않는다", _map_persona(0, {
    "display_name": "ㄱ", "stakeholder_type": "ㄴ"})["importance_grade"] == "미상")

# 🔴 옛 키(name/role/description)는 **2026-08-11 에 제거**했다(프런트 배포 652aa99 확인 후).
#    여기서 「통과한다」를 확인하던 자리다 — 별칭을 뺐으면 대조기도 같이 뒤집는다.
#    안 뒤집으면 대조기가 없어진 동작을 계속 요구해 회귀로 잡힌다.
old_shape = {"name": "주민대표", "role": "주민", "description": "인근 거주"}
try:
    _map_persona(1, old_shape)
    chk("옛 키(name/role)는 이제 거절", False, "예외가 안 났다")
except Exception as e:
    chk("옛 키(name/role)는 이제 거절", "400" in repr(e), type(e).__name__)

try:
    _map_persona(2, {"foo": 1})
    chk("이름 없는 페르소나 거절", False, "예외가 안 났다")
except Exception as e:
    chk("이름 없는 페르소나 거절", "400" in repr(e) or "이름" in str(e), type(e).__name__)

print("--- 1-5) site_jibun (DB 안 씀. 지어내지 않는 규칙만 본다)")


class _P:
    def __init__(self, jibun, pnu, id=7):
        self.jibun, self.pnu, self.id = jibun, pnu, id


chk("지번+지역 결합", site_jibun(_P("422 대", "111"), "서울특별시 용산구")
    == "서울특별시 용산구 422 대")
chk("지역 없으면 원문만", site_jibun(_P("422 대", "111"), None) == "422 대")
chk("지목을 안 떼어낸다", site_jibun(_P("228-1 도", None), None) == "228-1 도")
chk("지번 없으면 PNU 로 남긴다", site_jibun(_P("", "1117012500104220000"), "용산구")
    == "지번 미상 (PNU 1117012500104220000)", site_jibun(_P("", "1117012500104220000"), "용산구"))
chk("둘 다 없으면 후보 번호", site_jibun(_P(None, None), None) == "지번 미상 (후보지 #7)")

print("--- 1-7) normalize_chunk — 평평한 이벤트 (DB·LLM 안 씀)")
# 이름에 `)` 와 `:` 를 일부러 넣는다. 프런트가 쓰던 `"): "` 분리라면 여기서 잘린다.
tricky = _map_persona(3, {"display_name": "주민 (A동): 대표", "stakeholder_type": "주민"})
PRE = persona_prefixes([m, tricky])

e = normalize_chunk(
    {"supervisor": {"next_speaker": "persona_0", "messages": ["[사회자 (Supervisor)]: 시작합니다."]}},
    PRE,
)
chk("사회자 1건", len(e) == 1 and e[0]["type"] == "message", str(len(e)))
chk("사회자 speaker", e[0]["speaker"] == {"id": "supervisor", "name": "사회자", "kind": "moderator"})
chk("사회자 접두사 제거", e[0]["text"] == "시작합니다.", e[0]["text"])
chk("next_speaker 동봉", e[0].get("next_speaker") == "persona_0")

e = normalize_chunk(
    {"persona_speaker": {"messages": ["이태원 상인회 대표 (persona_0): 반대합니다."],
                         "rebuttal_target": "persona_3"}},
    PRE,
)
chk("페르소나 id/이름", e[0]["speaker"]["id"] == "persona_0"
    and e[0]["speaker"]["name"] == "이태원 상인회 대표", str(e[0]["speaker"]))
chk("페르소나 본문", e[0]["text"] == "반대합니다.", e[0]["text"])

e = normalize_chunk({"persona_speaker": {"messages": ["주민 (A동): 대표 (persona_3): 동의합니다."]}}, PRE)
chk("이름에 ')' ':' 가 있어도 정확", e[0]["speaker"]["name"] == "주민 (A동): 대표"
    and e[0]["text"] == "동의합니다.", f"{e[0]['speaker']['name']} / {e[0]['text']}")

e = normalize_chunk({"factchecker": {"messages": ["[팩트체커 (System)]: 근거가 없다."]}}, PRE)
chk("팩트체커 kind", e[0]["speaker"]["kind"] == "factchecker" and e[0]["text"] == "근거가 없다.")

e = normalize_chunk({"persona_speaker": {"messages": ["System Error: persona_9 없음"]}}, PRE)
chk("모르는 접두사는 안 자른다", e[0]["speaker"]["kind"] == "unknown"
    and e[0]["text"] == "System Error: persona_9 없음", e[0]["text"])

e = normalize_chunk(
    {"evaluator": {"evaluations": {"persona_0_acceptance": 0.4}, "round_count": 1,
                   "css_levels": {"persona_0": "MEDIUM"},
                   "messages": ["[사회자 (Supervisor)]: 제1라운드 평가 완료."]}},
    PRE,
)
chk("평가는 message + evaluation 2건", [x["type"] for x in e] == ["message", "evaluation"],
    str([x["type"] for x in e]))
chk("평가 값", e[1]["round"] == 1 and e[1]["acceptance"]["persona_0_acceptance"] == 0.4
    and e[1]["css_levels"]["persona_0"] == "MEDIUM")

e = normalize_chunk({"reporter": {"final_scenarios": {"scenario_type": "Scenario A"}, "is_finished": True}}, PRE)
chk("리포트 이벤트", e[0]["type"] == "report" and e[0]["is_finished"]
    and e[0]["final_scenarios"]["scenario_type"] == "Scenario A")

e = normalize_chunk({"새노드": {"뭔가": 1}}, PRE)
chk("모르는 노드는 raw 로 드러난다", e[0]["type"] == "raw" and e[0]["node"] == "새노드"
    and e[0]["delta"] == {"뭔가": 1}, str(e))

e = normalize_chunk({"reporter": {"final_scenarios": {}, "is_finished": True, "새필드": 9}}, PRE)
chk("모르는 키도 raw 로", [x["type"] for x in e] == ["report", "raw"]
    and e[1]["delta"] == {"새필드": 9}, str([x["type"] for x in e]))

print("--- 2) build_site_context (parcel_id 하나로 조달)")


async def _ctx():
    """🔴 끝나기 전에 `engine.dispose()` 를 한다. `asyncio.run` 이 루프를 닫는데
       커넥션 풀은 모듈 전역이라, 남겨두면 **다음 TestClient 루프**에서
       `Event loop is closed` 로 터진다 — 코드가 아니라 드라이버 문제다."""
    try:
        async with AsyncSessionLocal() as db:
            return await build_site_context(db, 2)
    finally:
        await engine.dispose()


ctx = asyncio.run(_ctx())
chk("domain/run_id 조달", ctx["domain"] == "흡연" and ctx["run_id"] == "정본",
    f"{ctx['domain']}/{ctx['run_id']}")
chk("facility_type 조달", ctx["facility_type"] == "흡연부스", ctx["facility_type"])
chk("좌표 실측값", isinstance(ctx["gis_data"]["lat"], float) and 33 < ctx["gis_data"]["lat"] < 39,
    str(ctx["gis_data"]["lat"]))
chk("jibun 이 실제 지번", ctx["gis_data"]["jibun"].startswith(ctx["audit_meta"]["region"])
    and "실제 위치 기반" not in ctx["gis_data"]["jibun"], ctx["gis_data"]["jibun"])
chk("ahp_weights 8개", len(ctx["gis_data"]["ahp_weights"]) == 8,
    str(len(ctx["gis_data"]["ahp_weights"])))
chk("감리 근거 있음", "절대 배제" in ctx["audit_context"])
chk("조례 조문 5건", len(ctx["ordinance_contexts"]) == 5, str(len(ctx["ordinance_contexts"])))
chk("조문에 DOC_ID", all(c.startswith("[DOC_ID:") for c in ctx["ordinance_contexts"]))
print(f"      topic = {_topic_from(ctx)}")
chk("topic 에 시설·후보 번호", "흡연부스" in _topic_from(ctx) and "#2" in _topic_from(ctx))

print("--- 2-5) persist_hearing_b — 🔴 실제로 넣는다 (2026-08-11 신설, 사람 승인)")
# SSE 로 나간 **정규화 이벤트 그대로**를 넣는다. 그래프 내부를 다시 뒤지지 않는 게
# 이 함수의 요점이라, 대조도 같은 모양으로 한다.
FAKE_EVENTS = [
    {"type": "message", "seq": 0, "node": "p1", "speaker": {"id": "p1", "name": "주민",
     "kind": "persona"}, "text": "가"},
    {"type": "message", "seq": 1, "node": "p2", "speaker": {"id": "p2", "name": "상인",
     "kind": "persona"}, "text": "나"},
    {"type": "evaluation", "seq": 2, "round": 1, "acceptance": 0.42,
     "css_levels": {"p1": "HIGH"}},
    {"type": "raw", "seq": 3, "delta": {"무시": 1}},
    {"type": "report", "seq": 4, "final_scenarios": {"p1": "조건부"}, "is_finished": True},
]
# 정본 어휘로 적는다 — 실제로 저장되는 건 `_map_persona` 를 거친 dict 다.
FAKE_PERSONAS = [
    {"display_name": "주민", "stakeholder_type": "인근 거주자"},
    {"display_name": "상인", "stakeholder_type": "상가"},
]


# 🔴 `basis` 는 **필수 인자**다. 기본값을 두면 빠뜨렸을 때 근거 없는 결과가 조용히
#    저장된다 — 결론만 남고 무엇을 보고 판단했는지는 사라진다(원칙 4).
FAKE_BASIS = basis_snapshot(
    domain="흡연", run_id="정본", facility_type="흡연부스",
    audit_context="절대 배제: 어린이집", poi_context="주변 버스정류소 2개소",
    rag_docs=[{"doc_id": "d1", "text": "제5조 …"}],
    audit_meta={"exclusion_targets": ["어린이집"], "ahp_weights": {"수요": 0.5}},
)


async def _persist():
    try:
        return await persist_hearing_b(
            parcel_id=2, facility_type="흡연부스",
            topic="대조기 임시행", purpose="검증", personas=FAKE_PERSONAS,
            events=FAKE_EVENTS, basis=FAKE_BASIS,
        )
    finally:
        await engine.dispose()


hb_id = asyncio.run(_persist())
chk("hearing_id 발번", isinstance(hb_id, int) and hb_id > 0, f"id={hb_id}")

with TestClient(app) as c:
    print("--- 2-6) 넣은 행이 조회 경로로 그대로 읽힌다")
    rb = c.get(f"/api/v1/simulations/hearings/b/{hb_id}")
    chk("단건 200", rb.status_code == 200, f"-> {rb.status_code} {rb.text[:120]}")
    if rb.status_code == 200:
        d = rb.json()
        chk("engine B", d["engine"] == "B")
        # 🔴 저장 안 한 값이다. `booth_candidates` 조인으로만 나온다 — 복사해두면 어긋난다.
        chk("run_id 는 조인으로", d["run_id"] == "정본" and d["domain"] == "흡연",
            f"{d['domain']}/{d['run_id']}")
        chk("발화만 센다 (raw·evaluation 제외)", d["message_count"] == 2,
            str(d["message_count"]))
        chk("report 접힘", d["result_json"]["scenarios"]["is_finished"] is True)
        chk("evaluation 접힘", len(d["result_json"]["evaluations"]) == 1)
        chk("이벤트 총수 보존", d["result_json"]["event_count"] == 5,
            str(d["result_json"]["event_count"]))
        # 근거 스냅샷. 「나중에 조회하면 나온다」에 안 기댄다 — audit_rules 는
        # 같은 (domain, run_id) 로 교체되는데 이 토론과는 FK 가 없다.
        b = d["result_json"].get("basis") or {}
        chk("근거 스냅샷 있음", bool(b), str(list(b)))
        chk("감리 원문 보존", b.get("audit_context") == "절대 배제: 어린이집")
        # POI 는 감리와 **따로** 남는다. 이어붙이면 누가 말한 근거인지 구분이 사라진다.
        chk("POI 가 감리와 분리", b.get("poi_context") == "주변 버스정류소 2개소")
        chk("조례는 doc_id 가 아니라 본문", (b.get("ordinances") or [{}])[0].get("text")
            == "제5조 …")
        chk("근거에 domain/run_id", b.get("domain") == "흡연" and b.get("run_id") == "정본")
    rl = c.get("/api/v1/simulations/hearings", params={"run_id": "정본", "engine": "B"})
    chk("목록에 뜬다", rl.status_code == 200
        and any(h["hearing_id"] == hb_id for h in rl.json()["hearings"]),
        f"-> {rl.status_code} n={len(rl.json().get('hearings', []))}")

with TestClient(app) as c:
    print("--- 3) 거절 경로 (LLM 미호출)")
    r = c.post("/api/v1/stakeholders/generate", json={})
    chk("generate parcel_id 필수 422", r.status_code == 422, f"-> {r.status_code}")
    r = c.post(
        "/api/v1/stakeholders/dynamic/discuss/stream", json={"personas": [gen_shape]}
    )
    chk("stream parcel_id 필수 422", r.status_code == 422, f"-> {r.status_code}")
    r = c.post(
        "/api/v1/stakeholders/dynamic/discuss/stream",
        json={"personas": [], "parcel_id": 2},
    )
    chk("personas 빈배열 400", r.status_code == 400, f"-> {r.status_code}")

    # 🔴 뺀 키를 조용히 무시하지 않는다 — 무시하면 프런트는 반영된 줄 안다.
    r = c.post(
        "/api/v1/stakeholders/generate",
        json={"parcel_id": 2, "gis_data": {"lat": 0.0}},
    )
    chk("generate gis_data 400", r.status_code == 400, f"-> {r.status_code}")
    chk("400 본문이 이유를 말한다", "gis_data" in r.text and "parcel_id" in r.text,
        r.json().get("detail", "")[:60] if r.status_code == 400 else "")
    r = c.post(
        "/api/v1/stakeholders/dynamic/discuss/stream",
        json={"personas": [gen_shape], "parcel_id": 2, "ordinance_contexts": ["x"]},
    )
    chk("stream ordinance_contexts 400", r.status_code == 400, f"-> {r.status_code}")
    r = c.post(
        "/api/v1/stakeholders/dynamic/discuss/stream",
        # 🔴 `parcel_id` 를 없는 값으로 준다. 유효한 값을 주면 **5분짜리 토론이 돈다**.
        json={"personas": [gen_shape], "parcel_id": 999999, "오타키": 1},
    )
    chk("모르는 키는 안 막는다(400 아님)", r.status_code == 404, f"-> {r.status_code}")
    r = c.post(
        "/api/v1/stakeholders/dynamic/discuss/stream",
        json={"personas": [{"foo": 1}], "parcel_id": 2},
    )
    chk("이름 없는 페르소나 400", r.status_code == 400, f"-> {r.status_code}")
    r = c.post(
        "/api/v1/stakeholders/dynamic/discuss/stream",
        json={"personas": [gen_shape], "parcel_id": 999999},
    )
    chk("없는 후보 404", r.status_code == 404, f"-> {r.status_code}")
    r = c.post("/api/v1/stakeholders/generate", json={"parcel_id": 999999})
    chk("generate 없는 후보 404", r.status_code == 404, f"-> {r.status_code}")

print("--- 4) 임시행 정리")


async def _cleanup(pk):
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(HearingResultB).where(HearingResultB.id == pk))
            await db.commit()
            left = (
                await db.execute(select(HearingResultB).where(HearingResultB.id == pk))
            ).scalars().first()
            return left is None
    finally:
        await engine.dispose()


chk("임시행 지워짐", asyncio.run(_cleanup(hb_id)), f"id={hb_id}")

print(f"\n{ok + fail}항목 중 {ok} 통과 · {fail} 실패")
sys.exit(1 if fail else 0)
