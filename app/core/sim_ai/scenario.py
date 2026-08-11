# -*- coding: utf-8 -*-
"""최종 시나리오 객체에서 **A/B/C 코드와 비교용 텍스트**를 뽑는다.

여기 한 곳에만 둔다. 예전엔 소비자마다 각자 뽑았고 **어휘가 갈렸다**:

- `app/api/v1/simulations.py` 는 `scenario` 를 읽었다 (A 엔진 · `reporter.txt:11`)
- `app/core/audit_ai/classifier.py` 는 `scenario_type` 을 읽고 **없으면 `"A"`** 로
  기본값을 넣었다 (2026-07-09 작성). 그런데 그날의 `reporter.txt` 는 JSON 스키마가
  아예 없는 산문이었다 — **읽을 키가 정해지기 전에 가정한 이름**이고, 기본값이
  그 가정을 덮어 안 보이게 했다. 결과: `matched_scenario` 가 **항상 `"A"`**.
  안 터지고 값만 틀린다.

두 이름 다 실재한다 — `scenario` 는 A 대립토론(`reporter.txt`),
`scenario_type` 은 B 다인토론(`multi_party_discussion_prompts.py:88`)이다.
그래서 둘 다 받되 **여기서만** 받는다. 소비자가 각자 받으면 다시 갈린다.
"""
import re

# `conflict_simulations` 의 칸 이름과는 별개다. 여기는 **코드 어휘**만 안다.
SCENARIO_CODES = ("A", "B", "C")

# 🔴 `"Scenario A"` 를 글자 단위로 훑으면 `SCENARIO` 의 **`C` 가 먼저 걸려 "C"** 가 된다.
#    A 엔진 템플릿은 `"A (또는 B, C)"` 형식이라 여태 안 걸렸을 뿐이고, B 엔진 예시는
#    실제로 `"Scenario A"` 다. 코드를 찾기 전에 이 낱말을 지운다.
_LABEL_RE = re.compile(r"scenario|시나리오", re.IGNORECASE)

# 값이 `scenario` 인지 `scenario_type` 인지는 **엔진이 정한다**. 순서에 의미는 없다.
_CODE_KEYS = ("scenario", "scenario_type")

# 비교용 텍스트를 만들 때 이어붙이는 칸. 앞에서부터 있는 것만 쓴다.
# `summary` 하나만 보면 그 칸이 빈 시나리오는 **유사도 0.0** 이 되어
# 「OCR 과 겹치는 단어가 없음」과 구분이 안 된다(원칙 4).
_TEXT_KEYS = ("summary", "scenario_description", "reason", "risk_reason")


def scenario_code(scenario: dict) -> str | None:
    """시나리오 객체에서 A/B/C 를 뽑는다. **못 뽑으면 `None`** — 추측하지 않는다."""
    for key in _CODE_KEYS:
        raw = _LABEL_RE.sub("", str(scenario.get(key) or "")).strip().upper()
        for ch in raw:
            if ch in SCENARIO_CODES:
                return ch
    return None


def scenario_compare_text(scenario: dict) -> str:
    """유사도 대조에 쓸 텍스트. 없으면 **빈 문자열**(호출자가 구분해서 처리한다)."""
    parts = [str(scenario.get(k)).strip() for k in _TEXT_KEYS if scenario.get(k)]
    return "\n".join(parts)
