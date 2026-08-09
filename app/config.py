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
VWORLD_KEY = os.environ.get("VWORLD_KEY") or os.environ.get("VWORLD_API_KEY", "")
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
_target_region = DATA_ROOT / "region_data"
REGION_DATA_DIR = _target_region if _target_region.exists() else (BASE_DIR / "data_임시" / "region_data")
DATA_DIR = str(REGION_DATA_DIR)  # (구 이름 호환)

# 국유·공유 재산 — 후보 필지에 '국유 지분' 정보를 붙이는 데 쓴다(점수 아님, 실행축).
#   ⚠️ 이 파일은 **지오코딩 산출물**이다. 원본(k-pis.go.kr)에는 좌표가 없다.
#      출처: https://www.k-pis.go.kr/selectBasSerList.do
#   🔴 **이 경로는 최후 폴백이다.** 국유부동산은 지자체별 파일이라 실제 위치는
#      region_data/<지자체>/국유부동산*.{csv,xls} 이며, make_parcel_candidates 가
#      find_region_file() 로 시군구코드·폴더명으로 찾는다. 여기 평면 경로에는
#      파일이 없는 것이 정상이다(2026-08-02 지역 하위폴더 도입).
#      → 폴백까지 내려오면 attach_ownership 이 '지분 태그 생략' 경고를 남긴다.
#   ⚠️ 지역별 데이터이므로 **사용자 업로드 → 온디맨드 지오코딩**으로 전환 예정.
#      전환 시 이 상수는 폴백(기본 샘플)로만 남는다. → GEOCODE_CACHE_DIR 참조
NATIONAL_PROPERTY_CSV = str(REGION_DATA_DIR / "국유부동산_위경도_v2.csv")

# 경계 폴리곤 SHP (센서스경계, 국가데이터처) — 세 파일은 같은 기준일 세트로 유지할 것.
#   spatial_join_admin 이 좌표에 지역을 붙이는 핵심 입력이다.
#   ADM_DONG : ADM_CD(행정동 8자리, **통계청 행정구역분류코드**)·ADM_NM('이촌1동')
#   SIGUNGU  : SIGUNGU_CD(5자리 '11030', **통계청**)·SIGUNGU_NM('용산구')
#              ★ 자치구명이 행정동 경계에는 없어서 반드시 함께 필요.
#                (없으면 자치구 필터가 0행이 된다)
#   SIDO     : 현재 미사용. 광역 단위 확장 대비 보관.
#
#   ⚠️ 통계청 코드는 행자부 코드와 **다르다.** 같은 접두가 다른 구를 가리킨다:
#        11170  행자부=용산구  통계청=구로구
#        11140  행자부=중구    통계청=마포구
#        11110  행자부=종로구  통계청=노원구      (전국 57개 접두가 겹침)
#      생활인구 등 **통계표와 조인하려면 반드시 ADMIN_CROSSWALK_PATH 를 경유**할 것.
#      뒤 3자리 매칭은 우연히 맞는 경우가 섞여 있어 쓰면 안 된다(용산 실측 12/16).
ADM_DONG_SHP = str(REGION_DATA_DIR / "BND_ADM_DONG_PG.shp")
SIGUNGU_SHP = str(REGION_DATA_DIR / "BND_SIGUNGU_PG.shp")
SIDO_SHP = str(REGION_DATA_DIR / "BND_SIDO_PG.shp")

# 행정동 코드 크로스워크(**전국 3,555동**) — 감리 AI 가 추측한 지역코드를 검증하는 1순위 표.
#   컬럼: 행정구역코드(통계청) · 행정동코드(행자부10) · 행정동코드8 · 행정동명 · 시도명 · 시군구명
#   ⚠️ 여기에 선언이 없으면 `getattr(config, "ADMIN_CROSSWALK_PATH", "")` 로 읽는 쪽이
#      **파일이 있는데도 빈 문자열을 받아** 엑셀 폴백(서울 한정)으로 내려간다.
#      실제로 그렇게 돼서 04 생활인구가 `unknown — 코드표 없음` 으로 떴다(2026-08-03).
ADMIN_CROSSWALK_PATH = os.environ.get(
    "OMNISITE_ADMIN_CROSSWALK", str(REGION_DATA_DIR / "행정동_크로스워크.csv")
)

