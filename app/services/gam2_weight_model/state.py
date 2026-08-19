# -*- coding: utf-8 -*-
"""도메인 컨텍스트 — config 조달 · 상수"""
from __future__ import annotations

import os

# --- config (설치 환경) ---
# 이 파일은 app/services/ 에 있으므로 app.config 를 절대경로로 임포트.
try:
    from app.config import (
        ADM_DONG_SHP,
        OPENAI_API_KEY,
        SEARCH_LLM_MODEL,
        STEP2_OUTPUT_DIR,
        SPATIAL_CRS,
    )

    # 가중치 산출물은 정제(step2)와 섞지 않고 step3_output 에 둔다.
    # config 에 STEP3_OUTPUT_DIR 이 있으면 그걸 쓰고, 없으면 step2 옆에 파생.
    try:
        from app.config import STEP3_OUTPUT_DIR as WEIGHT_OUTPUT_DIR
    except Exception:
        WEIGHT_OUTPUT_DIR = os.path.join(
            os.path.dirname(STEP2_OUTPUT_DIR), "step3_output"
        )
    # 공용 지역 데이터 루트. find_region_file() 의 기본 탐색 경로다.
    #   예전엔 크로스워크 폴백 안에서만 `_RD` 로 잡혀서, config 에 ADMIN_CROSSWALK_PATH 가
    #   있으면 **아예 바인딩되지 않았다** → find_region_file(root=None) 이 NameError.
    #   모듈 스코프에서 항상 잡는다.
    try:
        from app.config import REGION_DATA_DIR
    except Exception:
        REGION_DATA_DIR = os.path.join(os.path.dirname(STEP2_OUTPUT_DIR), "region_data")
    REGION_DATA_DIR = str(REGION_DATA_DIR)  # config 는 Path — glob 에 문자열로 넘긴다
    # 행정동 코드 크로스워크(참조 데이터). config 에 없으면 region_data 에서 찾는다.
    try:
        from app.config import ADMIN_CROSSWALK_PATH
    except Exception:
        ADMIN_CROSSWALK_PATH = os.path.join(REGION_DATA_DIR, "행정동_크로스워크.csv")
except Exception:  # 단독 실행/테스트 폴백
    ADM_DONG_SHP = os.environ.get("ADM_DONG_SHP", "")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    SEARCH_LLM_MODEL = "gpt-4o-mini"
    STEP2_OUTPUT_DIR = os.environ.get("STEP2_OUTPUT_DIR", ".")
    WEIGHT_OUTPUT_DIR = os.environ.get("STEP3_OUTPUT_DIR", "./step3_output")
    ADMIN_CROSSWALK_PATH = os.environ.get(
        "ADMIN_CROSSWALK_PATH", "./행정동_크로스워크.csv"
    )
    REGION_DATA_DIR = os.environ.get("REGION_DATA_DIR", "./region_data")
    SPATIAL_CRS = 5186

WORK_CRS = SPATIAL_CRS  # 미터 단위 작업 좌표계 (거리·버퍼) — config 와 통일


_ADM_CODE_COL = "ADM_CD"  # TODO(2): 경계 SHP 의 행정동코드 컬럼명
SPARSE_THRESHOLD = 0.05  # 비영 비율 5% 미만 -> CRITIC 제외


# =========================================================
# [A] 지표 정의
# =========================================================
# 크기 미정(`weight: null`)일 때의 슬라이더 초기 위치.
#   도메인 상수가 아니다 — 어떤 시설·지표에도 의미가 없는 중립점이고,
#   [W] 에서 사람이 확정하기 전까지의 자리표시일 뿐이다.
#   STEP1 HITL 이 방향만 확정하도록 바뀌면서(2026-07-31) 필요해졌다.
NEUTRAL_SEED = 0.5
