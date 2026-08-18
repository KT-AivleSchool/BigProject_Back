import json
import datetime
import asyncio
import logging
import redis.asyncio as aioredis
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.sim_ai.graph import build_discussion_graph
from app.db.models.simulation import (
    Parcel,
    HearingResultA,
    DebateLog,
)
from app.services.poi_context import build_poi_context
from app.services.candidate_context import (  # noqa: F401
    CandidateNotFound,
    _extract_dynamic_meta_from_audit_rules,
    _fetch_and_parse_audit_rules_from_db,
    basis_snapshot,
    resolve_candidate,
    retrieve_ordinance_texts,
    site_jibun,
)
from app.db.session import AsyncSessionLocal
from app.services import pipeline_runner
from app.utils.redis_pubsub import RedisPubSubManager
from app.core.sim_ai.scenario import scenario_code

logger = logging.getLogger("uvicorn.error")

_SCENARIO_COLUMN = {
    "A": "optimal_scenario",
    "B": "normal_scenario",
    "C": "worst_scenario",
}

INITIAL_CSS_LEVEL = "HIGH"
INITIAL_CSS_SOURCE = "deterministic_default"

_scenario_code = scenario_code


def _scenario_text(scenario: dict) -> str:
    """시나리오 객체를 사람이 읽는 한 덩어리로."""
    code = _scenario_code(scenario) or "?"
    lines = [f"[{code}] {scenario.get('scenario_description') or '설명 없음'}"]
    acc = scenario.get("final_acceptance_score")
    cri = scenario.get("conflict_risk_index")
    if acc is not None or cri is not None:
        lines.append(f"수용도 {acc} · 갈등위험지수 {cri}")
    if scenario.get("reason"):
        lines.append(f"사유: {scenario['reason']}")
    if scenario.get("risk_reason"):
        lines.append(f"위험 사유: {scenario['risk_reason']}")
    if scenario.get("summary"):
        lines.append(str(scenario["summary"]))
    return "\n".join(lines)


async def _persist_simulation(
    db: AsyncSession,
    parcel_id: int,
    facility_type: str,
    result_json: dict,
    css_score: float,
    scenarios: list[dict],
    debate_logs: list[dict],
) -> int:
    land_id = await db.scalar(select(Parcel.land_id).where(Parcel.id == parcel_id))

    sim = HearingResultA(
        parcel_id=parcel_id,
        candidate_land_id=land_id,
        facility_type=facility_type,
        result_json=result_json,
        css_score=css_score,
        css_vector=result_json.get("ahp_weights") or {},
    )

    for sc in scenarios or []:
        code = _scenario_code(sc)
        if code is None:
            logger.warning(
                "[simulations] 시나리오 코드(A/B/C)를 못 읽었다 — 개별 컬럼은 비운다: %r",
                sc.get("scenario"),
            )
            continue
        setattr(sim, _SCENARIO_COLUMN[code], _scenario_text(sc))

    db.add(sim)
    await db.flush()

    for i, log in enumerate(debate_logs or []):
        db.add(
            DebateLog(
                simulation_id=sim.id,
                turn_index=i,
                sender=str(log.get("sender") or "참여자")[:50],
                message=str(log.get("text") or ""),
            )
        )

    await db.commit()
    return sim.id


