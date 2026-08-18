# -*- coding: utf-8 -*-
"""
OmniSite 감리 AI — 판정 테스트 하네스 (실제 판정 테스트)
======================================================
목적: 감리 AI(LLM)가 데이터 프로파일을 보고 올바른 역할 + op 조합을 고르는지를
      (정답표 대조 채점 구조는 폐기 — 판정 결과를 그대로 리포트한다.)

설계(사용자 확정)
  - (나) 하네스+목 우선: LLM 호출부는 인터페이스(LLMClient)만 두고, MockLLM 으로
    채점 로직을 먼저 검증. 실제 (가)로 넘어갈 때 RealLLM 만 꽂으면 됨.
  - 채점 3기준: ① 역할 적중 ② op 집합 적중 ③ 누락/과잉 op

구성
  1) build_prompt(profile)     : 시스템+유저 프롬프트 조립(카탈로그 동적 주입)
  2) LLMClient / MockLLM       : 호출 인터페이스 + 목 구현
  3) review_one / run_harness  : 판정 + 리포트
  4) build_fixtures(폴더)      : profile.py 로 실제 파일을 읽어 프로파일 생성
                                 (도메인 폴더만 갈아끼우면 다른 도메인도 동작)

의존: gam2_audit_ops_catalog · gam2_profile · config

────────────────────────────────────────────────────────────────────────────
패키지 구성 (2026-08-19 분할. 그 전에는 `gam2_audit_judgment_test.py` 2,179행 한 파일)

  state       도메인 컨텍스트·산출물 경로        ← 아무것도 import 안 한다
  prompts     프롬프트 빌더 · 시설 확정
  llm         LLMClient / MockLLM / RealLLM
  intents     데이터 의도 답변 적용              ← 아무것도 import 안 한다
  fixtures    조례 로드 · 프로파일 생성 · 지역 요구
  admin_code  행정코드 크로스워크
  exclusions  배제반경 HITL · 상위법 검색
  harness     Judgment · review_one · run_harness
  hitl        review_hitl (대화형)
  outputs     산출물 저장 · 리포트
  __main__    CLI

import 순서는 위 그대로다. 역방향은 없다 —
`exclusions → admin_code → fixtures`, `harness → {llm, prompts, exclusions}`,
`hitl → {admin_code, exclusions, intents}`, `outputs → harness` 로 전부 한 방향이다.

🔴 **CLI 실행 형태가 바뀐다.** 패키지는 스크립트로 못 돈다:
     (옛) python app\\services\\gam2_audit_judgment_test.py hitl <도메인>
     (현) python -m app.services.gam2_audit_judgment_test hitl <도메인>
   러너는 이 모듈을 subprocess 로 **안 부른다**(`pipeline_runner/commands.py` 의
   `_svc(...)` 대상 5개에 없다) — 그래서 바뀌는 것은 **사람이 손으로 치는 경로뿐**이고,
   그 안내 문구 두 곳(`gam4_site_select.py`·`pipeline_runner/runner.py`)을 같이 고쳤다.

🔴 **분할해도 밖에서 보는 이름은 하나도 안 바뀐다.** `import app.services.
   gam2_audit_judgment_test as A` 로 쓰던 곳(`gam2_clean_data`·`gam2_run_pipeline`·
   `pipeline_runner.answers`·`pipeline_runner.prepare`·대조기 2종)이 그대로 돌아야 한다 —
   그래서 아래에서 전부 다시 내보내고, 맨 끝에 `_Proxy` 를 건다.
"""
from __future__ import annotations

import sys
import types

from . import (
    admin_code,
    exclusions,
    fixtures,
    harness,
    hitl,
    intents,
    llm,
    outputs,
    prompts,
    state,
)

# ── state ───────────────────────────────────────────────────────────────
from .state import (  # noqa: F401
    _DOMAIN,
    _ROOT,
    _out_path,
    set_domain,
)

# ── prompts ─────────────────────────────────────────────────────────────
from .prompts import (  # noqa: F401
    MAX_PEER_COLS,
    ROLE_ENUM_DOC,
    SYSTEM_PROMPT_TEMPLATE,
    _peer_summaries,
    build_prompt,
    get_system_prompt,
    resolve_facility,
    resolve_facility_mock,
)

# ── llm ─────────────────────────────────────────────────────────────────
from .llm import (  # noqa: F401
    LLMClient,
    MockLLM,
    RealLLM,
)

# ── intents ─────────────────────────────────────────────────────────────
from .intents import (  # noqa: F401
    _read_int,
    _read_weight,
    apply_intent_answer,
)

