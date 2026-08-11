"""B 다인 토론 — 이해관계자 페르소나 N명 (`app/core/stakeholder_mode/`).

화면5 토론 엔진은 **둘**이고 합치지 않았다. 여기는 B 다.
입력은 A 와 공통이다 — **화면4 에서 사람이 고른 후보점(`parcel_id`)**.

🔴 `parcel_id` 하나만 받는 경로를 넣었다(2026-08-11). 이유는 세 가지다.
   ① 감리 근거는 `(domain, run_id, target_facility)` 로 좁혀야 하는데 그 셋의 출처가
      `booth_candidates` 행이다. 요청으로 받으면 후보지와 근거가 어긋날 수 있다.
   ② 조례 검색에는 `facility_type` 필터가 걸려야 한다. 안 걸면 흡연부스 토론에
      전기차충전소 조례가 섞인다(실제로 겪었다).
   ③ A 와 B 가 다른 근거로 토론하면 두 결과를 나란히 놓고 비교할 수 없다.
   조달은 `app/services/candidate_context.build_site_context` **한 곳**에서 한다.

🔴 `gis_data`·`ordinance_data`·`ordinance_contexts` 를 요청에서 **뺐다**(2026-08-11,
   프런트 회신 ⒝). 프런트는 한 번도 안 보내고 있었고, 남겨두면 위 ①②③ 이 전부
   우회 가능한 방어가 된다. `parcel_id` 는 이제 **필수**다.
   ⚠ 뺀 키를 보내면 pydantic 기본값(무시)에 맡기지 않고 **400 으로 말해준다** —
     조용히 버리면 프런트는 "보낸 값이 반영됐다" 로 읽는다(원칙 4).
"""

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.core.stakeholder_mode.graph.dynamic_builder import dynamic_discussion_graph
from app.core.stakeholder_mode.graph.dynamic_state import DynamicDiscussionState
from app.core.stakeholder_mode.schemas.dynamic_stakeholder import StakeholderCandidate
from app.core.stakeholder_mode.services.stakeholder_generator import StakeholderGenerator
from app.db.models.simulation import HearingResultB
from app.db.session import AsyncSessionLocal
from app.services.candidate_context import (
    CandidateNotFound,
    basis_snapshot,
    build_site_context,
)

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


# 요청에서 뺀 키 → 왜 뺐는지. 400 본문에 그대로 실어 보낸다.
_REMOVED_KEYS = {
    "gis_data": "후보점 좌표·지번은 parcel_id 로 조달한다(요청으로 받으면 후보지와 값이 어긋난다)",
    "ordinance_data": "조례·감리 근거는 (domain, run_id, target_facility) 로 좁혀야 하고 그 셋의 출처가 booth_candidates 행이다",
    "ordinance_contexts": "위와 같다. A 와 B 가 다른 근거로 토론하면 두 결과를 비교할 수 없다",
    "audit_data": "화면5 A 에서도 뺐다. 감리 근거는 백엔드가 조달한다",
}


def _reject_removed(req: BaseModel) -> None:
    """`extra="allow"` 로 받아놓고 **뺀 키만 골라 400** 을 낸다.

    `extra="forbid"`(422)를 안 쓰는 이유: 문구가 "Extra inputs are not permitted"
    하나뿐이라 **왜** 못 쓰는지가 안 남는다. 반대로 pydantic 기본값(무시)이면
    프런트는 보낸 값이 반영된 줄 안다 — 둘 다 원칙 4 에 걸린다.
    모르는 키는 통과시킨다. 여기서 막을 것은 「예전에 받던 것」이지 오타가 아니다.
    """
    hits = sorted(k for k in (req.model_extra or {}) if k in _REMOVED_KEYS)
    if hits:
        raise HTTPException(
            status_code=400,
            detail=(
                "요청에서 제거된 키다: "
                + " / ".join(f"`{k}` — {_REMOVED_KEYS[k]}" for k in hits)
                + ". parcel_id 만 주면 백엔드가 같은 값을 조달한다."
            ),
        )


class StakeholderGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    # 🔴 `parcel_id` 는 필수다(2026-08-11). 예전엔 gis_data·ordinance_data 를 직접
    #    채우면 없어도 됐는데, 그 두 칸을 없앴으므로 문맥의 출처가 이것 하나다.
    parcel_id: int
    # 사람이 정하는 두 값. 비면 후보점 문맥에서 만든다(지어내는 게 아니라 조립이다).
    topic: Optional[str] = None
    purpose: Optional[str] = None
    # 발굴 프롬프트가 실제로 읽는 자유 입력칸이다(`DISCOVERY_PROMPT.extra_data`).
    extra_data: Optional[Dict[str, Any]] = None


def _topic_from(ctx: dict) -> str:
    """후보점 문맥에서 토론 주제 문자열을 만든다. 도메인 값을 새로 짓지 않는다 —
    시설명·지역은 전부 `audit_rules`·`booth_candidates` 에서 온 값이다."""
    region = (ctx["audit_meta"].get("region") or "").strip()
    where = f"{region} " if region else ""
    rank = f", {ctx['rank']}위" if ctx.get("rank") else ""
    return f"{where}{ctx['facility_type']} 설치 (후보지 #{ctx['parcel_id']}{rank})"


async def _context_or_400(db: AsyncSession, parcel_id: int) -> dict:
    """`build_site_context` 의 실패를 400/404 로 바꾼다.

    조용히 빈 문맥으로 진행하지 않는다 — 근거 없는 토론이 완주하면 프런트엔
    정상 결과로 보인다(원칙 1·4)."""
    try:
        return await build_site_context(db, parcel_id)
    except CandidateNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:  # audit_rules 미적재 등
        raise HTTPException(status_code=409, detail=str(e))


@router.post(
    "/generate",
    response_model=List[StakeholderCandidate],
    status_code=status.HTTP_200_OK,
)
async def generate_dynamic_stakeholders(
    request: StakeholderGenerationRequest, db: AsyncSession = Depends(get_db)
):
    _reject_removed(request)

    ctx = await _context_or_400(db, request.parcel_id)
    # 사람이 적어 보낸 값이 우선이다. 백엔드는 **빈 자리만** 채운다.
    topic = request.topic or _topic_from(ctx)
    purpose = request.purpose or f"{ctx['facility_type']} 입지 선정 공청회 이해관계자 발굴"
    gis_data = ctx["gis_data"]
    # 🔴 조례 문맥의 출처를 여기 적어둔다(프런트 회신 ⒞). `ordinance_contexts` 는
    #    벡터 검색(`facility_type` 필터)이고 `audit_context` 는 STEP1 `reviewed` 를
    #    적재한 `audit_rules` 를 `(domain, run_id, target_facility)` 로 좁힌 것이다 —
    #    **둘은 다른 것**이라 합치지 않고 키를 갈라서 넘긴다. 화면6 이 "무엇을
    #    근거로 토론했나" 를 적을 때 이 구분이 필요하다.
    ordinance_data = {
        "조례_조문": ctx["ordinance_contexts"],
        "감리_근거": ctx["audit_context"],
        "주변_인프라": ctx["poi_context"],
    }

    try:
        llm = ChatOpenAI(
            api_key=settings.OPENAI_API_KEY, model="gpt-4o-mini", temperature=0.7
        )
        generator = StakeholderGenerator(llm_client=llm)
        candidates = await generator.generate_candidates(
            topic=topic,
            purpose=purpose,
            gis_data=gis_data,
            ordinance_data=ordinance_data,
            extra_data=request.extra_data,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"이해관계자 파이프라인 처리 중 오류가 발생했습니다: {str(e)}",
        )

    # 🔴 `generate_candidates` 는 LLM 응답 파싱에 실패하면 **`[]` 를 돌려준다**
    #    (`stakeholder_generator.py:66` 이 예외를 print 로 삼킨다). 그대로 200 을 주면
    #    "이해관계자가 없다" 로 읽히는데 사실은 "만들지 못했다" 다 — 다른 상태다.
    #    파싱 실패를 고치는 건 B 담당자 몫이라 여기서는 **드러내기만** 한다.
    if not candidates:
        raise HTTPException(
            status_code=502,
            detail=(
                "이해관계자 후보를 만들지 못했다(LLM 응답 파싱 실패 또는 0건). "
                "서버 로그의 'Error parsing final candidates' 를 확인할 것."
            ),
        )
    return candidates


class DynamicDiscussionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    # 🔴 `personas` 만은 **사람이 준다.** B 의 본질이 HITL 이라 백엔드가 조달하지
    #    않는다 — `/generate` 가 낸 후보를 사람이 고르고 고친 결과가 이 배열이다.
    personas: List[Dict[str, Any]]
    parcel_id: int
    topic: Optional[str] = None
    purpose: Optional[str] = None


def _map_persona(idx: int, p: Dict[str, Any]) -> Dict[str, Any]:
    """`/generate` 응답 → 그래프의 `PersonaConfig`.

    🔴 두 벌의 키를 다 받는다. `/generate` 는 `display_name`·`stakeholder_type`·
       `relationship_to_topic` 을 주는데 여기는 `name`·`role`·`description` 만 읽고
       있었다 → **응답을 그대로 넘기면** 예외 없이 통과하고 페르소나가 전부
       `"페르소나 0" / "unknown" / "관계 없음"` 이 됐다. 안 터지고 값만 틀린다.
       프런트가 손으로 이름을 갈아 끼워 우회하게 두면 그 변환이 화면마다 생긴다.
    """
    name = p.get("display_name") or p.get("name")
    role = p.get("stakeholder_type") or p.get("role")
    rel = p.get("relationship_to_topic") or p.get("description")
    if not name or not role:
        # 여기서 기본값을 넣으면 "이름 없는 페르소나"가 조용히 토론에 들어간다.
        raise HTTPException(
            status_code=400,
            detail=(
                f"personas[{idx}] 에 이름/유형이 없다. "
                "`/stakeholders/generate` 응답을 그대로 넘기거나 "
                "(display_name|name)·(stakeholder_type|role) 을 채울 것. "
                f"받은 키: {sorted(p.keys())}"
            ),
        )
    return {
        "persona_id": f"persona_{idx}",
        "display_name": name,
        "stakeholder_type": role,
        "relationship_to_topic": rel or "관계 없음",
        "importance_grade": p.get("importance_grade", "C"),
        "initial_position": "conditional_support",
        "interests": p.get("keywords") or p.get("interests") or [],
    }


# `dynamic_nodes.py` 가 발화 문자열에 직접 붙이는 접두사. 여기와 그쪽이 갈리면
# 발화자를 못 알아본다 — 그때는 이름을 지어내지 않고 `unknown` 으로 나간다.
_MODERATOR_PREFIX = "[사회자 (Supervisor)]: "
_FACTCHECKER_PREFIX = "[팩트체커 (System)]: "

# 노드가 내는 상태 변화 중 **여기서 뜻을 아는 키**. 이 밖의 키는 `raw` 이벤트로
# 그대로 내보낸다 — 그래프에 필드가 늘면 조용히 사라지는 게 아니라 눈에 띄어야 한다.
_HANDLED_KEYS = {
    "supervisor": {"messages", "next_speaker", "rebuttal_target", "rebuttal_count"},
    "persona_speaker": {"messages", "rebuttal_target"},
    "factchecker": {"messages"},
    "evaluator": {"messages", "evaluations", "round_count", "css_levels"},
    "reporter": {"final_scenarios", "is_finished"},
}


def _split_speaker(msg: str, persona_prefixes: Dict[str, Dict[str, Any]]):
    """발화 문자열 하나에서 **발화자와 본문**을 가른다.

    🔴 프런트가 `"): "` 로 자르고 `"[팩트체커"` 로 판별하던 자리다 — 표시 이름에
       `)` 나 `:` 가 들어가면 조용히 잘못 잘린다. 여기서는 **접두사 완전 일치**로만
       가른다: 접두사는 우리가 아는 값(`display_name`·`persona_id`)으로 조립한
       것이라 추측이 아니다.

    맞는 접두사가 없으면 **자르지 않고** `kind: "unknown"` 으로 내보낸다
       (`dynamic_nodes.py` 의 `"System Error: …"` 경로가 그렇다).
       모르는 것을 아는 척 잘라내면 본문 앞부분이 사라진다(원칙 4).
    """
    for prefix, speaker in persona_prefixes.items():
        if msg.startswith(prefix):
            return speaker, msg[len(prefix) :]
    if msg.startswith(_MODERATOR_PREFIX):
        return (
            {"id": "supervisor", "name": "사회자", "kind": "moderator"},
            msg[len(_MODERATOR_PREFIX) :],
        )
    if msg.startswith(_FACTCHECKER_PREFIX):
        return (
            {"id": "factchecker", "name": "팩트체커", "kind": "factchecker"},
            msg[len(_FACTCHECKER_PREFIX) :],
        )
    return {"id": None, "name": None, "kind": "unknown"}, msg


