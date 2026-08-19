# -*- coding: utf-8 -*-
"""
OmniSite 가중치 모델 (STEP 5 · 감리/정제 → 최종 가중치)
========================================================
정제 산출물(step2_output) + 감리 결과(reviewed.json) 로부터
위치 선정에 쓸 최종 가중치를 만든다.  위치 선정 자체는 하지 않는다(다음 단계).

파이프라인
  [R] suggest_radius   : mini 가 facility+지표 rationale 로 집계반경 R 제안 → HITL 확정
  [A] define_indicators: positive/negative role + whitelist_resolved 로
                         좌표레이어(+)통계표 자동 병합 → 지표 K개  (도메인/번호 하드코딩 0)
  [B] build_matrix     : 후보 N × 지표 K.  반경 R 내 합산 / 행정동 지표는 소속 동 값
  [C] human_weights    : 감리 weight seed 정규화  (쌍대비교는 HITL-2에서 대체)
  [D] critic_weights   : Spearman-CRITIC.  희소지표(비영<5%) 제외.  부트스트랩 CI
  [E] synthesize       : w = (1-alpha)*w_human + alpha*w_critic
  [F] build_weight_set : 전 단계 근거 동봉 dict → JSON/DB

설계 원칙
  - R 은 하드코딩하지 않는다 — mini 제안 + HITL 확정, weight_set 에 기록해 재현.
  - 지표 병합 관계는 코드에 박지 않는다 — clean_report.whitelist_resolved 를 읽는다.
  - 가중치는 평가단위(후보집합)에 의존하지 않는 '지표 간 상대 중요도' — DB 저장 후 재사용.

--------------------------------------------------------------------
# TODO(설치 시 확인):
#   1) config import — 이 파일은 app/services/ 에 둔다는 전제.
#   2) ADM_DONG_SHP 의 행정동코드 컬럼명(_ADM_CODE_COL) 을 실제 SHP 에 맞춰라.
#   3) 생활인구 행정동코드 자릿수 <-> 경계 SHP 코드 자릿수 (_admin_code_match).
--------------------------------------------------------------------

────────────────────────────────────────────────────────────────────────────
패키지 구성 (2026-08-19 분할. 그 전에는 `gam2_weight_model.py` 1,752행 한 파일)

  state        config 조달 · 상수            ← 아무것도 import 안 한다
  util         rankdata · Timer · 정규화기 · 값컬럼 추정 · 안전 상관
  radius       [R] 집계반경 제안
  indicators   [A] 지표 정의
  admin        행정동 코드 · 크로스워크 · 지역 파일 찾기
  matrix       [B] 후보×지표 행렬 · 정규화 · 희소 판정
  weights      [C][D][E] human · CRITIC · 합성
  hitl         [W] 슬라이더 · 데이터 주석 · 답변 적용
  outputs      [F] 지문 · weight_set · 제안서 저장
  diagnostics  표본 편향 · alpha 민감도

import 순서는 위 그대로다. 역방향은 없다 —
`admin → state`, `matrix → {state, util, admin}`, `weights → util`,
`outputs → {state, hitl}`, `diagnostics → {matrix, weights}` 로 전부 한 방향이다.

🔴 **CLI 가 없다.** 이 모듈은 예나 지금이나 스크립트로 안 돈다 —
   `if __name__ == "__main__"` 이 원본에도 없었다. 실행 진입점은
   `run_weight_model.py` 이고 그건 이 패키지를 `import … as W` 로 쓴다.
   그래서 감리 패키지와 달리 **사람이 치는 명령이 하나도 안 바뀐다.**

🔴 **분할해도 밖에서 보는 이름은 하나도 안 바뀐다.** `import app.services.
   gam2_weight_model as W` 로 쓰던 곳(`gam4_site_select`·`run_weight_model`·
   `check_loader_health`)과 `from … import find_region_file` 로 쓰던 곳
   (`make_parcel_candidates`·`check_exclusion_state`)이 그대로 돌아야 한다 —
   그래서 아래에서 전부 다시 내보내고, 맨 끝에 `_Proxy` 를 건다.
"""
from __future__ import annotations

import sys
import types