# ── fixtures ────────────────────────────────────────────────────────────
from .fixtures import (  # noqa: F401
    build_fixtures,
    load_ordinance,
    require_region,
)

# ── admin_code ──────────────────────────────────────────────────────────
from .admin_code import (  # noqa: F401
    ADM_CODE_SHEET,
    _ADM_CODE_CACHE,
    _FIXTURE_CACHE,
    _SIDO_ALIAS,
    _code_samples,
    _crosswalk_path,
    _enrich_code_prefix,
    _load_admin_code_map,
    _norm_sido,
    detect_code_system,
    region_is_unique,
    resolve_code_prefix,
    split_region,
    suggest_code_prefix,
    verify_code_prefix,
)

# ── exclusions ──────────────────────────────────────────────────────────
from .exclusions import (  # noqa: F401
    _norm,
    _read_radius,
    apply_radius_answer,
    assert_exclusions_confirmed,
    confirm_exclusion_radius,
    enrich_hitl_flags,
    enrich_with_search,
    reset_exclusion_confirmations,
    search_exclusion_radius,
)

# ── harness ─────────────────────────────────────────────────────────────
from .harness import (  # noqa: F401
    Judgment,
    review_one,
    run_harness,
)

# ── hitl ────────────────────────────────────────────────────────────────
from .hitl import review_hitl  # noqa: F401

# ── outputs ─────────────────────────────────────────────────────────────
from .outputs import (  # noqa: F401
    facility_inference_doc,
    report,
    save_facility_inference,
    save_results,
)

_SUBMODULES: tuple[types.ModuleType, ...] = (
    state,
    prompts,
    llm,
    intents,
    fixtures,
    admin_code,
    exclusions,
    harness,
    hitl,
    outputs,
)


class _Proxy(types.ModuleType):
    """`A.<이름> = 값` 을 **소유 서브모듈의 전역까지** 밀어 넣는다.

    왜 필요한가 — 파이썬에서 `from .llm import LLMClient` 는 **값을 복사**한다.
    한 파일이던 시절 `A.MockLLM = 가짜` 는 그 파일의 전역 하나를 고쳤고 모든 함수가
    즉시 새 값을 봤다. 패키지로 가르면 그 대입은 **패키지 객체의 attr 하나**만 고치고
    서브모듈들은 옛 값을 계속 본다.

    🔴 이 어긋남은 **예외를 안 낸다.** 실제로 갈아끼우는 곳이 있다 —
       `app/tools/check_full_step01.py` §10 이 이름 12개를 스텁으로 바꿔 STEP0·1 을
       LLM 없이 돌린다. 프록시가 없으면 스텁을 꽂아도 진짜 `RealLLM` 이 불려
       **대조기가 돈이 드는 진짜 호출을 한다**(그리고 초록불이 뜬다).
       패키지 안쪽도 같다: `harness` 는 `enrich_hitl_flags` 를, `admin_code` 는
       `build_fixtures` 를 자기 전역으로 들고 있다.

    🔴 모호하면 **터진다**(원칙 1). 같은 이름을 두 서브모듈이 **서로 다른 것**으로
       들고 있으면 어느 쪽을 고쳐야 하는지 알 수 없다 — 추측해서 하나만 고치면
       나머지가 조용히 옛 값으로 돈다. 그래서 `raise` 한다.
       (같은 객체를 여러 서브모듈이 import 해 갖고 있는 것은 모호가 아니다 —
        그게 정상이고, 그때는 **전부** 고친다.)
    """

    def __setattr__(self, name: str, value) -> None:
        owners = [m for m in _SUBMODULES if name in m.__dict__]
        if len(owners) > 1:
            ids = {id(m.__dict__[name]) for m in owners}
            if len(ids) > 1:
                raise RuntimeError(
                    f"'{name}' 을 여러 서브모듈이 **서로 다른 것**으로 들고 있습니다: "
                    f"{[m.__name__.rsplit('.', 1)[-1] for m in owners]}. "
                    f"어느 쪽을 갈아끼워야 하는지 알 수 없어 멈춥니다 — "
                    f"이름을 가르거나 소유 서브모듈을 직접 지정하세요."
                )
        for m in owners:
            m.__dict__[name] = value
        super().__setattr__(name, value)


# 🔴 **맨 끝이어야 한다.** 이 줄 위쪽의 `from … import` 들도 결국 이 모듈에 대한
#    attr 대입이라, 프록시를 먼저 걸면 import 하는 내내 전파가 돈다(느리고, 아직
#    `_SUBMODULES` 가 없어 `NameError` 다).
sys.modules[__name__].__class__ = _Proxy
