# -*- coding: utf-8 -*-
"""단계 목록·산출물 화이트리스트·**실행 계획**.

계획(`_PLAN`)이 여기 있는 이유 — 게이트는 별도 장치가 아니라 **계획 안의 한 칸**이다.
단계 이름과 계획이 갈라지면 `_resume_index`(답변 후 재개 위치)가 조용히 밀린다.
"""

from __future__ import annotations

import re

from .state import MODE_FIXTURE, MODE_FULL, MODE_HITL


# ══════════════════════════════════════════════════════════════════
# 1. 단계 정의  (계약 2절 — id·개수 고정, label 은 2026-08-04 실측으로 확정)
# ══════════════════════════════════════════════════════════════════
# 실행 단위는 **프로세스 4개**다. 6단계와 1:1 이 아니다:
#   · `2`·`3-1`·`3-2` 는 각각 프로세스 하나 = 경계가 확실하다.
#   · `4-1`·`4-2`·`4-3` 은 gam4_site_select.py **한 프로세스 안**이라
#     stdout 마커로만 나뉜다. 마커는 계약이 아니다 — 문구가 바뀌면 못 본다.
#     그래서 못 봐도 프로세스가 정상 종료하면 done 으로 닫되,
#     **소요 시간은 지어내지 않고 null 로 둔다**(원칙 4).
STEP_LABELS: list[tuple[str, str]] = [
    ("2", "정제"),
    ("3-1", "후보 필지 생성"),
    ("3-2", "가중치 산정"),
    ("4-1", "후보점 생성"),
    ("4-2", "점수화·배제 적용"),
    ("4-3", "위치 선정"),
]

# gam4 내부 단계의 시작을 알리는 stdout 마커 (gam4_site_select.py 의 print 문구)
_GAM4_MARKERS: dict[str, str] = {
    "4-1": "[B] 후보점 생성",
    "4-2": "[C] 지표 정의·부착",
    "4-3": "[H] 선정",
}

# 🔴 적재가 **두 칸**인 이유 — 화면5 로 넘어가려면 다리가 둘 다 있어야 한다.
#    후보점(`booth_candidates`)만 넣으면 목록은 뜨는데 토론이 첫 줄에서 죽는다:
#    `_select_audit_rules` 가 읽을 `audit_rules` 가 그 도메인에 없기 때문이다
#    (2026-08-10 실측 — `r_20260810_001` 이 여기서 막혔다. 다행히 조용히 죽지 않고
#    "적재된 (도메인, 시설)" 을 세어 알려줬다).
#    한 칸에 두 프로세스를 넣지 않는다 — 어느 쪽이 실패했는지 진행 표시에서 사라진다.
_LOAD_LABELS: list[tuple[str, str]] = [
    ("적재-감리", "감리 규칙 DB 적재 (토론 근거)"),
    ("적재-후보", "후보점 DB 적재 (화면5 목록)"),
]

# full 모드는 앞에 STEP0·STEP1 두 칸이 더 붙는다.
#   🔴 이 둘을 **모든 모드에 같이 두지 않는다.** fixture 는 STEP1 을 안 돌리므로
#      영원히 idle 인 단계가 화면에 남는다 — 진행률이 거짓말을 한다(원칙 4).
#      그래서 단계 목록은 mode 에서 유도한다(`step_labels`).
_STEP_LABELS_FULL: list[tuple[str, str]] = [
    ("0", "프로파일링 · 시설/지역 확정"),
    ("1", "감리 판정 · 상위법 검색"),
] + STEP_LABELS + _LOAD_LABELS

