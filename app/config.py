# -*- coding: utf-8 -*-
"""
OmniSite — 설정 (config)
========================
FastAPI 서버 설정(Settings) + 감리 AI(gam2) 파이프라인 설정을 함께 둔다.

원칙
  - 비밀값(API 키)은 코드에 두지 않는다 → .env 에서 로드.
  - 경로는 BASE_DIR(프로젝트 루트) 기준 절대경로. 실행 위치(cwd)에 의존하지 않는다.
  - "지역 상수"(용산 등)를 코드에 두지 않는다. 지역 판정은 전적으로 경계 SHP
    공간조인(SIGUNGU_NM·ADM_NM)이 담당한다 → 새 자치구를 넣어도 코드 수정 0.
    (구버전의 DISTRICT_BBOX 폴백은 제거됨: filter_bbox op 삭제 + validate_geocode 폴리곤화)
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# ── .env 로드 (python-dotenv 있으면 사용, 없으면 os.environ 직접) ──
try:
    from dotenv import load_dotenv

    load_dotenv()  # 같은 폴더의 .env 를 읽어 환경변수로
except ImportError:
    pass  # dotenv 미설치 시 시스템 환경변수만 사용


# ══════════════════════════════════════════════════════════════════
# 1. 비밀값 — .env 에서 로드 (코드/설정에 값 자체는 두지 않음)
# ══════════════════════════════════════════════════════════════════
VWORLD_KEY = os.environ.get("VWORLD_KEY", "")
DATA_GO_KR_KEY = os.environ.get("DATA_GO_KR_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
LAW_GO_KR_OC = os.environ.get("LAW_GO_KR_OC", "")  # 법제처 국가법령정보 OC값(조례 취득)


# ══════════════════════════════════════════════════════════════════
# 1b. 감리 AI 판정 LLM
# ══════════════════════════════════════════════════════════════════
# 판정 테스트에 쓸 모델. 먼저 mini 로 돌려 적중률이 충분하면 유지(비용),
# 부족하면 "gpt-4o" 로 이 값만 바꾸면 된다. 환경변수로도 덮어쓸 수 있음.
AUDIT_LLM_MODEL = os.environ.get("AUDIT_LLM_MODEL", "gpt-4o")

# 시설명 확정용 모델. 사용자 입력+데이터명 종합은 단순 작업이라 mini(비용 절감).
FACILITY_LLM_MODEL = os.environ.get("FACILITY_LLM_MODEL", "gpt-4o-mini")

# 배제반경 서핑용 모델(web_search). 검색·추출이라 mini 로 충분(비용).
SEARCH_LLM_MODEL = os.environ.get("SEARCH_LLM_MODEL", "gpt-4o-mini")


# ══════════════════════════════════════════════════════════════════
# 2. 외부 API 엔드포인트
# ══════════════════════════════════════════════════════════════════
# 브이월드 지오코딩/역지오코딩 공통 엔드포인트.
# (API 주소는 제공기관 사정으로 바뀔 수 있어 값으로 분리)
VWORLD_ENDPOINT = "https://api.vworld.kr/req/address"


# ══════════════════════════════════════════════════════════════════
# 3. 파일 경로  (BigProject_Back 구조)
# ══════════════════════════════════════════════════════════════════
# 이 파일 위치: BigProject_Back/app/config.py  →  BASE_DIR = 부모의 부모
# ⚠ 상대경로("./data")는 실행 위치(cwd)에 따라 깨진다(FastAPI 는 보통 루트에서 기동).
#   루트 기준 절대경로로 고정해 어디서 실행하든 동일하게 동작시킨다.
BASE_DIR = Path(__file__).resolve().parent.parent  # …/BigProject_Back

# 감리(gam2) 데이터 루트 — 도메인 폴더·산출물·캐시가 모두 이 아래에 모인다.
DATA_ROOT = Path(os.environ.get("OMNISITE_DATA_ROOT", str(BASE_DIR / "data_임시")))

# 도메인 폴더(흡연·EV·재활용)의 부모. domain_paths() 가 여기서 도메인을 찾는다.
#   예: data_임시/흡연/{data,law,fixture}
DOMAIN_ROOT = DATA_ROOT

# 공용 지역 데이터(경계 SHP 등) — 도메인 무관 공유
REGION_DATA_DIR = DATA_ROOT / "region_data"
DATA_DIR = str(REGION_DATA_DIR)  # (구 이름 호환)

# 경계 폴리곤 SHP (센서스경계, 국가데이터처) — 세 파일은 같은 기준일 세트로 유지할 것.
#   spatial_join_admin 이 좌표에 지역을 붙이는 핵심 입력이다.
#   ADM_DONG : ADM_CD(행정동 8자리)·ADM_NM(행정동명 '이촌1동')
#   SIGUNGU  : SIGUNGU_CD(5자리 '11030')·SIGUNGU_NM(자치구명 '용산구')
#              ★ 자치구명이 행정동 경계에는 없어서 반드시 함께 필요.
#                (없으면 자치구 필터가 0행이 된다)
#   SIDO     : 현재 미사용. 광역 단위 확장 대비 보관.
ADM_DONG_SHP = str(REGION_DATA_DIR / "BND_ADM_DONG_PG.shp")
SIGUNGU_SHP = str(REGION_DATA_DIR / "BND_SIGUNGU_PG.shp")
SIDO_SHP = str(REGION_DATA_DIR / "BND_SIDO_PG.shp")

# 행자부 행정동코드 ↔ 시군구명 매핑 (감리 AI 가 추측한 지역코드를 검증)
# 시트 '행정동코드': 통계청행정동코드 · 행자부행정동코드 · 시도명 · 시군구명 · 행정동명
ADM_CODE_MAP = str(REGION_DATA_DIR / "행정동코드_매핑정보_20241218.xlsx")

# 감리 AI 산출물 폴더(감리 결과 JSON)
STEP1_OUTPUT_DIR = os.environ.get("OMNISITE_STEP1_DIR", str(DATA_ROOT / "step1_output"))

# 정제(STEP 2) 산출물 — 정제 데이터 gpkg/csv + clean_report.json. 없으면 코드가 생성.
STEP2_OUTPUT_DIR = os.environ.get("OMNISITE_STEP2_DIR", str(DATA_ROOT / "step2_output"))

# 캐시 폴더(배제반경 등 재사용 캐시). 결과물과 분리 관리.
SEARCH_CACHE_DIR = os.environ.get("OMNISITE_CACHE_DIR", str(DATA_ROOT / "search_cache"))
EXCLUSION_CACHE_PATH = os.path.join(SEARCH_CACHE_DIR, "exclusion_radius_cache.json")

# 조례 폴더 — 기본은 각 도메인의 law/ (domain_paths). 아래는 도메인 미설정 시 폴백.
# 추후 DB/프론트 전환 시 load_ordinance() 에서 이 부분만 대체.
ORDINANCE_DIR = os.environ.get("OMNISITE_ORDINANCE_DIR", str(DATA_ROOT / "law"))
LAW_DIR = os.environ.get("OMNISITE_LAW_DIR", str(DATA_ROOT / "law"))


# ══════════════════════════════════════════════════════════════════
# 4. 좌표계 (CRS)
# ══════════════════════════════════════════════════════════════════
# 공간조인 기준 좌표계. 경계 SHP·연속지적도(D2)가 모두 EPSG:5186 이라 통일.
# 점 데이터(보통 4326)를 이 좌표계로 변환한 뒤 조인한다.
SPATIAL_CRS = 5186
# 지도 표출용(Mapbox 등)은 4326. 최종 결과만 이 좌표계로 되돌린다.
DISPLAY_CRS = 4326


# ══════════════════════════════════════════════════════════════════
# 5. CSV 로딩
# ══════════════════════════════════════════════════════════════════
# 한국 공공데이터 인코딩이 제각각이라 순차 시도한다. 앞에서부터 성공하면 채택.
# 새 인코딩을 만나면 여기에 추가하면 된다(코드 수정 불필요).
CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr")

# profile() 이 좌표 컬럼을 자동 탐지할 때 훑는 후보 이름들.
# 데이터마다 좌표 컬럼명이 달라서 목록으로 관리(새 컬럼명은 여기에 추가).
COORD_COL_CANDIDATES = (
    "위도",
    "경도",
    "lat",
    "lng",
    "X좌표",
    "Y좌표",
    "시설 위도(좌표값)",
    "시설 경도(좌표값)",
)


# ══════════════════════════════════════════════════════════════════
# 6. 지오코딩 호출 간격 (과호출 방지)
# ══════════════════════════════════════════════════════════════════
# 브이월드 API 호출 사이 대기(초). 키 등급/상황에 따라 조정.
GEOCODE_SLEEP_SEC = 0.3  # run_geocode (주소→좌표)
REVERSE_GEOCODE_SLEEP_SEC = 0.2  # reverse_geocode (좌표→시군구, 폴백)


# ══════════════════════════════════════════════════════════════════
# 7. 도메인 폴더 규약 (다중 도메인 — 폴더만 갈아끼우기)
# ══════════════════════════════════════════════════════════════════
# 각 도메인은 하나의 루트 폴더로 자기완결 (DOMAIN_ROOT = data_임시/ 아래):
#   data_임시/<도메인>/            예: data_임시/흡연, data_임시/EV
#     ├── data/        원본 csv·xlsx·shp + _manifest.json (프로파일 대상)
#     ├── law/         해당 도메인 조례 txt (real 에서 전 데이터셋 주입)
#     └── fixture/     gam2_profile.py 산출 profiles.json (간소화 프로파일)
# 산출물·캐시는 STEP1_OUTPUT_DIR·SEARCH_CACHE_DIR 아래 '도메인 프리픽스'로 구분.
#   (실행 시 도메인 인자는 짧은 이름으로: `... gam2_run_pipeline 흡연 "..."`)
DATA_SUBDIR = "data"
LAW_SUBDIR = "law"
FIXTURE_SUBDIR = "fixture"
PROFILES_NAME = "profiles.json"

# ⚠️ 행정동경계 SHP 는 도메인마다 같은 전국 경계라 '공용'으로 한 곳에만 둔다.
#    각 도메인 data/ 에 넣지 말 것. spatial_join_admin 이 이 공용 경로를 참조.
#    (위 3절의 ADM_DONG_SHP·SIGUNGU_SHP = data_임시/region_data/*.shp)
COMMON_ADM_DONG_SHP = ADM_DONG_SHP


def domain_prefix(domain_dir: str) -> str:
    """'EV_데이터셋/' → 'EV'. 접미사 '_데이터셋' 제거해 산출물 프리픽스로."""
    base = os.path.basename(os.path.normpath(str(domain_dir)))
    return base.replace("_데이터셋", "")


def resolve_domain_dir(domain: str) -> str:
    """도메인 인자 → 실제 폴더 경로.
    짧은 이름('흡연')이면 DOMAIN_ROOT(data_임시) 아래에서 찾는다.
    이미 존재하는 경로를 직접 주면 그대로 사용(하위호환·테스트).
    """
    p = Path(domain)
    if p.exists():  # 전체/상대 경로를 직접 준 경우
        return str(p)
    return str(DOMAIN_ROOT / domain)  # data_임시/흡연


def domain_paths(domain_dir: str) -> dict:
    """도메인 루트 폴더 → 하위 경로·프리픽스 묶음. 경로를 코드에 박지 않고 여기서 파생."""
    root = resolve_domain_dir(domain_dir)
    return {
        "prefix": domain_prefix(root),
        "root": root,
        "data": os.path.join(root, DATA_SUBDIR),
        "law": os.path.join(root, LAW_SUBDIR),
        "fixture": os.path.join(root, FIXTURE_SUBDIR),
        "profiles": os.path.join(root, FIXTURE_SUBDIR, PROFILES_NAME),
    }


# 서버 설정
class Settings(BaseSettings):
    # API 및 서버 기본 설정
    PROJECT_NAME: str = "OmniSite FastAPI Monolith"
    API_V1_STR: str = "/api/v1"

    # 데이터베이스 설정 (로컬 sqlite 메모리를 fallback으로 셋업하여 CI 환경 대응)
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/omnisite"
    )
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # AI 및 외부 연동 API 설정
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    KAKAO_REST_API_KEY: str = os.getenv("KAKAO_REST_API_KEY", "")
    VWORLD_API_KEY: str = os.getenv("VWORLD_API_KEY", "")

    # 보안 및 JWT 인증 설정
    SECRET_KEY: str = os.getenv("SECRET_KEY", "SUPER_SECRET_TOKEN_OMNISITE_2026_KEY")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 1주일

    # pydantic_settings v2 규격 설정
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