def persona_prefixes(mapped_personas: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """`dynamic_persona_speaker_node` 가 붙이는 접두사 → 발화자.

    형식은 그쪽 코드(`f"{role_title} ({speaker_id}): {content}"`)와 **한 글자도
    달라선 안 된다.** 달라지면 `_split_speaker` 가 못 자르고 `unknown` 으로 나간다 —
    틀린 이름을 붙이는 대신 모른다고 말하는 쪽으로 실패한다.
    """
    return {
        f'{p["display_name"]} ({p["persona_id"]}): ': {
            "id": p["persona_id"],
            "name": p["display_name"],
            "kind": "persona",
        }
        for p in mapped_personas
    }


def normalize_chunk(
    chunk: Dict[str, Any], prefixes: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """LangGraph `{노드: {상태변화}}` → **평평한 이벤트 목록**.

    2026-08-11 프런트 회신 ⑦(b). 예전엔 청크를 그대로 흘려보내 **내부 노드 이름이
    곧 프런트 계약**이었다 — 그래프를 고치면 화면이 깨지고, 발화자는 프런트가
    문자열을 잘라 되찾아야 했다. 이제 `speaker` 는 **필드**다.

    이벤트 종류 — `message` · `evaluation` · `report` · `raw`.
    `node` 는 남긴다(원본을 감추지 않는다). 한 청크가 이벤트 여럿이 될 수 있다.
    """
    events: List[Dict[str, Any]] = []
    for node, delta in (chunk or {}).items():
        delta = delta or {}
        for msg in delta.get("messages") or []:
            speaker, text = _split_speaker(msg, prefixes)
            ev = {"type": "message", "node": node, "speaker": speaker, "text": text}
            # 다음 발화자는 사회자 멘트에 실어 보낸다. 이벤트를 따로 만들면
            # 프런트가 "빈 말풍선" 을 그리게 된다.
            if node == "supervisor" and delta.get("next_speaker"):
                ev["next_speaker"] = delta["next_speaker"]
            events.append(ev)
        if node == "evaluator":
            events.append(
                {
                    "type": "evaluation",
                    "node": node,
                    "round": delta.get("round_count"),
                    "acceptance": delta.get("evaluations") or {},
                    "css_levels": delta.get("css_levels") or {},
                }
            )
        if node == "reporter":
            events.append(
                {
                    "type": "report",
                    "node": node,
                    "final_scenarios": delta.get("final_scenarios") or {},
                    "is_finished": bool(delta.get("is_finished")),
                }
            )
        unknown = sorted(set(delta) - _HANDLED_KEYS.get(node, set()))
        if unknown:
            events.append(
                {
                    "type": "raw",
                    "node": node,
                    "delta": {k: delta[k] for k in unknown},
                }
            )
    return events


async def persist_hearing_b(
    parcel_id: int,
    facility_type: str | None,
    topic: str,
    purpose: str,
    personas: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    basis: Dict[str, Any],
) -> int:
    """B 토론 결과를 `hearing_results_b` 에 남기고 id 를 돌려준다 (2026-08-11, 사람 승인).

    🔴 세션을 **새로 연다.** 요청 스코프의 `Depends(get_db)` 세션은 스트리밍이
       끝나는 시점에 이미 정리됐을 수 있다 — A 의 `run_debate_and_publish` 가
       `AsyncSessionLocal()` 을 직접 여는 것과 같은 이유다.

    저장하는 것은 **정규화한 이벤트 그대로**다. 그래프 내부 상태를 다시 뒤져
    "최종 결과"를 재구성하지 않는다 — 그러면 SSE 로 나간 것과 DB 에 남은 것이
    갈릴 수 있고, 갈려도 안 터진다. 프런트가 본 것이 곧 남는 것이다.
    """
    scenarios: Dict[str, Any] = {}
    evaluations: List[Dict[str, Any]] = []
    messages: List[Dict[str, Any]] = []
    for ev in events:
        if ev.get("type") == "message":
            messages.append(
                {
                    "seq": ev.get("seq"),
                    "node": ev.get("node"),
                    "speaker": ev.get("speaker"),
                    "text": ev.get("text"),
                }
            )
        elif ev.get("type") == "evaluation":
            evaluations.append(
                {
                    "round": ev.get("round"),
                    "acceptance": ev.get("acceptance"),
                    "css_levels": ev.get("css_levels"),
                }
            )
        elif ev.get("type") == "report":
            # 마지막 report 가 최종본이다. 여러 번 오면 뒤엣것이 이긴다.
            scenarios = {
                "final_scenarios": ev.get("final_scenarios") or {},
                "is_finished": bool(ev.get("is_finished")),
            }

    result_json = {
        "engine": "B",
        "scenarios": scenarios,
        "evaluations": evaluations,
        "messages": messages,
        # 이 토론이 실제로 무엇을 근거로 했는지. A(`conflict_simulations.result_json`)와
        # **같은 모양**이다 — 두 엔진 결과를 나란히 놓고 비교하려면 근거도 같은 자리에
        # 같은 형태로 있어야 한다. 조립은 `candidate_context.basis_snapshot` 한 곳.
        "basis": basis,
        # 🔴 어디까지 왔는지를 남긴다. `is_finished` 가 False 인 채로 저장될 수
        #    있다(스트림 중단·예외). 그때 "토론이 끝났다"로 읽히면 안 된다(원칙 4).
        "event_count": len(events),
    }

    async with AsyncSessionLocal() as db:
        row = HearingResultB(
            parcel_id=parcel_id,
            facility_type=facility_type,
            topic=topic,
            purpose=purpose,
            personas=personas,
            result_json=result_json,
            message_count=len(messages),
        )
        db.add(row)
        await db.commit()
        return row.id


@router.post("/dynamic/discuss/stream")
async def stream_dynamic_discussion(
    request: DynamicDiscussionRequest, db: AsyncSession = Depends(get_db)
):
    """다자간 페르소나 실시간 토론 스트리밍.

    🔴 스트림은 **평평한 이벤트**다(2026-08-11, 프런트 회신 ⑦(b)). `normalize_chunk`
       참조 — `{type, node, speaker{id,name,kind}, text}` 이고 끝은 `data: [DONE]`.
       예전엔 LangGraph 청크 원형(`{노드이름: {상태변화}}`)을 그대로 흘려보내
       **내부 노드 이름이 곧 프런트 계약**이었다.

    결과는 `hearing_results_b` 에 남는다(2026-08-11, 사람 승인). `conflict_simulations`
    에 못 넣어서 신설한 테이블이다 — 거기는 `css_score`·`css_vector` 가 NOT NULL 인데
    둘 다 **A 전용 지표**라, 넣으려면 없는 값을 지어내야 한다.
    스트림 끝에 `saved`(또는 `save_failed`) 이벤트가 나가고 `result_url` 로
    `GET /api/v1/simulations/hearings/b/{id}` 를 가리킨다.
    """
    _reject_removed(request)
    if not request.personas:
        raise HTTPException(status_code=400, detail="personas 가 비어 있다")

    ctx = await _context_or_400(db, request.parcel_id)
    topic = request.topic or _topic_from(ctx)
    purpose = request.purpose or f"{ctx['facility_type']} 입지 선정 공청회"

    # 🔴 매핑·검증을 제너레이터 **밖**에서 한다. 안에서 하면 잘못된 요청도
    #    HTTP 200 + SSE `{"error": …}` 로 나가서 프런트가 "토론이 시작됐다" 로 읽는다.
    mapped_personas = [_map_persona(i, p) for i, p in enumerate(request.personas)]
    active_ids = [p["persona_id"] for p in mapped_personas]

    # 🔴 `topic`·`purpose` 를 **읽히는 자리**에 넣는다. `DynamicDiscussionState.topic`
    #    은 선언돼 있지만 동적 그래프의 다섯 노드 중 **아무도 안 읽는다**(실측).
    #    거기만 채우면 프런트가 보낸 안건이 프롬프트에 한 글자도 안 들어간다 —
    #    "인자를 받고 안 쓰는" 그 함정이다. 페르소나 프롬프트가 실제로 보는 건
    #    `site_information`(`COMMON_SYSTEM_PROMPT` 의 `[시설 및 입지 정보]`)이므로
    #    안건을 거기에 **이름 붙여** 싣는다. 입지 값과 섞지 않는다.
    #    ⚠ 제자리는 프롬프트 빌더(`build_multi_party_prompt`)다 — B 담당자 몫이라
    #      여기서는 값이 닿게만 하고 노드는 안 건드렸다.
    site_information = json.dumps(
        {"안건": {"주제": topic, "목적": purpose}, "입지": ctx["gis_data"]},
        ensure_ascii=False,
    )

    initial_state = DynamicDiscussionState(
        project_id="stream_project",
        topic=topic,
        site_information=site_information,
        personas=mapped_personas,
        active_participants=active_ids,
        css_levels={},
        ordinance_contexts=[{"content": c} for c in ctx["ordinance_contexts"]],
        messages=[],
        next_speaker="supervisor",
        round_count=0,
        rebuttal_target="",
        rebuttal_count=0,
        evaluations={},
        final_scenarios={},
        is_finished=False,
    )

    prefixes = persona_prefixes(mapped_personas)

    async def event_generator():
        seq = 0
        collected: List[Dict[str, Any]] = []
        try:
            async for chunk in dynamic_discussion_graph.astream(
                initial_state, stream_mode="updates"
            ):
                for ev in normalize_chunk(chunk, prefixes):
                    ev["seq"] = seq
                    seq += 1
                    collected.append(ev)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("[B 다인토론] 스트림 중 실패")
            # `error` 키는 그대로 둔다(프런트가 이미 본다). `type` 만 덧붙인다.
            err = {"type": "error", "error": str(e), "seq": seq}
            seq += 1
            collected.append(err)
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

        # 🔴 **중단된 토론도 저장한다**(2026-08-11). 5분짜리 LLM 결과를 예외 하나로
        #    통째로 버리면 사람이 다시 돌리는 수밖에 없다. 대신 `is_finished` 가
        #    False 로 남고 `error` 이벤트도 `messages` 밖 `event_count` 에 반영된다 —
        #    "끝난 토론" 인 척하지 않는다(원칙 4).
        if collected:
            try:
                hearing_id = await persist_hearing_b(
                    parcel_id=request.parcel_id,
                    facility_type=ctx.get("facility_type"),
                    topic=topic,
                    purpose=purpose,
                    personas=mapped_personas,
                    events=collected,
                    basis=basis_snapshot(
                        domain=ctx.get("domain"),
                        run_id=ctx.get("run_id"),
                        facility_type=ctx.get("facility_type"),
                        audit_context=ctx.get("audit_context"),
                        poi_context=ctx.get("poi_context"),
                        rag_docs=ctx.get("rag_docs"),
                        audit_meta=ctx.get("audit_meta"),
                    ),
                )
            except Exception as e:
                # 저장 실패를 조용히 넘기면 프런트는 "저장됐다" 로 읽는다(원칙 1).
                logger.exception("[B 다인토론] 결과 저장 실패")
                yield "data: " + json.dumps(
                    {"type": "save_failed", "error": str(e), "seq": seq},
                    ensure_ascii=False,
                ) + "\n\n"
            else:
                yield "data: " + json.dumps(
                    {
                        "type": "saved",
                        "hearing_id": hearing_id,
                        "engine": "B",
                        "result_url": f"/api/v1/simulations/hearings/b/{hearing_id}",
                        "seq": seq,
                    },
                    ensure_ascii=False,
                ) + "\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