from . import (
    admin,
    diagnostics,
    hitl,
    indicators,
    matrix,
    outputs,
    radius,
    state,
    util,
    weights,
)

# ── state ───────────────────────────────────────────────────────────────
from .state import (  # noqa: F401
    ADM_DONG_SHP,
    ADMIN_CROSSWALK_PATH,
    NEUTRAL_SEED,
    OPENAI_API_KEY,
    REGION_DATA_DIR,
    SEARCH_LLM_MODEL,
    SPARSE_THRESHOLD,
    SPATIAL_CRS,
    STEP2_OUTPUT_DIR,
    WEIGHT_OUTPUT_DIR,
    WORK_CRS,
    _ADM_CODE_COL,
)

# ── util ────────────────────────────────────────────────────────────────
from .util import (  # noqa: F401
    Timer,
    _NORMALIZERS,
    _VALUE_HINT,
    _norm_none,
    _norm_station,
    _pick_value_cols,
    _safe_corr,
    rankdata,
)

# ── radius ──────────────────────────────────────────────────────────────
from .radius import (  # noqa: F401
    _mock_radius,
    suggest_radius,
)

# ── indicators ──────────────────────────────────────────────────────────
from .indicators import (  # noqa: F401
    _seed_magnitude,
    define_indicators,
)

# ── admin ───────────────────────────────────────────────────────────────
from .admin import (  # noqa: F401
    _XWALK_CACHE,
    _admin_code_match,
    _detect_admin_key_col,
    _norm_dong,
    admin_names_to_codes,
    find_region_file,
    load_admin_crosswalk,
    sgg_code_of,
)

# ── matrix ──────────────────────────────────────────────────────────────
from .matrix import (  # noqa: F401
    as_geodataframe,
    attach_layers,
    build_matrix,
    detect_sparse,
    normalize_matrix,
)

# ── weights ─────────────────────────────────────────────────────────────
from .weights import (  # noqa: F401
    critic_bootstrap,
    critic_weights,
    human_weights,
    synthesize,
)

# ── hitl ────────────────────────────────────────────────────────────────
from .hitl import (  # noqa: F401
    apply_weight_hitl,
    data_note,
    slider_from_indicators,
    slider_pct,
)

# ── outputs ─────────────────────────────────────────────────────────────
from .outputs import (  # noqa: F401
    build_weight_proposal,
    build_weight_set,
    fingerprint,
    save_weight_proposal,
    save_weight_set,
)

# ── diagnostics ─────────────────────────────────────────────────────────
from .diagnostics import (  # noqa: F401
    diagnose_alpha,
    diagnose_sample_bias,
)

_SUBMODULES: tuple[types.ModuleType, ...] = (
    state,
    util,
    radius,
    indicators,
    admin,
    matrix,
    weights,
    hitl,
    outputs,
    diagnostics,
)


class _Proxy(types.ModuleType):
    """`W.<이름> = 값` 을 **소유 서브모듈의 전역까지** 밀어 넣는다.

    왜 필요한가 — 파이썬에서 `from .matrix import build_matrix` 는 **값을 복사**한다.
    한 파일이던 시절 `W.build_matrix = 가짜` 는 그 파일의 전역 하나를 고쳤고 모든 함수가
    즉시 새 값을 봤다. 패키지로 가르면 그 대입은 **패키지 객체의 attr 하나**만 고치고
    서브모듈들은 옛 값을 계속 본다.

    🔴 이 어긋남은 **예외를 안 낸다.** 지금 이 저장소에 `W.<이름> = …` 로 갈아끼우는
       곳은 **0곳**이다 — 그래서 이 프록시는 오늘의 버그를 고치는 게 아니라
       **내일의 조용한 오동작을 막는다**(원칙 1). 감리(`A`)·러너(`R`) 패키지가
       같은 규칙이라, `A.RealLLM = 스텁` 이 되는 걸 본 사람은 `W.build_matrix = 스텁`
       도 될 거라고 읽는다. 셋 중 하나만 다르면 그 기대가 **말없이 배신당한다**.
       패키지 안쪽에도 사본이 있다: `matrix` 는 `load_admin_crosswalk` 를,
       `diagnostics` 는 `critic_weights`·`synthesize` 를 자기 전역으로 들고 있다.

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
