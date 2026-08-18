# -*- coding: utf-8 -*-
"""§2 LLM 호출부 — 인터페이스(`LLMClient`) + 목/실제 구현.

목을 먼저 두는 이유는 채점 로직을 LLM 없이 검증하기 위해서다(설계 확정 (나)).
"""
from __future__ import annotations

import json
import re

# ══════════════════════════════════════════════════════════════════
# 2. LLM 호출 인터페이스 + 목
# ══════════════════════════════════════════════════════════════════


class LLMClient:
    """실제 (가)로 넘어갈 때 이 인터페이스만 구현하면 됨."""

    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError


class MockLLM(LLMClient):
    """하네스 출력 형식 확인용(키 불필요). 흡연 도메인 가정의 고정 시나리오를 반환.
    → 리포트·flag·저장이 정상 동작하는지 형식만 확인하는 용도."""

    # MockLLM 전용 시나리오(흡연 도메인 기준). 실제 판정과 무관 — 형식 확인용.
    _SCENARIO = {
        "01": {"roles": ["positive_factor"], "coord": "needs_geocoding"},
        "02": {"roles": ["positive_factor"], "coord": "has_coords"},
        "03": {"roles": ["hard_exclusion"], "coord": "has_coords"},
        "04": {"roles": ["hard_exclusion"], "coord": "has_coords"},
        "05": {"roles": ["hard_exclusion"], "coord": "has_coords"},
        "06": {"roles": ["hard_exclusion", "positive_factor"], "coord": "has_coords"},
        "07": {"roles": ["hard_exclusion", "positive_factor"], "coord": "has_coords"},
        "08": {"roles": ["positive_factor"], "coord": "stat_join"},
        "09": {"roles": ["positive_factor"], "coord": "stat_join"},
        "10": {"roles": ["positive_factor"], "coord": "stat_join"},
        "12": {"roles": ["positive_factor"], "coord": "needs_geocoding"},
    }
    _SUMMARY = {
        "01": "담배꽁초 무단투기 지점 — 흡연 수요가 높은 곳(가점). 주소만 있어 지오코딩 필요",
        "03": "어린이보호구역 — 조례상 흡연시설 설치 금지(배제)",
        "06": "버스정류소 — 유동인구 거점(가점)이면서 조례 10m 배제 대상(공존)",
        "12": "가로휴지통 위치 — 흡연 관련 인프라(가점). 주소만 있어 지오코딩 필요",
    }

    def complete(self, system: str, user: str) -> str:
        u = json.loads(user)
        did = (
            u["dataset"].get("dataset_id") or ""
        )  # 프로파일 dataset_id 사용(파일명 가나다순 01,02…)
        ref = self._SCENARIO.get(did, {"roles": [], "coord": "stat_join"})
        roles = []
        for rn in ref["roles"]:
            if rn == "hard_exclusion":
                _ft = {
                    "03": "어린이보호구역",
                    "04": "학교절대보호구역",
                    "05": "어린이집",
                    "06": "버스정류소",
                    "07": "지하철역",
                }.get(did, "시설")
                # 조례(제5조)에 반경 명시된 것만 확정값. 나머지는 None→HITL.
                _has_radius = did in ("06", "07")  # 조례에 10m 명시된 것만
                roles.append(
                    {
                        "role": "hard_exclusion",
                        "exclusion_type": "radius",
                        "facility_type": _ft,
                        "배제반경_m": 10 if _has_radius else None,
                        "source": "조례 제5조" if _has_radius else None,
                        "confirmed": _has_radius,
                        "need_review": not _has_radius,
                        "rationale": "조례 근거"
                        if _has_radius
                        else "조례에 반경 없음→HITL",
                    }
                )
            else:
                w = 0.7 if rn == "positive_factor" else -0.5
                roles.append({"role": rn, "weight": w, "rationale": "mock"})
        return json.dumps(
            {
                "dataset_id": did,
                "summary": self._SUMMARY.get(did, f"{did} 데이터"),
                "roles": roles,
                "coord_status": ref["coord"],
                "cleaning_ops": [],  # mock 은 형식·시간 확인용. op 판정은 real(gpt-4o)에서만.
                "hitl_flags": [],
            },
            ensure_ascii=False,
        )


class RealLLM(LLMClient):
    """(가) 실제 판정용 — OpenAI. JSON 모드로 유효 JSON 강제.
    모델명은 config.SEARCH_LLM_MODEL(기본 gpt-4o-mini). 검색·추출이라 mini로 충분.
    키는 .env 의 OPENAI_API_KEY (코드에 안 박음)."""

    def __init__(self, model: str | None = None):
        from openai import OpenAI  # 지연 임포트(목만 쓸 땐 불필요)
        from app.config import OPENAI_API_KEY, AUDIT_LLM_MODEL

        if not OPENAI_API_KEY:
            raise RuntimeError(".env 에 OPENAI_API_KEY 를 설정하세요.")
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.model = model or AUDIT_LLM_MODEL

    # TPM(분당 토큰) 한도에 걸리면(429) 잠시 쉬고 재시도. 데이터셋을 연속 호출하므로
    # 한도가 낮은 계정에서는 정상적으로 발생한다 → 파이프라인을 중단시키지 않는다.
    #   ★ 대기 시간은 API 가 알려주는 값을 쓴다("Please try again in 1.122s").
    #     고정 20초로 기다리면 11개 데이터셋에서 1분 이상을 그냥 버린다.
    RETRY = 6
    BACKOFF_SEC = 5  # 응답에 대기시간이 없을 때만 쓰는 기본값(지수 증가)
    MAX_WAIT_SEC = 60

    @staticmethod
    def _retry_after(msg: str) -> float | None:
        """429 메시지에서 권장 대기시간(초) 추출. 'try again in 1.122s' / '2m30s' 대응."""
        m = re.search(r"try again in\s+(?:(\d+)m)?\s*([\d.]+)s", msg)
        if not m:
            return None
        mins = float(m.group(1) or 0)
        return mins * 60 + float(m.group(2))

    def complete(self, system: str, user: str) -> str:
        import time as _t

        last = None
        for attempt in range(self.RETRY):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0,  # 판정 재현성 위해 0
                    response_format={
                        "type": "json_object"
                    },  # JSON 모드(형식 이탈 방지)
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                return resp.choices[0].message.content
            except Exception as e:
                last = e
                if "rate_limit" not in str(e).lower() and "429" not in str(e):
                    raise
                hinted = self._retry_after(str(e))
                wait = (hinted + 0.5) if hinted else self.BACKOFF_SEC * (2**attempt)
                wait = min(wait, self.MAX_WAIT_SEC)
                src = "API 권장" if hinted else "기본"
                print(
                    f"\n  [rate limit] {wait:.1f}s 대기 후 재시도 "
                    f"({attempt + 1}/{self.RETRY}, {src})"
                )
                _t.sleep(wait)
        raise last