# 🔴 fixture 도 적재한다 (2026-08-11, 사람 결정). 예전엔 full 에만 있었고 사유는
#    "fixture 는 정본 산출물의 재생이고 그 Top-N 은 이미 `run_id='정본'` 으로 DB 에
#    있다" 였다 — 맞는 말이지만, 그래서 **fixture run 의 결과는 화면5 에서 볼 수가
#    없었다.** 시연에서 업로드를 건너뛰고 화면5까지 가려면 이 두 칸이 있어야 한다.
#    누적 우려(그때의 반대 근거)는 `runs/` 정리 정책 쪽에서 받는다 — `run_pruner`.
#    🔴 hitl 도 같은 날 붙였다(사람 지시). 처음엔 뺐고 이유는 "게이트에서 사람을
#       기다리므로 시연 프리셋이 아니다" 였는데, 그건 **왜 fixture 에 넣는가**의
#       답이지 **왜 hitl 에서 빼는가**의 답이 아니다. 게이트를 지나 완주한 run 은
#       사람이 값을 확정한 run 이다 — 그 결과를 화면5 에서 못 보는 건 똑같은 구멍이다.
_STEP_LABELS_WITH_LOAD: list[tuple[str, str]] = STEP_LABELS + _LOAD_LABELS

# gam2_run_pipeline.py 의 `_step()` 이 찍는 구분선 머리글.
#   "▶ STEP 0.5 시설·지역 확정" 은 마커에 **일부러 없다** — 뒤의 공백 하나로
#   "▶ STEP 0 " 과 갈린다. 0.5 를 단계로 세면 계약의 단계 수가 또 늘어난다.
_RUNPIPE_MARKERS: dict[str, str] = {
    "0": "▶ STEP 0 ",
    "1": "▶ STEP 1 ",
}


def step_labels(mode: str) -> list[tuple[str, str]]:
    # 지금은 세 모드 다 적재 칸을 갖는다. 그래도 mode 로 유도하는 구조는 유지한다 —
    # 모드가 늘거나 한 모드에서 칸이 빠질 때 볼 곳이 여기 하나여야 한다.
    if mode == MODE_FULL:
        return _STEP_LABELS_FULL
    return _STEP_LABELS_WITH_LOAD


# ══════════════════════════════════════════════════════════════════
# 2. 산출물 화이트리스트 (계약 1절)
#    🔴 `name` 을 경로로 쓰지 않는다. 여기 매핑을 통해서만 파일에 닿는다.
# ══════════════════════════════════════════════════════════════════
#   (step 폴더, 프리픽스 뒤에 붙는 파일명)
ARTIFACTS: dict[str, tuple[str, str]] = {
    # 이것만 단계가 만드는 게 아니라 `_prepare_dirs` 가 픽스처에서 복사해 넣는다.
    # 그래서 run 생성 직후부터 200 이다. 정본 step1_output/ 이 아니라 **run 안의
    # 사본**을 가리켜야 한다 — 정본을 가리키면 run 격리가 깨진다.
    "reviewed": ("step1", "_audit_result_reviewed.json"),
    # 화면2 STEP1「선정 대상」. `reviewed.facility_inference` 와 **같은 값**인데
    # 나오는 시점이 다르다 — 이건 STEP 0.5 직후(칸 "0", 실측 ~15초)이고 저건 감리
    # (실측 238초) 뒤다. 화면이 시설·지역 한 줄 때문에 감리를 기다릴 이유가 없다.
    # 🔴 `full` 에만 생긴다. fixture·hitl 은 STEP0-1 을 안 돌아 **항상 null** 이므로
    #    프런트는 반드시 `reviewed.facility_inference` 로 되짚을 것.
    "facility": ("step1", "_facility_inference.json"),
    "clean_report": ("step2", "_clean_report.json"),
    "candidates": ("step3", "_후보_지적도필지.gpkg"),
    "weight_set": ("step3", "_weight_set.json"),
    "report": ("step4", "_report.json"),
    "topN": ("step4", "_topN_min.csv"),
    "score_grid": ("step4", "_score_grid.json"),
    # 화면2b「최종 판정」. S9 점/면 판정 결과가 레이어(dataset_id)별로 들어 있고
    # `type`(최종) · `type_llm`(LLM 제안) · `type_source` 를 **같이** 실어 보낸다 —
    # 규약("값마다 누가 정했는지 남긴다")이 산출물에 그대로 드러나는 유일한 파일이다.
    # 이게 없으면 프런트는 최종 판정을 report.json 에서 **유추**해야 한다(원칙 5 위반).
    "exclusion": ("step4", "_exclusion.geojson"),
}