async def run_debate_and_publish(
    parcel_id: int,
    facility_type: str,
    redis: aioredis.Redis,
):
    pubsub_manager = RedisPubSubManager(redis)
    async with AsyncSessionLocal() as db:
        try:
            resolved = await resolve_candidate(db, parcel_id)
            parcel = resolved["parcel"]
            domain = resolved["domain"]
            run_id = resolved["run_id"]

            audit_context = await _fetch_and_parse_audit_rules_from_db(
                db, facility_type, domain, run_id
            )
            audit_meta = await _extract_dynamic_meta_from_audit_rules(
                db, facility_type, domain, run_id
            )

            gis_data = {
                "lat": resolved["lat"],
                "lng": resolved["lng"],
                "jibun": site_jibun(parcel, audit_meta.get("region")),
                "intensity_level": "보통",
                "ahp_weights": audit_meta.get("ahp_weights", {}),
            }

            poi_context = (await build_poi_context(resolved))["text"]
            audit_context_no_poi = audit_context

            if poi_context:
                audit_context += f"\n\n## 📍 주변 인프라 요인 (DB 연산)\n{poi_context}"

            await pubsub_manager.publish_debate_message(
                parcel_id,
                "시스템",
                f"선택된 위치(지번: {gis_data['jibun']})의 {facility_type} 모의 심의를 시작합니다...",
                is_finished=False,
            )

            graph = build_discussion_graph()

            common_rag, rag_docs_list = await retrieve_ordinance_texts(
                facility_type, terms=audit_meta.get("exclusion_targets")
            )

            basis = basis_snapshot(
                domain=domain,
                run_id=run_id,
                facility_type=facility_type,
                audit_context=audit_context_no_poi,
                poi_context=poi_context,
                rag_docs=rag_docs_list,
                audit_meta=audit_meta,
            )

            timestamp = datetime.datetime.now().isoformat()

            initial_state = {
                "parcel_id": str(parcel_id),
                "facility_type": facility_type,
                "domain": domain,
                "run_id": run_id,
                "land_id": parcel.land_id,
                "lat": gis_data["lat"],
                "lng": gis_data["lng"],
                "jibun": gis_data["jibun"],
                "intensity_level": gis_data["intensity_level"],
                "ahp_weights": gis_data["ahp_weights"],
                "common_rag": common_rag,
                "audit_context": audit_context,
                "audit_context_no_poi": audit_context_no_poi,
                "poi_context": poi_context,
                "rag_docs_list": rag_docs_list,
                "basis": basis,
                "css_score": 0.0,
                "css_level": INITIAL_CSS_LEVEL,
                "css_source": INITIAL_CSS_SOURCE,
                "messages": [],
                "debate_logs": [],
                "turn_count": 0,
                "max_turns": 3,
                "is_finished": False,
                "timestamp": timestamp,
            }

            final_state = await graph.ainvoke(initial_state)

            css_score = float(final_state.get("css_score") or 0.0)
            scenarios = final_state.get("scenarios") or []
            debate_logs = final_state.get("debate_logs") or []
            conflict_factors = final_state.get("conflict_factors") or []

            result_json = {
                "candidate_lat": gis_data["lat"],
                "candidate_lng": gis_data["lng"],
                "candidate_jibun": gis_data["jibun"],
                "facility_type": facility_type,
                "conflict_sensitivity_score": css_score,
                "css_level": final_state.get("css_level"),
                "ahp_weights": gis_data["ahp_weights"],
                "scenarios": scenarios,
                "debate_logs": debate_logs,
                "conflict_factors": conflict_factors,
                "basis": basis,
                "determinism": {
                    "initial_css_level": INITIAL_CSS_LEVEL,
                    "initial_css_source": INITIAL_CSS_SOURCE,
                    "intensity_level": "보통 (고정)",
                },
                "timestamp": timestamp,
            }

            sim_id = await _persist_simulation(
                db=db,
                parcel_id=parcel_id,
                facility_type=facility_type,
                result_json=result_json,
                css_score=css_score,
                scenarios=scenarios,
                debate_logs=debate_logs,
            )

            await pubsub_manager.publish_debate_message(
                parcel_id,
                "시스템",
                f"모의 심의 토론이 종결되었습니다. (생성 ID: {sim_id})",
                is_finished=True,
            )

        except Exception as e:
            err_msg = str(e)
            is_quota = "quota" in err_msg.lower() or "429" in err_msg
            if isinstance(e, CandidateNotFound):
                error_code = "CANDIDATE_NOT_FOUND"
                message = f"선택한 후보점을 찾을 수 없습니다 (parcel_id={parcel_id})."
            elif is_quota:
                error_code = "OPENAI_QUOTA_EXCEEDED"
                message = "OpenAI API Quota가 초과되었습니다. API 키 잔액을 충전하고 다시 시도해 주세요."
            else:
                error_code = "AI_ENGINE_ERROR"
                message = f"AI 토론 엔진 오류가 발생했습니다: {err_msg}"
            logger.error("[Stream Error] %s: %s", error_code, err_msg, exc_info=True)

            await redis.publish(
                f"debate:{parcel_id}",
                json.dumps(
                    {
                        "error_code": error_code,
                        "message": message,
                        "is_finished": True,
                    },
                    ensure_ascii=False,
                ),
            )


async def _missing_run_detail(run_id: str, domain: str | None) -> dict:
    rec = await asyncio.to_thread(pipeline_runner.loaded_record, run_id)
    loaded = rec["loaded"] if isinstance(rec.get("loaded"), dict) else None
    where = f"run_id={run_id!r}" + (f" domain={domain!r}" if domain else "")

    if rec["state"] == "unknown_run":
        code = "UNKNOWN_RUN"
        msg = f"{where} 의 후보점이 없다. runs/{run_id} 폴더 자체가 없다 — run_id 가 틀렸거나 이 서버에서 실행한 run 이 아니다."
    elif rec["state"] == "status_unreadable":
        code = "STATUS_UNREADABLE"
        msg = f"{where} 의 후보점이 없다. status.json 을 읽지 못함 ({rec['reason']})."
    elif loaded and (loaded.get("booth_candidates") or 0) > 0:
        code = "LOADED_BUT_MISSING"
        msg = f"{where} 는 {loaded['booth_candidates']}행을 적재했다고 기록돼 있으나 지금 DB 에 없다."
    else:
        code = "NEVER_LOADED"
        msg = f"{where} 의 후보점이 없고, 적재 기록도 없다."

    detail = {
        "code": code,
        "message": msg,
        "run_id": run_id,
        "domain": domain,
        "loaded": rec["loaded"],
        "current": {"booth_candidates": 0},
    }

    if domain is not None:
        n_all = await _count_parcels(run_id)
        if n_all > 0:
            detail["current"]["booth_candidates_any_domain"] = n_all
            detail["code"] = "DOMAIN_MISMATCH"
            detail["message"] = f"run_id={run_id!r} 에는 후보점 {n_all}행이 있으나 domain={domain!r} 인 것은 없다."
    return detail


async def _count_parcels(run_id: str) -> int:
    async with AsyncSessionLocal() as s:
        return await s.scalar(
            select(func.count(Parcel.id)).where(Parcel.run_id == run_id)
        ) or 0
