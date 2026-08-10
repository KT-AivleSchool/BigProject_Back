import json
from typing import Dict, Any, List

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.core.stakeholder_mode.graph.dynamic_state import DynamicDiscussionState
from app.core.stakeholder_mode.schemas.persona import PersonaConfig
from app.core.stakeholder_mode.prompts.renderer import render_persona_system_prompt
from app.core.sim_ai.multi_party_discussion_prompts import build_multi_party_prompt, REPORTER_PROMPT
from app.config import settings

def get_llm_smart(temperature: float = 0.7) -> ChatOpenAI:
    return ChatOpenAI(api_key=settings.OPENAI_API_KEY, model="gpt-4o", temperature=temperature)

def get_llm_fast(temperature: float = 0.7) -> ChatOpenAI:
    return ChatOpenAI(api_key=settings.OPENAI_API_KEY, model="gpt-4o-mini", temperature=temperature)

def _format_history(messages: List[str]) -> str:
    return "\n".join(messages)

async def dynamic_supervisor_node(state: DynamicDiscussionState) -> dict:
    """참여 중인 동적 페르소나들 중 대화 맥락상 다음 발화자를 선택하고 진행 멘트를 출력"""
    from pydantic import BaseModel, Field
    import re
    
    class SupervisorDecision(BaseModel):
        reasoning: str = Field(description="상황에 맞는 진행자의 자연스러운 진행 멘트 (1~2문장. 예: 'OOO님의 의견에 대해 다른 분들의 생각은 어떠신가요?', '충분한 논의가 진행된 것 같습니다. 평가를 시작하겠습니다.')")
        next_speaker: str = Field(description="선택된 다음 발언자의 persona_id (또는 'evaluator')")

    active_participants = state.get("active_participants", [])
    rebuttal_target = state.get("rebuttal_target", "")
    rebuttal_count = state.get("rebuttal_count", 0)
    messages = state.get("messages", [])
    round_count = state.get("round_count", 0)
    
    # 페르소나 ID와 표시 이름 매핑 생성
    persona_map = {}
    for p_dict in state.get("personas", []):
        persona_map[p_dict["persona_id"]] = p_dict["display_name"]
    
    # [핑퐁 로직] 만약 이전 발언자가 누군가를 지목했고, 연속 핑퐁이 2회 미만이라면 즉시 발언권 부여
    if rebuttal_target and rebuttal_target in active_participants and rebuttal_count < 2:
        display_name = persona_map.get(rebuttal_target, rebuttal_target)
        return {
            "next_speaker": rebuttal_target,
            "rebuttal_count": rebuttal_count + 1,
            "messages": [f"[사회자 (Supervisor)]: 방금 제기된 의견에 대해 {display_name}님의 답변을 직접 들어보겠습니다."]
        }

    # [라운드별 발언 미참여 자 강제 지목]
    # 마지막 평가 멘트 이후에 발언한 페르소나 목록 수집
    last_eval_idx = -1
    for idx, msg in enumerate(messages):
        if "[사회자 (Supervisor)]:" in msg and "평가" in msg:
            last_eval_idx = idx
            
    recent_messages = messages[last_eval_idx + 1:] if last_eval_idx != -1 else messages
    
    speakers_in_this_round = set()
    for msg in recent_messages:
        if msg.startswith("[팩트체커") or msg.startswith("[사회자"):
            continue
        match = re.search(r'\((persona_\d+)\):', msg)
        if match:
            speakers_in_this_round.add(match.group(1))

    # 해당 라운드에서 아직 한 번도 발언하지 않은 페르소나가 있다면 강제 순차 지목
    if len(speakers_in_this_round) < len(active_participants):
        for pid in active_participants:
            if pid not in speakers_in_this_round:
                display_name = persona_map.get(pid, pid)
                round_name = "1라운드 모두발언" if round_count == 0 else f"{round_count + 1}라운드 본격 토론"
                return {
                    "next_speaker": pid,
                    "rebuttal_target": "",
                    "rebuttal_count": 0,
                    "messages": [f"[사회자 (Supervisor)]: {round_name} 단계입니다. 다음으로 {display_name}님의 의견을 들어보겠습니다."]
                }

    # 모든 참가자가 해당 라운드에서 발언을 마친 경우 LLM 기반 진행자 결정
    history_text = _format_history(messages)
    participant_desc = ", ".join([f"{pid} ({persona_map.get(pid, 'Unknown')})" for pid in active_participants])
    
    system_msg = f"""당신은 다자간 심의위원회의 사회자(Supervisor)입니다.
현재 참여 중인 페르소나 리스트: {participant_desc}

모든 페르소나가 이번 라운드 기본 발언을 마쳤습니다.
이전 대화 내용을 검토한 후, 추가 토론을 진행할 페르소나를 선택하거나, 이번 라운드를 마감하고 수용도 평가 단계(evaluator)로 넘어갈지 선택하세요.

선택 가능한 키워드(next_speaker):
{", ".join(active_participants)}
- evaluator (평가자 - 이번 라운드 논의를 마무리하고 평가를 수행할 경우 반드시 선택)

진행 멘트(reasoning) 작성 규칙:
1. 1~2문장으로 아주 간결하고 자연스럽게 작성할 것.
2. evaluator를 선택했다면 "이번 라운드 토론이 충분히 진행되었으므로 수용도 평가 단계로 넘어가겠습니다." 와 같이 라운드 평가 이동을 알릴 것.
"""

    structured_llm = get_llm_smart().with_structured_output(SupervisorDecision)
    
    try:
        response = await structured_llm.ainvoke([
            SystemMessage(content=system_msg),
            HumanMessage(content=f"이전 대화:\n{history_text}\n\n사회자 진행 및 다음 발언자 선택:")
        ])
        next_speaker = response.next_speaker.strip()
        moderator_msg = f"[사회자 (Supervisor)]: {response.reasoning}"
    except Exception:
        next_speaker = "evaluator"
        moderator_msg = "[사회자 (Supervisor)]: 이번 라운드 토론을 정리하고 평가 단계로 넘어가겠습니다."
    
    valid_speakers = active_participants + ["evaluator"]
    
    if next_speaker not in valid_speakers:
        found = False
        for pid in valid_speakers:
            if pid in next_speaker:
                next_speaker = pid
                found = True
                break
        if not found:
            next_speaker = "evaluator"
            moderator_msg = "[사회자 (Supervisor)]: 이번 라운드 토론을 정리하고 평가 단계로 넘어가겠습니다."
        
    return {
        "next_speaker": next_speaker,
        "rebuttal_target": "",
        "rebuttal_count": 0,
        "messages": [moderator_msg]
    }