# 엑셀 폴백(**서울 424동 한정**). 크로스워크가 없을 때만 쓴다.
# 시트 '행정동코드': 통계청행정동코드 · 행자부행정동코드 · 시도명 · 시군구명 · 행정동명
#   🔴 **현재 저장소에 이 파일은 없다 — 실질적으로 죽은 폴백이다.**
#      "폴백이 있으니 괜찮겠지" 로 오독하지 말 것. 크로스워크가 1순위이자 사실상 유일하다.
#      서울 밖 도메인에서는 이 표로 내려가면 전부 unknown 이 되어 HITL 만 늘어난다.
ADM_CODE_MAP = str(REGION_DATA_DIR / "행정동코드_매핑정보_20241218.xlsx")

# 감리(STEP 1) AI 산출물 폴더(감리 결과 JSON)
STEP1_OUTPUT_DIR = os.environ.get("OMNISITE_STEP1_DIR", str(DATA_ROOT / "step1_output"))

# 정제(STEP 2) 산출물 — 정제 데이터 gpkg/csv + clean_report.json. 없으면 코드가 생성.
STEP2_OUTPUT_DIR = os.environ.get("OMNISITE_STEP2_DIR", str(DATA_ROOT / "step2_output"))

# 가중치 모델(STEP 3) 산출물 — weight_set.json (감리·정제 → 최종 가중치)   ← 추가
STEP3_OUTPUT_DIR = os.environ.get("OMNISITE_STEP3_DIR", str(DATA_ROOT / "step3_output"))

# 위치선정(STEP 4) 산출물 — Top-N·점수면·배제구역. 표출은 DISPLAY_CRS(4326).
STEP4_OUTPUT_DIR = os.environ.get("OMNISITE_STEP4_DIR", str(DATA_ROOT / "step4_output"))

# 캐시 폴더(배제반경 등 재사용 캐시). 결과물과 분리 관리.
_target_cache = DATA_ROOT / "search_cache"
_fallback_cache = BASE_DIR / "data_임시" / "search_cache"
SEARCH_CACHE_DIR = os.environ.get(
    "OMNISITE_CACHE_DIR",
    str(_target_cache if _target_cache.exists() else _fallback_cache)
)
EXCLUSION_CACHE_PATH = os.path.join(SEARCH_CACHE_DIR, "exclusion_radius_cache.json")
# 지목 판정 캐시(시설별). 지목 부호는 법정 표준이라 지적도가 갱신돼도 유지된다.
JIMOK_CACHE_PATH = os.path.join(SEARCH_CACHE_DIR, "jimok_role_cache.json")
# 시설 물리 파라미터 캐시(시설별) — 설치폭·서비스반경·최소이격.
FACILITY_PARAM_CACHE_PATH = os.path.join(SEARCH_CACHE_DIR, "facility_params_cache.json")
# 지오코딩 결과 캐시 — 공유지/국유지 CSV 는 주소만 있어 Vworld 호출이 필요하다.
#   호출당 GEOCODE_SLEEP_SEC(0.3초) 대기가 걸려 2,486건이면 약 12분이다.
#   같은 지역을 다시 돌릴 때 재호출하지 않도록 주소 단위로 캐시한다.
GEOCODE_CACHE_DIR = os.path.join(SEARCH_CACHE_DIR, "geocode")

# 조례 폴더 — 기본은 각 도메인의 law/ (domain_paths). 아래는 도메인 미설정 시 폴백.
# 추후 DB/프론트 전환 시 load_ordinance() 에서 이 부분만 대체.
ORDINANCE_DIR = os.environ.get("OMNISITE_ORDINANCE_DIR", str(DATA_ROOT / "law"))
LAW_DIR = os.environ.get("OMNISITE_LAW_DIR", str(DATA_ROOT / "law"))