# 정제 산출물은 데이터셋마다 확장자가 다르다(gpkg / parquet). 이름으로 추측하지 않고
# clean_report.json 의 `output` 을 읽어 확정한다. 이름 형식: clean_01 … clean_11
_CLEAN_NAME_RE = re.compile(r"^clean_(\d{2})$")



# ══════════════════════════════════════════════════════════════════
# 8. 실행 계획 — 게이트는 계획 안의 한 칸이다
# ══════════════════════════════════════════════════════════════════
# 🔴 `hitl` 이 `fixture` 에 게이트 두 칸과 제안 패스를 끼워 넣은 것뿐이라는 게 중요하다.
#    단계 커맨드는 두 모드가 **같은 `_proc_of`** 를 탄다. 모드별로 따로 짜면
#    "픽스처는 맞는데 hitl 은 다른 값" 이 나오고 그건 이 프로젝트가 반복해서 당한 유형이다.
#
#    재실행은 0회다. 게이트에서 **스레드가 끝나고**, 답이 오면 그 다음 칸부터
#    새 스레드가 이어 간다. 진행 상태는 전부 디스크(status.json · run 폴더)에 있으므로
#    서버가 재시작돼도 답변 POST 로 이어갈 수 있다.
#
#    🔴 `full` 은 `hitl` 앞에 **STEP0·1 과 seed 칸**을 더 붙인 것뿐이다.
#       게이트 뒤쪽(2·3-1·propose·gate:weight·3-2·4)은 `hitl` 과 **같은 배열**이고
#       같은 `_proc_of` 를 탄다. 다르게 짜면 "hitl 로는 되는데 full 은 다른 값"이 나온다.
#       뒤로도 **두 칸**이 더 붙는다 — `load-audit`(reviewed → audit_rules)와
#       `load`(Top-N → booth_candidates). 화면5 는 두 테이블을 다 읽는다:
#       앞이 토론의 근거, 뒤가 논의 대상 목록이다. 하나만 넣으면 목록은 뜨는데
#       토론이 첫 줄에서 죽는다(2026-08-10 실측).
#       근거를 먼저 넣는다 — 순서상 의존은 없지만, 목록이 먼저 보이면 사람이
#       고를 수 있는데 눌러도 안 되는 구간이 생긴다.
_PLAN: dict[str, tuple[str, ...]] = {
    MODE_FIXTURE: ("2", "3-1", "3-2", "4", "load-audit", "load"),
    MODE_HITL: ("gate:audit", "2", "3-1", "propose", "gate:weight", "3-2", "4",
                "load-audit", "load"),
    MODE_FULL: ("0-1", "seed", "gate:audit", "2", "3-1", "propose",
                "gate:weight", "3-2", "4", "load-audit", "load"),
}

GATE_IDS = ("audit", "weight")

# 「고속 자동 분석」이 STEP1 산출물에 적는 출처. `human_confirmed` 자리에 들어간다.
# 🔴 `human` 도 `llm` 도 아닌 **따로 만든 낱말**인 이유 — 이 자리의 기존 값은
#    「조항 문자열」이거나 리터럴 `human_confirmed` 둘뿐이라(audit.py:70),
#    `llm` 처럼 짧은 낱말을 넣으면 조례 출처처럼 읽힌다. STEP3 쪽 어휘
#    (`run_weight_model.SRC_RADIUS["llm"]`)와 굳이 같게 맞추지 않는다:
#    두 자리는 뜻이 다르다(여긴 배제반경의 근거, 저긴 값의 출처).
AUTO_APPROVE_SRC = "llm_auto_approved"


def _resume_index(mode: str, gate_id: str) -> int:
    """`gate.id` 로 이어갈 위치를 계획에서 되찾는다.

    status.json 에 '어디까지 했나' 필드를 새로 두지 않는다 — 계약 3절의 스키마를
    늘리지 않으려는 것도 있지만, 그보다 **같은 사실을 두 곳에 적으면 갈리기** 때문이다.
    계획은 고정 배열이고 게이트 id 는 그 안에서 유일하므로 위치는 유도된다.
    """
    plan = _PLAN[mode]
    return plan.index(f"gate:{gate_id}") + 1