async def dynamic_persona_speaker_node(state: DynamicDiscussionState) -> dict:
    """next_speaker에 지정된 동적 페르소나의 프롬프트를 렌더링하고 발화"""
    speaker_id = state["next_speaker"]
    
    # 페르소나 설정 찾기
    persona_dict = None
    for p in state.get("personas", []):
        if p["persona_id"] == speaker_id:
            persona_dict = p
            break
            
    if not persona_dict:
        # 비상 대처: 페르소나를 찾지 못한 경우 (사실상 일어나지 않아야 함)
        return {"messages": [f"System Error: {speaker_id} 페르소나 정보를 찾을 수 없습니다."]}
        
    persona_config = PersonaConfig(**persona_dict)
    role_title = persona_config.display_name
    
    css_dict = state.get("css_levels", {})
    css_level = css_dict.get(speaker_id, "HIGH")
    site_info = state.get("site_information", "입지 정보 미지정")
    history_text = _format_history(state.get("messages", []))
    
    # 역할 시스템 프롬프트 렌더링
    role_prompt = render_persona_system_prompt(persona_config)
    
    # 조례(RAG) 데이터 가져오기 (State에 저장된 컨텍스트 우선 사용)
    ordinance_contexts = state.get("ordinance_contexts", [])
    if ordinance_contexts:
        retrieved_docs = []
        for ord_ctx in ordinance_contexts:
            if isinstance(ord_ctx, str):
                retrieved_docs.append(ord_ctx)
            elif isinstance(ord_ctx, dict):
                title = ord_ctx.get("ordinance_name") or ord_ctx.get("title") or ""
                content = ord_ctx.get("content") or ""
                if title:
                    retrieved_docs.append(f"[{title}] {content}")
                else:
                    retrieved_docs.append(content)
            else:
                retrieved_docs.append(str(ord_ctx))
    else:
        retrieved_docs = []
        
    # 첫 발언인지 확인 (is_opening_statement)
    is_opening_statement = False
    unique_speakers = set()
    for msg in state.get("messages", []):
        if msg.startswith("[팩트체커"):
            continue
        import re
        match = re.search(r'\((persona_\d+)\):', msg)
        if match:
            unique_speakers.add(match.group(1))
            
    if speaker_id not in unique_speakers:
        is_opening_statement = True
    
    prompt = build_multi_party_prompt(
        role_prompt=role_prompt,
        site_information=site_info,
        rag_context="\n".join(retrieved_docs),
        discussion_history=history_text,
        css_level=css_level,
        is_opening_statement=is_opening_statement
    )
    
    # 지정된 LLM 모델 가져오기
    model_name = getattr(persona_config, "preferred_model", "gpt-4o-mini")
    active_llm = get_llm_smart() if model_name.startswith("gpt-4o") and "mini" not in model_name else get_llm_fast()

    response = await active_llm.ainvoke([SystemMessage(content=prompt)])
    content = response.content.strip()
    
    # [REBUTTAL: persona_id] 태그 파싱
    import re
    rebuttal_target = ""
    match = re.search(r'\[REBUTTAL:\s*(persona_\d+)\]', content)
    if match:
        rebuttal_target = match.group(1)
        # UI에 노출되지 않도록 태그 제거
        content = re.sub(r'\[REBUTTAL:\s*persona_\d+\]', '', content).strip()
        
    return {
        "messages": [f"{role_title} ({speaker_id}): {content}"],
        "rebuttal_target": rebuttal_target
    }