# ── 참조 데이터 존재 점검 ─────────────────────────────────────────
#   왜 필요한가 (2026-08-03)
#     config 가 가리키는 경로에 파일이 없으면 파이프라인은 **멈추지 않고 기능만
#     조용히 꺼진다.** 크로스워크가 없던 실행에서 코드 검증이 통째로 비활성화됐고,
#     그 사실은 gpt-4o 11회(142초)를 다 쓴 뒤 HITL 화면에서야 드러났다.
#     실행 **전에** 한 번 훑으면 그 비용을 안 치른다.
#
#   import 시점에는 아무 일도 하지 않는다 — 부르는 쪽이 언제 점검할지 정한다.
#   (FastAPI 기동 로그를 오염시키지 않기 위해서다)
REFERENCE_FILES = {
    "행정동 경계 SHP":       (ADM_DONG_SHP, True),
    "시군구 경계 SHP":       (SIGUNGU_SHP, True),
    "행정동 크로스워크":      (ADMIN_CROSSWALK_PATH, True),
    "엑셀 코드표(죽은 폴백)":  (ADM_CODE_MAP, False),
    "국유부동산(최후 폴백)":   (NATIONAL_PROPERTY_CSV, False),
}


def missing_reference_files(required_only: bool = False) -> list:
    """참조 데이터 중 **실제로 없는** 파일 목록. [(설명, 경로, 필수여부), ...]

    지적도(LSMD_CONT_LDREG)·지자체별 국유부동산은 여기 넣지 않는다 —
    지역마다 파일명이 달라 find_region_file() 이 시군구코드로 찾는 대상이다.
    """
    out = []
    for label, (path, required) in REFERENCE_FILES.items():
        if required_only and not required:
            continue
        if not path or not os.path.isfile(str(path)):
            out.append((label, str(path or ""), required))
    return out


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
GEOCODE_SLEEP_SEC = 0.3  # 재시도 백오프 단위 (S6 이후 직렬 대기 용도로는 쓰지 않는다)
REVERSE_GEOCODE_SLEEP_SEC = 0.2  # reverse_geocode (좌표→시군구, 폴백)

# 지오코딩 병렬 호출(S6). 직렬 sleep 을 토큰버킷 + 스레드풀로 대체한다.
#   도메인 값이 아니라 **API 사용 한도**라 코드 상수로 두는 것이 맞다(절대원칙 2 예외).
#   🔴 Vworld 는 일일 4만 건만 공표하고 **초당 한도는 문서에 없다.** 아래 10 req/s 는
#      근거 있는 상한이 아니라 실측 시작값이다. 차단당하면 낮춘다.
GEOCODE_RATE_LIMIT = float(os.environ.get("OMNISITE_GEOCODE_RATE", "10"))  # req/s
GEOCODE_MAX_WORKERS = int(os.environ.get("OMNISITE_GEOCODE_WORKERS", "6"))


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


# 후보 필지 gpkg (make_parcel_candidates.py 산출물) — STEP3·4 공통 입력.
#   ⚠️ region_data 가 아니라 step3_output 에 둔다. region_data 는 **원본·참조 데이터
#     전용**이며, 생성물이 섞이면 DB 적재 대상을 가릴 때 헷갈린다.
#   ⚠️ 도메인 프리픽스 필수 — 후보 집합은 **지목 판정(시설별)에 의존**한다.
#     프리픽스가 없으면 흡연으로 만든 후보를 재활용 도메인이 그대로 쓴다(조용한 오염).
CANDIDATE_GPKG_NAME = "후보_지적도필지.gpkg"


def candidate_gpkg_path(domain: str) -> str:
    """도메인별 후보 gpkg 경로. 예: step3_output/흡연_후보_지적도필지.gpkg"""
    p = domain_prefix(domain)
    return os.path.join(STEP3_OUTPUT_DIR,
                        f"{p + '_' if p else ''}{CANDIDATE_GPKG_NAME}")


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