async def dynamic_evaluator_node(state: DynamicDiscussionState) -> dict:
    """동적 페르소나들의 수용도를 평가하는 노드"""
    history_text = _format_history(state.get("messages", []))
    round_count = state.get("round_count", 0) + 1
    active_participants = state.get("active_participants", [])
    
    # 동적 JSON 스키마를 위한 안내 문자열 생성
    schema_fields = ",\n    ".join([f'"{pid}_acceptance": 0.0' for pid in active_participants])
    
    eval_prompt = f"""
당신은 다자간 심의 회의의 진행 상황을 정량 평가하는 AI 감사관입니다.

현재까지의 대화 내용을 종합 분석하여 현재 참여 중인 각 페르소나별 수용도 점수를 0.0 ~ 1.0 사이로 평가하세요.

[평가 기준]
- 0.0 ~ 0.2: 완강한 반대 및 수용 불가
- 0.3 ~ 0.6: 조건부 타협 및 부분 수용 고려
- 0.7 ~ 1.0: 높은 만족도 및 전면 합의

반드시 아래 JSON 형식으로만 응답하고, 부연 설명은 절대 출력하지 마십시오:
{{
    {schema_fields}
}}
"""
    
    llm_json = get_llm_smart().bind(response_format={"type": "json_object"})
    response = await llm_json.ainvoke([
        SystemMessage(content=eval_prompt),
        HumanMessage(content=f"이전 대화:\n{history_text}\n\n평가 JSON 작성:")
    ])
    
    try:
        evals = json.loads(response.content)
    except:
        evals = {}
        
    def score_to_css(score: float) -> str:
        if score < 0.3: return "HIGH"
        elif score < 0.7: return "MEDIUM"
        else: return "LOW"
        
    css_levels = {}
    for pid in active_participants:
        # 응답에 점수가 없으면 0.0 처리
        score = float(evals.get(f"{pid}_acceptance", evals.get(pid, 0.0)))
        css_levels[pid] = score_to_css(score)
    
    return {
        "evaluations": evals,
        "round_count": round_count,
        "css_levels": css_levels,
        "messages": [f"[사회자 (Supervisor)]: 제{round_count}라운드 토론에 대한 수용도 평가가 완료되었습니다."]
    }

async def dynamic_reporter_node(state: DynamicDiscussionState) -> dict:
    """토론 결과를 최종 리포팅하는 노드"""
    history_text = _format_history(state.get("messages", []))
    
    llm_json = get_llm_smart().bind(response_format={"type": "json_object"})
    response = await llm_json.ainvoke([
        SystemMessage(content=REPORTER_PROMPT),
        HumanMessage(content=f"전체 토론 내역:\n{history_text}\n\n최종 시나리오 보고서 JSON 작성:")
    ])
    
    try:
        scenarios = json.loads(response.content)
    except:
        scenarios = {}
        
    return {"final_scenarios": scenarios, "is_finished": True}

async def dynamic_factchecker_node(state: DynamicDiscussionState) -> dict:
    """방금 발언한 페르소나의 메시지에서 데이터 조작(할루시네이션)이 있는지 검증하는 노드 (유연성 확보를 위해 bypass/비활성화)"""
    # 팩트체커 유연성 부족으로 인해 비활성화 처리 (Bypass)
    return {}

    messages = state.get("messages", [])
    if not messages:
        return {}
        
    last_message = messages[-1]
    
    # 팩트체커가 자신이 한 정정 발언을 다시 검증하지 않도록 방지
    if last_message.startswith("[팩트체커 (System)]"):
        return {}
        
    site_info = state.get("site_information", "")
    
    ordinance_contexts = state.get("ordinance_contexts", [])
    ord_text = ""
    if ordinance_contexts:
        ord_lines = []
        for ord_ctx in ordinance_contexts:
            if isinstance(ord_ctx, str):
                ord_lines.append(ord_ctx)
            elif isinstance(ord_ctx, dict):
                title = ord_ctx.get("ordinance_name") or ord_ctx.get("title") or ""
                content = ord_ctx.get("content") or ""
                if title:
                    ord_lines.append(f"[{title}] {content}")
                else:
                    ord_lines.append(content)
            else:
                ord_lines.append(str(ord_ctx))
        ord_text = "\n".join(ord_lines)
    
    factcheck_prompt = f"""당신은 다자간 토론방의 데이터 팩트체커(Fact Checker)입니다.
아래 제공된 원본 데이터(공간 데이터 및 조례)와 방금 페르소나가 한 발언을 대조하여 발언의 사실 부합 여부를 매우 엄격하게 판정하세요.

[원본 공간 데이터 (GIS)]
{site_info}

[원본 조례 데이터 (RAG)]
{ord_text}

[검사할 방금 발언]
{last_message}

[판정 기준 (상태코드)]
- SUPPORTED: 제공 자료로 사실이 완벽히 확인됨
- CONTRADICTED: 제공 자료의 수치나 조항과 명확히 반대되게 거짓말을 함 (강력한 정정 필요)
- DISTORTED: 조례나 데이터를 유리하게 교묘하게 왜곡하여 해석함 (정정 필요)
- UNSUPPORTED: 조례나 데이터에 아예 없는 내용(허위 사실, 존재하지 않는 규정)을 마치 사실인 것처럼 확정지어 주장함 (정정 필요)
- UNVERIFIABLE: 현재 자료로 확인할 수 없는 합리적 가정(예: "아이들이 많이 지나다닐 수 있다") (개입 불필요)
- INTERPRETATION: 사실이 아니라 데이터에 대한 주관적 해석 (개입 불필요)
- VALUE_JUDGMENT: 가치판단 (건강, 안전 우선 등) (개입 불필요)
- MISSING_DATA: 원본 데이터가 누락되어 판단 불가 (개입 불필요)

[중요 규칙]
1. 페르소나가 주관적인 추측(UNVERIFIABLE)이나 가치판단(VALUE_JUDGMENT)을 하는 것은 허용되므로 정정 메시지를 보내지 마세요.
2. 단, **조례나 데이터에 없는 규정을 존재하는 것처럼 말하거나(UNSUPPORTED), 수치를 교묘하게 바꾸어 말하는 경우(DISTORTED)**에는 반드시 사실관계를 바로잡는 정정 메시지를 작성하세요.
"""

    class FactCheckResult(BaseModel):
        status: FactCheckStatus = Field(..., description="판정 상태")
        reason: str = Field(..., description="판정 이유")
        correction_message: str = Field(..., description="CONTRADICTED, DISTORTED, UNSUPPORTED일 경우 사용자에게 보여줄 정정 메시지. (예: '제공된 데이터에는 해당 내용이 명시되어 있지 않습니다.'). 아닐 경우 빈 문자열.")

    # 판단력이 중요하므로 get_llm_smart()(gpt-4o) 사용
    llm_structured = get_llm_smart().with_structured_output(FactCheckResult)
    result = await llm_structured.ainvoke([SystemMessage(content=factcheck_prompt)])
    
    if result.status in [FactCheckStatus.CONTRADICTED, FactCheckStatus.DISTORTED, FactCheckStatus.UNSUPPORTED] and result.correction_message:
        msg = result.correction_message
        if not msg.startswith("[팩트체커"):
            msg = f"[팩트체커 (System)]: {msg}"
        return {"messages": [msg]}
    else:
        return {}
