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
  1) build_prompt(profile)     : 시스템+유저 프롬프트 조립(카탈로그 13개 동적 주입)
  2) LLMClient / MockLLM       : 호출 인터페이스 + 목 구현
  3) score_one / run_harness   : 채점 + 리포트
  4) build_fixtures(폴더)      : profile.py 로 실제 파일을 읽어 프로파일 생성
                                 (--data 로 폴더만 갈아끼우면 다른 도메인도 동작)

의존: audit_ops_catalog·profile·config(같은 폴더)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

# 프로젝트 루트를 sys.path 에 추가 → `python app\services\...` 로 직접 실행해도
#   `app.xxx` 절대 임포트가 된다. (`python -m app.services.…` 는 원래 되지만
#   실행 방식마다 다르게 동작하면 매번 걸린다 — STEP3·4 스크립트와 동일한 보정)
import os as _os
import sys as _sys

_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from app import config
from app.services.gam2_audit_ops_catalog import describe_all
from app.config import STEP1_OUTPUT_DIR

# 🔴 배제반경 캐시는 2026-08-10 제거했다(사람 지시). 예전엔 사람이 한 번 확정한
#    시설유형→반경을 `<prefix>_exclusion_radius_cache.json` 에 적어두고 다음 실행에서
#    **묻지 않고 채웠다.** 자동 진행이 필요한 경우는 이제 `mode:"full"` 이 맡는다 —
#    HITL 은 "사람이 전부 본다"가 뜻의 전부여야 한다. 캐시가 남아 있으면 화면에
#    안 뜨는 항목이 생기고, 그건 사람이 확인한 것처럼 기록된다(원칙 4).


# ── 도메인 컨텍스트 (실행 시 set_domain 으로 채움; 전까지는 기존 기본값) ──
_DOMAIN = {
    "prefix": "",
    "data": None,
    "law": None,
    "fixture": None,
    "profiles": None,
}


def set_domain(domain_dir: str) -> None:
    """도메인 루트 폴더로 경로·프리픽스를 확정. 모든 모드 시작 시 1회 호출."""
    p = config.domain_paths(domain_dir)
    _DOMAIN.update(
        prefix=p["prefix"],
        data=p["data"],
        law=p["law"],
        fixture=p["fixture"],
        profiles=p["profiles"],
    )


def _out_path(name: str) -> str:
    """산출물 경로에 도메인 프리픽스. 예: name='audit_result.json' → EV_audit_result.json"""
    pre = f"{_DOMAIN['prefix']}_" if _DOMAIN["prefix"] else ""
    return os.path.join(STEP1_OUTPUT_DIR, f"{pre}{name}")


# ══════════════════════════════════════════════════════════════════
# 1. 프롬프트 빌더 — 카탈로그를 동적 주입(코드에 op 목록 안 박음)
# ══════════════════════════════════════════════════════════════════

ROLE_ENUM_DOC = """\
[의미 role] — 입지 판단에서의 의미. 한 데이터에 여러 개 공존 가능(리스트).
- positive_factor  : 설치 수요를 높이는 가점 요인 (weight: +값 제안, 0~1)
- negative_factor  : 갈등·민감도를 높이는 감점 요인 (weight: -값 제안, -1~0)
- hard_exclusion   : 조례·법령상 설치 금지. weight 대신 배제반경_m 을 조례에서 추출
- reference_only   : 입지 판정의 입력(가점/감점/배제) 어디에도 해당 안 되는 참조·하류·무관 데이터.
                     예: 연속지적도(후보 좌표의 지목 확인용, 위치선정 뒤 단계)·단순 참고 레이어.
                     억지로 positive/negative/hard_exclusion 을 붙이지 말고 이 역할로 둔다(→ 사람이 용도 확인).
※ 예: 버스정류소 = positive_factor(유동인구) + hard_exclusion(조례 10m) 공존

[좌표 상태] — 위치선정에 필요한 좌표의 유무. 의미 role 과 별개 축.
- has_coords        : 좌표 컬럼이 이미 있음 (그대로 사용)
- needs_geocoding   : 좌표 없고 주소만 있음 → 다음 단계에서 지오코딩 필요
- stat_join         : 좌표 없는 통계. 마스터/경계와 조인·공간조인으로 위치 부여
- spatial           : 폴리곤(경계·지적도) 자체가 공간정보"""

SYSTEM_PROMPT_TEMPLATE = """너는 스마트시티 입지선정 플랫폼 OmniSite의 데이터 감리 AI다.
사용자가 데이터를 넣으면, 그 데이터가 '어디에 쓰일 데이터인지' 판단해서 사람이 확인할 수 있게
정리하고, 다음 단계(지오코딩·정제)가 참고할 지시를 만든다.
이번 선정 대상 시설은 '{facility}' 이다. 모든 역할 판정은 '{facility}' 입지 기준으로 한다.

너의 출력 4가지:
(1) summary   : 이 데이터가 '{facility}' 입지에서 어떤 역할인지 한 줄 요약(사람이 HITL로 확인).
(2) roles     : 입지 판단에서의 의미 role 리스트(공존 가능). positive/negative 는 weight,
                hard_exclusion 은 배제반경_m 을 조례 근거와 함께.
(3) coord_status : 좌표 상태(has_coords/needs_geocoding/stat_join/spatial). 위치선정에 필요.
(4) cleaning_ops : 정제에 필요한 op(카탈로그에 있는 것만). 다음 단계가 실행할 지시서.

철칙:
- 너는 판정만 한다. 데이터를 직접 변환하지 않는다.
- roles 는 '데이터 형식'이 아니라 '입지에서의 의미'로 정한다. 좌표 유무는 roles 가 아니라
  coord_status 에 적는다. (좌표 없는 통계도 의미는 있다 — 예: 승하차인원 = positive_factor 이고 stat_join)
- hard_exclusion 판정 시 배제 유형(exclusion_type)을 함께 정한다:
  · "radius"  : 점 시설에서 일정 거리 배제(버퍼). 예: 버스정류소 10m, 어린이집 30m.
                이 경우 배제반경_m 을 조례/법령에서 추출(없으면 null→HITL).
  · "polygon" : 구역 경계 자체로 배제(면). 예: 도시공원·교육환경보호구역·침수구역.
                구역 안이면 배제하므로 배제반경_m 은 불필요(null). 반경을 지어내지 마라.
  점 시설이면 radius, 면(구역) 데이터면 polygon 으로 판정한다.
- hard_exclusion 이면 facility_type 에 시설 유형명을 넣는다(예: "어린이집",
  "버스정류소", "지하철역", "도시공원"). 이 값은 **배제반경 캐시의 키이자 상위법
  검색어**다 — 틀리면 엉뚱한 법령 조문이 근거로 붙고, 그 근거가 산출물에 남는다.
  · 데이터셋ID·확장자·기관명·파일명 형식(날짜·지자체 접두 등)은 쓰지 마라.
  · 시설 종류 컬럼(시설구분·구분·유형 등)에 값이 여러 개면 **그 중 하나를 고르지 마라.**
    개별 값이 아니라 **그 컬럼 전체를 아우르는 상위 개념**을 쓴다.
    데이터셋 주제 자체가 그 상위 개념이면 그 이름을 그대로 쓰는 것이 맞다.
    (예: 시설구분에 '학교절대보호구역·어린이집·도시공원'이 섞인 금연구역 목록
     → facility_type = "금연구역". "학교절대보호구역" 처럼 표본 값 하나를 집으면
     상위법 검색이 학교 조문을 가져와 이 데이터 전체와 무관한 근거가 된다.)
  · 스스로 검산하라 — "이 이름으로 법령을 검색하면 이 데이터 **전체**에 맞는 조문이
    나오는가?" 한 유형에만 맞는 이름이면 상위 개념으로 한 단계 올려라.
- hard_exclusion 의 배제반경_m 은 exclusion_type=radius 일 때만 조례 근거로 채운다.
- **한 데이터셋의 hard_exclusion 은 1개만 낸다.** 데이터에 시설 종류 컬럼이 있어
  여러 유형(어린이집·초등학교·유치원 등)이 섞여 있어도 나누지 마라.
  배제는 현재 데이터셋 단위로 적용되므로, 유형을 나눠도 행마다 다른 반경을 적용할 수 없다
  (같은 데이터셋이 HITL 에 두 번 올라와 사람만 두 번 묻게 된다).
  이 경우 facility_type 은 위 [상위 개념] 규칙대로 데이터 전체를 대표하는 이름으로 하고
  (예: "어린이보호구역"), 반경은 섞인 유형 중 가장 보수적인(넓은) 값을 쓴다.
- 다음 데이터는 hard_exclusion 이 아니다. 배제로 판정하지 마라:
  · 조례·법령 텍스트(rag_document): 배제 규칙의 '근거 문서'일 뿐, 그 자체가 배제 대상이 아니다.
  · 행정경계·연속지적도 등 공간 기반 데이터: 후보지·범위 정보이지 배제 시설이 아니다(coord_status=spatial).
- 배제(hard_exclusion)는 '시설의 위치(점/구역) 데이터'에만 붙인다. 승하차 인원·생활인구 같은
  통계 데이터(stat_join)에는 배제를 붙이지 마라. 통계는 수요 지표(positive/negative)일 뿐이다.
  (예: 버스정류소 '위치'는 배제 대상일 수 있으나, 버스 '승하차 인원' 통계는 배제가 아니다.)
- [공존] 유동인구 거점(정류소·역·환승센터 등)의 '위치' 데이터는 조례상 배제 대상이면서
  동시에 유동인구=수요 거점이다 → hard_exclusion 과 positive_factor 를 **함께** 붙여라.
  배제로 판정했다고 positive 를 빼지 마라(둘 다 맞으면 둘 다 넣는다).
- [배제 귀속] 특정 시설의 배제(hard_exclusion)는 그 데이터가 '그 시설에 관한' 것일 때만 붙인다
  (그 시설의 위치이거나 그 시설 이용 통계 등, 그 시설이 이 데이터의 '주체'일 때).
  다른 데이터의 상세위치·설명 텍스트에 그 시설이 우연히 등장한다고 그 시설 배제를 갖다붙이지 마라.
  '이 데이터의 주체가 무엇인가'로 판단하라. (예: 가로휴지통 데이터의 상세위치에 "버스정류장"이
  적혀 있어도 주체는 '가로휴지통'이다 → 버스정류소 배제를 붙이면 안 된다. 휴지통은 positive 만.)
- [좌표상태] coord_status 는 반드시 profile 신호로 정한다:
  · has_coord_col=true            → has_coords
  · has_coord_col=false, has_addr_col=true → needs_geocoding (주소만 있으면 지오코딩 대상)
  · 좌표도 주소도 없는 통계        → stat_join
  · 폴리곤(shp)                   → spatial
  주소만 있는 '점 데이터'를 stat_join 으로 판정하지 마라(그건 needs_geocoding 이다).
- weight 는 대략값이다. 사람이 HITL 로 조정하므로 방향(+/-)과 크기 감만 맞으면 된다.
- cleaning_ops 의 op_id 는 operation_catalog 에 있는 것만. profile 근거가 있을 때만 추가.
  (예: null_coords=0 이면 run_geocode 를 넣지 않는다.)
- 지역 판정 기본은 spatial_join_admin(경계 SHP 공간조인, API 0회). 이 op 가 좌표에
  SIGUNGU_NM(자치구명)·ADM_NM(행정동명)을 붙이므로, 대상 자치구 필터는
  filter_by_value(col='SIGUNGU_NM') 로 건다. reverse_geocode 는 경계 SHP 를 못 쓸 때의 폴백.
- 입력 팩터(가점/감점/배제)로 볼 근거가 약하거나 용도가 불분명하면, 억지로 분류하지 말고
  roles=[{{"role":"reference_only", "rationale": "왜 입력 팩터로 보기 어려운지"}}] 로 판정한다.
  (모르면 지어내지 말 것 — reference_only 로 두면 사람이 HITL 에서 의도를 확인한다.)
- 출력은 유효한 JSON 하나만. 설명·마크다운·코드펜스 금지.

{role_enum}"""


def get_system_prompt(facility: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(facility=facility, role_enum=ROLE_ENUM_DOC)


def resolve_facility(user_input: str, fixtures: dict, model: str | None = None) -> dict:
    """사용자 입력 + 데이터명을 종합해 선정 시설(facility)을 확정(mini, 단순 작업).
    입력↔데이터 불일치 시 경고. 반환: {facility, 근거, mismatch, mismatch_reason}.
    이 결과는 hitl 확인 대상(confirmed=false)."""
    from openai import OpenAI
    from app.config import OPENAI_API_KEY, FACILITY_LLM_MODEL

    if not OPENAI_API_KEY:
        raise RuntimeError(".env 에 OPENAI_API_KEY 를 설정하세요.")
    client = OpenAI(api_key=OPENAI_API_KEY)
    m = model or FACILITY_LLM_MODEL

    dataset_names = [f.get("filename", "") for f in fixtures.values()]
    prompt = (
        f"사용자가 입지 선정을 요청했다. 아래 [사용자 입력]과 [데이터 목록]을 종합해 "
        f"'선정하려는 시설(facility)'과 '대상 지역(region)'을 확정하라.\n\n"
        f"[사용자 입력] {user_input}\n"
        f"[데이터 목록] {dataset_names}\n\n"
        f"규칙:\n"
        f"- facility 는 시설명만 짧게(예: '흡연부스', 'EV 충전소', '음식물 쓰레기 수거함'). "
        f"'부지 선정해줘' 같은 요청어는 빼라.\n"
        f"- region 은 **'<시도> <시군구>' 형식**으로(예: '서울특별시 용산구', "
        f"'경상남도 창원시마산합포구'). 조례 검색과 행정코드 검증에 쓰인다.\n"
        f"  시군구명은 전국에서 유일하지 않다(중구·동구·서구·남구·북구 등). "
        f"시도를 빼면 코드 검증이 불가능해지므로 반드시 함께 적어라.\n"
        f"  사용자 입력에 지역이 있으면 그것을, 없으면 데이터 파일명·내용에서 추론하라. "
        f"시도를 알 수 없으면 시군구만 적어라(추측하지 마라).\n"
        f"- 근거를 쓸 때 [데이터 목록]의 실제 파일명을 확인하고 인용하라. 목록에 있는 데이터를 "
        f"'없다'고 하지 마라(예: 담배꽁초·금연구역 파일이 있으면 그것을 근거로 들라).\n"
        f"- 사용자 입력의 시설과 데이터 목록이 안 맞으면(예: 입력은 흡연부스인데 데이터는 전부 EV 관련) "
        f"mismatch=true 로 표시하고 이유를 적어라.\n"
        f"- 사용자 입력이 비었으면 데이터 목록만으로 추론하라.\n"
        f"JSON 하나만 출력(설명 금지):\n"
        f'{{"facility": "<시설명>", "region": "<시도 시군구>", "근거": "<판단 근거>", '
        f'"mismatch": <true|false>, "mismatch_reason": "<불일치 시 이유, 없으면 빈 문자열>"}}'
    )
    resp = client.chat.completions.create(
        model=m,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        out = json.loads(resp.choices[0].message.content)
    except json.JSONDecodeError:
        out = {
            "facility": user_input or "(추론 실패)",
            "region": "",
            "근거": "",
            "mismatch": False,
            "mismatch_reason": "",
        }
    out.setdefault("region", "")
    out["confirmed"] = False  # hitl 확인 전
    out["source_input"] = user_input
    return out


def resolve_facility_mock(
    user_input: str, fixtures: dict, model: str | None = None
) -> dict:
    """mock — 사용자 입력에서 시설명만 대략 추출(웹/키 없이 형식 검증용, 도메인 무관)."""
    fac = (
        user_input.replace("부지 선정해줘", "")
        .replace("입지 선정", "")
        .replace("선정해줘", "")
        .replace("선정", "")
        .strip()
    )
    # 입력에서 '~구/~시/~군' 지역 추출(없으면 빈값)
    mreg = re.search(r"(\S+?[구시군])", user_input)
    region = mreg.group(1) if mreg else ""
    return {
        "facility": fac or "(미지정)",
        "region": region,
        "근거": "(mock)",
        "mismatch": False,
        "mismatch_reason": "",
        "confirmed": False,
        "source_input": user_input,
    }


MAX_PEER_COLS = 15  # 프롬프트 팽창(429) 방지 — peer 당 노출 컬럼 상한


def _peer_summaries(
    fixtures: dict | None, self_id, profile: dict | None = None
) -> list[dict]:
    """다른 데이터셋 요약(조인 짝 판단용). 감리는 데이터셋을 하나씩 보므로,
    이게 없으면 '어느 데이터셋에서 키 목록을 가져와야 하는지'를 알 수 없다.
    (실제로 지하철·버스 승하차 통계가 지역 필터를 못 걸어 0행/전체통과가 났다)

    ※ 좌표나 주소가 있어 스스로 지역을 좁힐 수 있는 데이터셋에는 주입하지 않는다.
      필요 없는데도 넣으면 프롬프트가 커져 TPM 한도(429)를 유발한다."""
    if not fixtures:
        return []
    if profile is not None and (
        profile.get("has_coord_col") or profile.get("has_addr_col")
    ):
        return []  # 자체 필터 가능 → 조인 짝 정보 불필요
    out = []
    for did, pf in fixtures.items():
        if str(did) == str(self_id):
            continue
        # 조인 '생산자'가 될 수 있는 데이터셋만 넣는다 = 스스로 지역을 좁힐 수 있는 것
        # (좌표 또는 주소 보유). 통계표끼리는 서로 도움이 안 되므로 제외.
        #   ※ 전부 넣으면 프롬프트가 데이터셋 수만큼 불어나 TPM 한도(429)에 걸린다.
        if not (pf.get("has_coord_col") or pf.get("has_addr_col")):
            continue
        cols = list(pf.get("columns") or [])
        out.append(
            {
                "dataset_id": did,
                "filename": pf.get("filename"),
                # 키가 될 만한 컬럼만 보이면 되므로 상한을 둔다(수치 통계 컬럼이 수십 개인 경우 대비)
                "columns": cols[:MAX_PEER_COLS]
                + (["…"] if len(cols) > MAX_PEER_COLS else []),
                "has_coord_col": pf.get("has_coord_col"),
            }
        )
    return out


def build_prompt(
    profile: dict,
    domain: dict,
    ordinance_rag: list[str] | None = None,
    fixtures: dict | None = None,
) -> dict:
    """시스템+유저 프롬프트 조립. 카탈로그는 describe_all()로 동적 주입.
    조례는 profile에 실린 것을 우선 사용(데이터셋별 주입), 인자로도 덮어쓸 수 있음.
    fixtures 를 주면 다른 데이터셋 스키마를 함께 보여준다(조인 짝 판단용)."""
    if ordinance_rag is not None:
        ordinance = ordinance_rag
    elif profile.get("ordinance"):
        # 조례 전문을 데이터셋마다 통째로 실으면 프롬프트가 급격히 커진다
        #   (성동구 폐기물 조례 전문 적용 시 감리 66초 -> 4분 37초, 데이터셋 9개).
        #   규제성 조문(거리·금지·열거)과 그 참조 조문만 발췌한다.
        #   ※ 배제반경 추출(STEP2)은 전문을 쓴다 — 거긴 호출이 배제 건수뿐이다.
        try:
            from app.services.gam2_ordinance_select import select_articles, keywords_of

            ordinance = [select_articles(profile["ordinance"], keywords_of(profile))]
        except Exception as e:
            print(f"  ⚠ 조례 발췌 생략({e}) — 전문 사용")
            ordinance = [profile["ordinance"]]
    else:
        ordinance = []
    facility = domain.get("facility", "대상 시설")
    user = {
        "domain_context": domain,  # {facility, region}
        "dataset": {
            "dataset_id": profile.get("dataset_id"),
            "filename": profile.get("filename"),
            "extension": profile.get("extension"),
            "schema": profile.get("columns"),
            "sample_rows": profile.get("sample_rows", []),
            # 저카디널리티 컬럼의 **값 분포 전체**. sample_rows(2행)로는 보이지 않는
            #   드문 값까지 들어 있다. filter_by_value 의 allowed 는 여기서 고른다.
            "value_dist": profile.get("value_dist", {}),
            "profile": {
                k: profile[k]
                for k in (
                    "row_count",
                    "has_coord_col",
                    "coord_cols",
                    "has_addr_col",
                    "addr_cols",
                    "null_coords",
                    "dup_estimate",
                )
                if k in profile
            },
        },
        "ordinance_rag": ordinance,
        # 다른 데이터셋 스키마 — 이 데이터셋만으로 지역을 못 좁힐 때(좌표·자치구명 없음)
        # 어느 데이터셋에서 emit_whitelist 로 키 목록을 만들지 판단하는 데 쓴다.
        "other_datasets": _peer_summaries(fixtures, profile.get("dataset_id"), profile),
        "operation_catalog": describe_all(),
        "output_schema": {
            "dataset_id": "str",
            "summary": f"이 데이터가 '{facility}' 입지에서 어떤 역할인지 한 줄(사람 확인용)",
            "roles": [
                {
                    "role": "positive_factor|negative_factor",
                    "weight": "float(-1~1, 대략값)",
                    "rationale": "str",
                },
                {
                    "role": "hard_exclusion",
                    "exclusion_type": "radius|polygon",
                    "facility_type": "시설 유형명(배제반경 캐시 키 + 상위법 검색어). "
                    "시설 종류 컬럼에 여러 값이 섞였으면 개별 값이 "
                    "아니라 상위 개념. 예: 어린이집·버스정류소·금연구역",
                    "배제반경_m": "int|null(radius이고 조례에 있으면 숫자, polygon이면 null)",
                    "source": "조례 조항|null",
                    "confirmed": "bool(조례근거 있으면 true)",
                    "need_review": "bool(radius인데 조례에 반경 없으면 true→HITL)",
                    "rationale": "str",
                },
                {"role": "reference_only", "rationale": "입력 팩터로 보기 어려운 이유"},
            ],
            "coord_status": "has_coords|needs_geocoding|stat_join|spatial",
            "cleaning_ops": [{"op_id": "<카탈로그 내 값>", "params": {}}],
            "hitl_flags": [],
        },
        # cleaning_ops 작성 규칙 — 실제 실패 사례에서 도출. 위반하면 결과가 조용히 틀린다.
        "cleaning_ops_rules": [
            "op_id 는 operation_catalog 에 있는 값만 쓴다. 없는 op 를 새로 만들지 마라 "
            "(만들면 그 op 는 실행되지 않고 건너뛴다).",
            "각 op 의 params 는 params_schema 의 필수 항목을 반드시 채운다. "
            "특히 좌표 op 의 coord_cols 는 [경도컬럼, 위도컬럼] 순서로 실제 컬럼명을 쓴다.",
            "params 의 컬럼명은 위 dataset.schema 에 실제로 있는 이름만 쓴다.",
            "대상 자치구로 좁힐 때: 자치구명 컬럼이 스키마에 있으면 그 컬럼으로 filter_by_value, "
            "주소 컬럼만 있으면 filter_by_address_contains, "
            "좌표만 있으면 spatial_join_admin 후 filter_by_value(col='SIGUNGU_NM') 를 쓴다. "
            "spatial_join_admin 이 만드는 ADM_NM 은 행정동명(예 '이촌1동')이라 "
            "자치구명으로 거르면 결과가 0행이 된다.",
            "지역을 좁히는 방법은 다음 순서로 고른다. 앞의 방법이 되면 뒤의 방법을 쓰지 마라. "
            "(1) **좌표가 있으면** spatial_join_admin 후 filter_by_value(col='SIGUNGU_NM'). "
            "자치구명 컬럼이 따로 있어도 좌표를 우선한다 — 위치선정은 좌표로 배제 버퍼를 그리므로 "
            "'주소상 A구인데 좌표는 B구'인 행을 넣으면 엉뚱한 곳에 배제가 생긴다. "
            "이 op 는 ADM_NM(행정동명)도 함께 붙여 주므로 이후 행정동 단위 분석에도 쓰인다. "
            "★ spatial_join_admin 은 **좌표가 있거나 생기는 모든 데이터셋에 항상 포함**하라. "
            "지역 필터를 자치구명·주소 등 다른 방법으로 하더라도 마찬가지다 "
            "— 모든 레이어에 자치구·행정동 태그가 붙어 있어야 행정동 단위 집계·필터가 가능하다. "
            "coord_status=needs_geocoding 인 데이터도 run_geocode 로 좌표가 생기므로 "
            "run_geocode 뒤에 spatial_join_admin 을 넣는다(순서: run_geocode → spatial_join_admin → 필터). "
            "좌표가 아예 없는 통계표(stat_join)에만 생략한다.  "
            "(2) 좌표가 없고 자치구명 컬럼이 있으면 → filter_by_value  "
            "(3) 좌표가 없고 주소 컬럼만 있으면 → filter_by_address_contains  "
            "(4) 지역이 인코딩된 코드 컬럼(행정동코드 등) → filter_by_code_prefix "
            "(예: 행안부 행정동코드는 앞 5자리가 자치구)  "
            "(4b) **행정동 '이름'만 있고 자치구 표현이 없으면** → filter_by_admin_name. "
            "행정동 통계표가 여기 해당한다(값이 '왕십리제2동'·'합계' 뿐). "
            "이런 데이터에 filter_by_value(allowed=['<자치구>']) 를 걸면 0행이 된다.  "
            "(5) 위 어느 것도 없을 때만 → filter_by_join_key",
            "filter_by_value 의 allowed 에 **샘플 행에서 본 값을 나열하지 마라**. "
            "샘플은 데이터의 앞 2행일 뿐이고 실제로는 훨씬 많은 값이 있다. "
            "(실패 사례: 행정동 통계표에서 샘플에 보인 allowed=['왕십리제2동','성동구'] 로 걸러 "
            "17개 행정동 중 1개만 남았다) "
            "파일명에 대상 지역명이 들어 있고(예: '성동구_인구 및 세대현황.xlsx') "
            "sample_rows 도 그 지역 내용으로 보이면, 이미 그 지역 전용 데이터다 "
            "— 지역 필터를 넣지 마라(넣으면 값이 안 맞아 0행이 되기 쉽다). "
            "다만 '합계'·'소계' 같은 집계 행이 섞여 있으면 filter_by_admin_name 으로 걸러라.",
            "allowed 값은 **value_dist 에 실린 그 컬럼의 값을 그대로 골라 쓴다.** "
            "value_dist 는 고유값이 적은 컬럼의 값 분포 전체이므로, 거기 있는 컬럼이면 "
            "표기를 추측할 필요가 없다. value_dist 에 없는 컬럼(고유값이 많거나 자유 텍스트)일 "
            "때만 sample_rows 를 참고하되, 앞 2행뿐이라 값 집합이 아니라는 점을 유념하라. "
            "🔴 value_dist 는 **값의 표기를 확인하는 용도지 컬럼을 고르는 근거가 아니다.** "
            "어느 컬럼으로 거를지는 위 (1)~(5) 우선순위가 정한다. value_dist 에 '시군구명' "
            "같은 지역 컬럼이 보인다고 해서 좌표 기반 SIGUNGU_NM 대신 그것을 쓰지 마라 "
            "— 주소와 좌표가 어긋난 행이 통과한다. (실측: 상권 데이터에서 좌표 기준 15,722행 "
            "↔ 주소 기준 15,726행. 주소는 대상 자치구인데 좌표는 밖인 4건이 섞였다) "
            "(실패 사례: '행정기관' 컬럼 값은 '왕십리제2동'·'합계' 인데 allowed=['성동구'] 를 걸어 "
            "18행이 0행이 됐다. 컬럼에 없는 값으로 거르면 레이어가 통째로 사라진다) "
            "allowed 에는 '걸러내려는 기준값'만 넣는다 — 지역 필터면 대상 지역명, "
            "운영상태 필터면 남길 상태값. 그리고 데이터가 이미 대상 지역 전용이면(파일명·내용상) "
            "지역 필터 자체를 넣지 마라.",
            "[운영상태] 시설 위치 데이터에 운영 상태 컬럼(운영현황·영업상태·폐업여부·"
            "휴폐업·상태·폐지일자 등)이 있으면 **운영 중인 값만 남기는 filter_by_value 를 "
            "반드시 낸다.** 폐업·폐지·휴지 시설은 그 자리에 시설이 없으므로 배제 근거도 "
            "가점 근거도 되지 않는다. 배제 데이터면 없는 시설 주변을 배제해 후보가 부당하게 "
            "줄고, 가점 데이터면 없는 수요를 만든다. "
            "(실패 사례: 용산구 어린이집 180건 중 98건(54%)이 '폐지' 였는데 그대로 30m "
            "배제에 들어가 배제 면적의 절반 이상이 존재하지 않는 시설이었다) "
            "남길 값은 **value_dist 의 그 컬럼 값 목록을 보고 고른다.** sample_rows 로 "
            "정하지 마라 — 앞 2행뿐이라 드문 상태값이 안 보인다. (실패 사례: 어린이집 "
            "`운영현황` 이 정상 3,835 / 폐지 5,504 / 재개 75 / 휴지 66 인데 앞 2행이 둘 다 "
            "'정상' 이라 allowed=['정상'] 이 나왔고, 운영 중인 시설이 조용히 배제에서 빠졌다) "
            "value_dist 목록에서 **운영 중이 아님이 명백한 값**(폐지·폐업·폐원·휴지·휴업·"
            "말소·취소·중단)만 제외하고 **나머지는 전부 남긴다.** 판단이 서지 않는 값은 남겨라 "
            "— filter_by_value 는 허용목록 방식이라 **빠뜨린 값은 경고 없이 사라진다.** "
            "폐업이 몇 건 섞이는 비용 << 운영 중인 시설을 배제에서 놓치는 비용(법적 리스크). "
            "🔴 다만 값이 Y/N·O/X·있음/없음·유무 같은 **이진 플래그**면 이 op 를 내지 마라. "
            "`폐업여부=Y` 와 `영업여부=Y` 는 의미가 정반대인데 값만 봐서는 구분되지 않는다. "
            "방향을 뒤집으면 **운영 중인 시설만 지우고 폐업만 남는다** — 폐업이 섞이는 것보다 "
            "훨씬 나쁘고, 행 수가 그럴듯해 자동 검증으로도 안 잡힌다. 이때는 필터를 넣지 말고 "
            "요약에 '상태 컬럼이 이진 플래그라 방향을 확정할 수 없어 필터를 넣지 않았다'고 남겨라. "
            "상태 컬럼이 없으면 이 op 를 넣지 마라 — 없는 컬럼으로 거르면 0행이 된다.",
            "emit_whitelist 로 만든 이름을 **같은 데이터셋에서** filter_by_join_key 로 "
            "소비하지 마라. 자기 값으로 자기를 거르는 것이라 아무 효과가 없다. "
            "emit_whitelist 는 '이미 지역이 좁혀진 데이터셋'이 다른 데이터셋에 키를 넘길 때만 쓴다.",
            "이 데이터셋에 좌표도 자치구명도 주소도 지역코드도 없으면(예: 역명·정류장ID 만 있는 승하차 통계) "
            "자기 힘으로 지역을 좁힐 수 없다. 이때는 other_datasets 에서 "
            "'좌표가 있고 같은 대상을 가리키는 키 컬럼을 가진 데이터셋'을 찾아 "
            "filter_by_join_key(key_col='<이 데이터셋의 키 컬럼>', whitelist='<이름>') 를 쓴다. "
            "컬럼명이 서로 달라도 된다(예: '표준버스정류장ID'↔'NODE_ID', '역명'↔'역사명'). "
            "짝이 될 데이터셋이 안 보이면 filter_by_join_key 를 쓰되 key_col 은 "
            "이 데이터셋의 식별자 컬럼으로 정확히 지정하라 — 정제 엔진이 실제 값 겹침으로 "
            "짝을 찾아 자동 연결한다.",
            "거를 수 없다고 해서 값이 안 맞는 컬럼으로 filter_by_value 를 쓰지 마라. "
            "(예: 노선명='5호선' 컬럼을 allowed=['용산구'] 로 거르면 결과가 0행이 된다)",
            "서울 전역/전국 데이터인데 지역을 좁히는 op 가 하나도 없으면 안 된다 "
            "(원본이 그대로 통과해 다음 단계가 잘못된다).",
        ],
    }
    return {
        "system": get_system_prompt(facility),
        "user": json.dumps(user, ensure_ascii=False, indent=2),
    }


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


# ══════════════════════════════════════════════════════════════════
# 3. 채점 — 역할 적중 / op 집합 적중 / 누락·과잉
# ══════════════════════════════════════════════════════════════════


@dataclass
class Judgment:
    """감리 AI 판정 1건. (참고값 채점은 제거 — 실제 검토 관문은 HITL)"""

    dataset_id: str
    summary: str
    roles: list  # [{role, weight|배제반경_m, ...}]
    coord_status: str
    ops: list
    exclusions: list  # hard_exclusion 중 조례에 반경 없어 검토 필요한 것


def review_one(pred: dict, dataset_id: str) -> Judgment:
    summary = pred.get("summary", "")
    roles = pred.get("roles", [])
    coord = pred.get("coord_status", "")
    ops = [op["op_id"] for op in pred.get("cleaning_ops", [])]
    # 배제반경 미확정(조례에 없음) → 검토/서핑 대상
    exclusions = [
        r
        for r in roles
        if r.get("role") == "hard_exclusion"
        and (r.get("need_review") or r.get("배제반경_m") is None)
    ]
    return Judgment(
        dataset_id=dataset_id,
        summary=summary,
        roles=roles,
        coord_status=coord,
        ops=ops,
        exclusions=exclusions,
    )


def run_harness(llm: LLMClient, fixtures: dict, domain: dict, progress=None):
    """감리 AI 판정을 수집. 반환: (judgments, raw_preds).
    progress: 선택. 각 데이터셋 처리 후 호출되는 콜백(did) — 진행바(tqdm) 연결용.
    """
    out, raw_preds = [], {}
    for did, profile in fixtures.items():
        prompt = build_prompt(profile, domain, fixtures=fixtures)
        raw = llm.complete(prompt["system"], prompt["user"])
        try:
            pred = json.loads(raw)
        except json.JSONDecodeError:
            pred = {
                "dataset_id": did,
                "summary": "(파싱 실패)",
                "roles": [],
                "coord_status": "",
                "cleaning_ops": [],
                "hitl_flags": [],
                "_raw": raw[:200],
            }
        pred.setdefault("dataset_id", did)
        pred = (
            enrich_hitl_flags(  # 배제반경 null·지역코드 등 → hitl_flags 자동 생성(코드)
                pred, region=(domain or {}).get("region", ""), fixtures=fixtures
            )
        )
        raw_preds[did] = pred
        out.append(review_one(pred, did))
        if progress:
            progress(did)
    return out, raw_preds


def apply_radius_answer(
    result: dict, flag: dict, radius_m: int | None, source: str = "human_confirmed"
) -> None:
    """HITL 답변을 roles·flag 에 반영(메모리). radius_m=None 이면 '반경 없음(면 배제 등)'.

    확정은 **이 run 안에서만** 유효하다. 다음 실행으로 넘기지 않는다(캐시 제거, 2026-08-10).
    """
    idx = flag.get("role_index", 0)
    roles = result.get("roles", [])
    if idx >= len(roles):
        return
    role = roles[idx]

    role["배제반경_m"] = radius_m
    role["confirmed"] = True  # 사람이 확인함 → 확정
    role["need_review"] = False
    role["source"] = source
    flag["제안값"] = radius_m
    flag["confirmed"] = True
    flag["confirmed_by_human"] = True


def reset_exclusion_confirmations(doc: dict) -> int:
    """이미 확정돼 있던 배제(hard_exclusion)를 **제안값으로 되돌린다**. 반환 = 되돌린 건수.

    `mode:"hitl"` 은 STEP1 을 안 돈다 — 고정된 감리 산출물(픽스처/정본)을 그대로 쓰는데
    거기엔 예전에 확정된 `confirmed:true` 가 이미 박혀 있다. 그대로 두면 게이트A 가
    그 항목을 `editable:false` 로 내보내 **사람이 볼 수는 있어도 고칠 수 없다.**
    "HITL 인데 사람이 전부 확인한다"가 성립하려면 이 값들이 제안값이어야 한다
    (2026-08-10 사람 지시).

    값은 지우지 않는다 — `배제반경_m`·`source` 는 그대로 두고 flag 의 `제안값` 으로도
    올린다. 사람이 Enter 로 그대로 승인하면 같은 값이 다시 확정된다.
    """
    n = 0
    for r in doc.get("results", []):
        flags = r.setdefault("hitl_flags", [])
        by_idx = {
            f.get("role_index", 0): f
            for f in flags
            if f.get("type") == "exclusion_radius_missing"
        }
        for i, role in enumerate(r.get("roles", [])):
            if role.get("role") != "hard_exclusion":
                continue
            was_confirmed = role.get("confirmed") is True
            f = by_idx.get(i)
            if f is None:
                f = {"type": "exclusion_radius_missing", "role_index": i}
                flags.append(f)
            if f.get("제안값") is None and role.get("배제반경_m") is not None:
                f["제안값"] = role["배제반경_m"]
                f["출처"] = role.get("source") or f.get("출처")
            f["message"] = (
                "이전 실행에서 확정된 값입니다(이번 실행에서는 제안값). 다시 확인하세요."
                if was_confirmed
                else "배제 대상이나 반경이 확정되지 않았습니다. 확인이 필요합니다."
            )
            f["이전_확정"] = was_confirmed
            # 확정 표시를 지운다 — 남겨두면 게이트A 가 editable:false 로 내보낸다.
            f.pop("confirmed", None)
            f.pop("confirmed_by_human", None)
            role["confirmed"] = False
            role["need_review"] = True
            n += 1
    return n


def assert_exclusions_confirmed(doc: dict, src: str = "") -> None:
    """배제(hard_exclusion) 중 미확정이 하나라도 있으면 **멈춘다**(SystemExit).

    2026-08-10 사람 결정. 예전엔 미확정인 채로 STEP2~4 를 완주하고 `report.json` 의
    gap(`배제판정_확인요청`)에만 남았다 — 산출물은 정직했지만 **아무도 안 봤고**
    배제가 빠진 Top-N 이 그대로 화면4·5 로 갔다. 배제는 후보를 지우는 조건이라
    빠지면 결과가 뒤집힌다. 「경고하고 진행」이 아니라 「멈춤」이 맞다(원칙 1).

    푸는 방법은 하나다 — 게이트A(API `POST /runs/{id}/hitl/audit`) 또는
    CLI `hitl` 에서 **사람이 반경을 확정**한다. 반경 없이 면으로 배제할 항목은
    `radius_m: null`(CLI 는 `n`)로 확정한다. 이것도 확정이다.
    """
    bad: list[str] = []
    for r in doc.get("results", []):
        did = r.get("dataset_id", "?")
        for i, role in enumerate(r.get("roles", [])):
            if role.get("role") != "hard_exclusion":
                continue
            if role.get("confirmed") is True:
                continue
            bad.append(
                f"  [{did}] roles[{i}] {role.get('facility_type') or role.get('rationale') or '?'}"
                f"  (배제반경_m={role.get('배제반경_m')} · exclusion_type="
                f"{role.get('exclusion_type')})"
            )
    if not bad:
        return
    raise SystemExit(
        f"🔴 배제 {len(bad)}건이 미확정이라 STEP2 로 넘어가지 않는다"
        + (f" — {src}" if src else "")
        + "\n"
        + "\n".join(bad)
        + "\n   게이트A(HITL)에서 배제반경을 확정할 것. 반경 없이 면으로 배제할"
        " 항목은 null(CLI 는 n)로 확정한다 — 그것도 확정이다."
    )


def _read_radius(default: int | None = None) -> int | None | str:
    """배제반경(m) 입력.
      숫자   → 그 값으로 확정
      Enter  → 제안값 있으면 승인, 없으면 건너뜀(미확정 유지)
      n      → 반경 없음(면 배제 등)으로 확정
      s      → 건너뜀(미확정 유지 — 나중에 다시)
    반환: int | None(반경없음 확정) | "skip"(미확정 유지)
    """
    hint = f"[Enter={default}m 승인]" if default is not None else "[Enter=건너뜀]"
    while True:
        s = input(f"  배제반경(m) {hint} · n=반경없음 · s=건너뜀: ").strip().lower()
        if s == "":
            return default if default is not None else "skip"
        if s == "n":
            return None
        if s == "s":
            return "skip"
        try:
            # '100m', '30 m', '30미터' 같은 단위 표기도 허용(프론트 입력칸도 관대하게)
            num = s.replace("미터", "").replace("m", "").replace("ｍ", "").strip()
            v = int(float(num))
            if v < 0:
                print("    0 이상으로 입력하세요.")
                continue
            return v
        except ValueError:
            print("    숫자 · n · s 중 하나를 입력하세요.")


def confirm_exclusion_radius(
    enriched_path: str, dataset_id: str, radius_m: int, out_path: str | None = None
) -> str:
    """HITL 담당자가 서핑 제안값을 확인·확정할 때 호출. confirmed=true 로 바꾼다."""
    doc = json.load(open(enriched_path, encoding="utf-8"))
    for r in doc["results"]:
        if not r["dataset_id"].startswith(dataset_id):
            continue
        for f in r.get("hitl_flags", []):
            if f.get("type") != "exclusion_radius_missing":
                continue
            idx = f.get("role_index", 0)
            f["제안값"] = radius_m
            f["confirmed_by_human"] = True
            # roles 쪽도 확정 반영
            if idx < len(r.get("roles", [])):
                r["roles"][idx]["배제반경_m"] = radius_m
                r["roles"][idx]["confirmed"] = True
                r["roles"][idx]["need_review"] = False
    path = out_path or enriched_path
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return path


def _norm(s: str) -> str:
    """조례 대조용 정규화(NFC). 시설유형·값이 조례 텍스트에 있는지 substring 비교에 사용."""
    import unicodedata

    return unicodedata.normalize("NFC", s or "")


def enrich_hitl_flags(
    pred: dict, region: str = "", fixtures: dict | None = None
) -> dict:
    """LLM 판정을 받은 뒤, 사람 검토가 필요한 항목을 코드가 결정론적으로 hitl_flags에 채운다.
    (LLM 판정 실수와 무관하게 항상 보장 — '판정=LLM, 확정=코드' 원칙)

    🔴 2026-08-10(사람 지시) — **배제(hard_exclusion)는 예외 없이 전부 사람이 본다.**
       예전엔 두 경로가 사람을 건너뛰었다: ⓐ 캐시 히트(제거됨) ⓑ 조례 텍스트에
       시설유형·값이 둘 다 있으면 `confirmed=True`. ⓑ 는 substring 대조라
       「제5조의 10m 가 이 시설 얘기인지」까지는 알 수 없다 — 근거는 되지만
       확정은 아니다. 자동으로 끝까지 가야 할 때는 `mode:"full"` 이 따로 있다.
       이제 조례 근거는 **제안값**으로 내려가고, 확정은 사람 답변으로만 붙는다.
    """
    flags = list(pred.get("hitl_flags", []))
    ord_norm = _norm(load_ordinance())  # 현재 도메인 조례 텍스트(제안 근거)
    existing = {(f.get("type"), f.get("role_index")) for f in flags}
    for i, r in enumerate(pred.get("roles", [])):
        if r.get("role") != "hard_exclusion":
            continue
        ftype = r.get("facility_type")
        radius = r.get("배제반경_m")
        is_radius = r.get("exclusion_type", "radius") == "radius"

        # 조례 대조: radius 값 존재 + 시설유형·값이 조례에 실제로 있으면 **제안값**.
        ftype_in_ord = bool(ftype) and _norm(ftype) in ord_norm
        value_in_ord = radius is not None and str(radius) in ord_norm
        ord_backed = bool(is_radius and radius is not None and ftype_in_ord and value_in_ord)

        # 배제는 전부 미확정으로 내려간다. LLM 자가확정도 여기서 벗긴다.
        # ※ source·rationale·배제반경_m 은 지우지 않는다 — 사람이 판단 근거로 봐야 하므로.
        r["confirmed"] = False
        r["need_review"] = True
        key = ("exclusion_radius_missing", i)
        if key not in existing:
            # 중복 필드(dataset_id·facility_type·exclusion_type) 없음 —
            # 이 flag 는 해당 데이터셋의 hitl_flags 안에 있고, role_index 로 roles[i] 를 가리킨다.
            flags.append(
                {
                    "type": "exclusion_radius_missing",
                    "role_index": i,
                    "message": (
                        "조례에서 시설유형·반경 문구를 찾았습니다(제안값). 같은 시설 규정이 맞는지 확인하세요."
                        if ord_backed
                        else "배제 대상이나 조례에서 반경/근거 확인 안 됨(LLM 자가판정). 검색·사람 확인 필요."
                    ),
                    "제안값": radius if ord_backed else None,
                    "출처": (r.get("source") or "ordinance") if ord_backed else None,
                    # 조례 제안일 때만 의미가 있다. substring 대조라 True 여도
                    # '그 시설의 규정'임을 보장하지 않는다 — 그래서 사람이 본다.
                    "근거_시설_일치": ftype_in_ord if ord_backed else None,
                }
            )

    # reference_only(참조/하류/무관 데이터) → 사람에게 '의도'를 묻는 HITL flag.
    #   LLM 은 reference_only 판정만, 질문 flag 생성은 코드가 결정론적으로.
    role_names = {r.get("role") for r in pred.get("roles", [])}
    if "reference_only" in role_names and not any(
        f.get("type") == "data_intent_unclear" for f in flags
    ):
        flags.append(
            {
                "type": "data_intent_unclear",
                "message": (
                    f"'{pred.get('summary', '')}' — 입지 판정의 입력 팩터로 보이지 않습니다"
                    "(참조·하류·무관 가능). 이 데이터를 어떤 용도로 넣으셨나요?"
                ),
                "질문": "이 데이터의 의도는?",
                "선택지": [
                    "가점(수요) 요인",
                    "감점(민감도) 요인",
                    "배제(금지) 요인",
                    "위치선정 참조용(감리 입력 아님)",
                    "잘못 넣음 · 제외",
                ],
                "제안": "참조용이면 감리에서 제외하고 위치선정 단계에서 사용",
                "confirmed": False,
            }
        )
    pred["hitl_flags"] = flags
    return _enrich_code_prefix(pred, region, fixtures)


def search_exclusion_radius(
    dataset_summary: str, region: str, facility: str = "", model: str | None = None
) -> dict:
    """[폴백] OpenAI Responses API + web_search 로 배제반경 후보 검색(법령 API 실패 시).
    반환: {"제안값": int|null, "출처": url|null, "근거문장": str, "source_type": "web_search"}
    ※ 확정 아님 — confirmed 는 호출부에서 계속 false 로 둔다(사람 확인 필수).
    """
    from openai import OpenAI
    from app.config import OPENAI_API_KEY, SEARCH_LLM_MODEL

    client = OpenAI(api_key=OPENAI_API_KEY)
    m = model or SEARCH_LLM_MODEL

    fac = facility or "대상 시설"
    prompt = (
        f"한국 {region}에서 '{fac}' 입지를 선정한다. '{dataset_summary}'에 해당하는 시설로부터 "
        f"'{fac}' 설치가 금지되는 법정 이격거리(배제 반경, 미터)를 찾아라. "
        f"근거는 반드시 법령·시행령·조례 등 공식 출처여야 한다. "
        f"블로그·뉴스의 인용값은 신뢰하지 말고, 원 법령을 확인하라. "
        f"★중요: 반드시 '현행(현재 시행 중인)' 최신 기준을 찾아라. 법은 개정되므로 "
        f"과거 폐지된 수치를 쓰지 말고, 개정 이력을 확인해 가장 최근 시행 값을 쓰고 "
        f"근거문장에 시행일을 포함하라.\n"
        f"찾으면 아래 JSON 형식 하나만 출력(설명 금지):\n"
        f'{{"제안값": <정수 미터 또는 null>, "출처": "<법령명·조항 또는 URL>", '
        f'"근거문장": "<해당 거리를 규정한 문장 요약 + 시행일>"}}'
    )
    resp = client.responses.create(
        model=m,
        tools=[{"type": "web_search"}],
        input=prompt,
    )
    text = resp.output_text.strip()
    text = re.sub(r"^```(json)?|```$", "", text).strip()
    try:
        found = json.loads(text)
    except json.JSONDecodeError:
        found = {"제안값": None, "출처": None, "근거문장": text[:200]}
    found["source_type"] = "web_search"
    return found


def enrich_with_search(
    in_path: str | None = None,
    out_path: str | None = None,
    region: str | None = None,
    ordinance_rag: str = "",
) -> str:
    """audit_result.json 의 exclusion_radius_missing flag 를, 조례가 인용한 상위법을
    법령 API 로 조회해 배제반경 후보로 채워 별도 저장. 원본 보존, confirmed=false(HITL 확인).
    ordinance_rag: 업로드된 조례 본문(「」 인용 법령 파싱용). 없으면 조례 텍스트 파일 사용.
    region: 안 주면 감리 결과의 `facility_inference.region` 을 쓴다. 🔴 예전 기본값은
      **"용산구"** 였다(2026-08-10 제거) — `search` 모드로 성동구를 돌리면 인자를 안 주므로
      용산구로 web_search 했다. 시설명(`facility`)은 이미 그 파일에서 읽고 있었다."""
    import copy
    import os
    from app.services.gam2_ordinance_acquisition import (
        extract_cited_laws,
        find_radius_in_laws,
    )
    from app.services.gam2_ordinance_select import has_siting_provision

    in_path = in_path or _out_path("audit_result.json")
    out_path = out_path or _out_path("audit_result_enriched.json")
    doc = json.load(open(in_path, encoding="utf-8"))
    enriched = copy.deepcopy(doc)

    # 조례 본문에서 인용된 상위법 목록 추출(한 번만)
    rag = ordinance_rag or load_ordinance()
    cited = extract_cited_laws(rag)
    print(f"  조례 인용 상위법: {cited}")

    # ── 검색 스킵 → HITL 직행 ─────────────────────────────────────────
    # 검색의 출발점은 '조례가 인용한 상위법'이다. 조례가 없으면 법령 API 진입로가 없고,
    # 남는 건 web_search 뿐인데 실측 결과 비용·시간만 쓰고 소득이 없었다(128s, 제안 대부분 null).
    #
    # 🔴 2026-08-03 — 게이트가 틀린 질문을 하고 있었다.
    #   기존: "조례가 있는가"(`not cited`)
    #   성동구 폐기물 조례는 **있고 상위법을 11개나 인용**하는데 이격 규정만 없다.
    #   → 게이트가 안 걸려 11개 × 배제 3건 검색으로 들어갔고 크레딧이 소진됐다.
    #   물어야 할 것은 "조례에 **이격 규정**이 있는가" 다. → has_siting_provision (LLM 0회)
    has_prov, prov_sig = has_siting_provision(rag)
    if not cited or not has_prov:
        if not cited:
            reason = "조례(또는 인용 상위법) 없음"
            stype = "ordinance_absent"
        else:
            reason = (
                f"조례에 이격거리·설치금지 규정 없음(전문 {len(rag):,}자 · 신호 0건)"
            )
            stype = "ordinance_no_provision"

        n_missing = 0
        for r in enriched["results"]:
            for f in r.get("hitl_flags", []):
                if f.get("type") != "exclusion_radius_missing":
                    continue
                n_missing += 1
                # 출처를 값마다 남긴다 — 안 한 것은 "안 했다"고 기록한다(절대원칙 4).
                f["source_type"] = stype
                f["근거문장"] = f"{reason} · 상위법 검색 생략"

        print(f"\n  ※ {reason} → 배제반경 검색을 건너뜁니다.")
        print(f"     미확정 배제반경 {n_missing}건은 HITL 에서 직접 확인·입력하세요:")
        print("       python audit_judgment_test.py hitl <도메인폴더>")
        # ⚠️ 상위법을 '검색했는데 없었다'가 아니라 '검색하지 않았다'. 구분해서 남긴다.
        enriched["_schema"]["상위법검색"] = (
            f"생략 — {reason}. 상위법은 **검색하지 않았다**(규정 없음을 확정한 것이 아니다). "
            f"배제반경은 HITL 에서 사람이 입력."
        )
        enriched["_schema"]["조례_입지규정_신호"] = prov_sig
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(enriched, f, ensure_ascii=False, indent=2)
        print(f"\n[검색 생략] 원본 그대로 저장 → {out_path}")
        return out_path
    # ─────────────────────────────────────────────────────────────

    # facility·region(폴백 web_search 프롬프트용) — 결과 JSON의 facility_inference에서.
    # 두 값의 출처를 같은 곳으로 맞춘다 — facility 만 파일에서 읽고 region 은 기본값을
    # 쓰면 "성동구 재활용정거장" 이 아니라 "용산구 재활용정거장" 을 검색하게 된다.
    _fi = doc.get("facility_inference", {}) or {}
    facility = _fi.get("facility", "")
    region = str(region or _fi.get("region") or "").strip()
    if not region:
        # 여기까지 왔다는 건 검색할 flag 가 실제로 있다는 뜻이다(위 스킵 경로 통과).
        raise SystemExit(
            f"🔴 {in_path} 에 facility_inference.region 이 없다. "
            "지역을 추측하지 않는다 — region= 으로 넘기거나 감리를 다시 돌릴 것."
        )

    n_filled = 0
    for r in enriched["results"]:
        for f in r.get("hitl_flags", []):
            if f.get("type") != "exclusion_radius_missing":
                continue
            # facility_type 은 flag 에 중복 저장 안 함 — role_index 로 roles[i] 에서 조회.
            _roles = r.get("roles", [])
            _idx = f.get("role_index", 0)
            ftype = (
                _roles[_idx].get("facility_type") if _idx < len(_roles) else None
            ) or r.get("summary", "")[:6]
            print(f"  [법령검색] {r['dataset_id']}: '{ftype}' 배제반경 상위법 조회")

            # 1차: 조례 인용 상위법을 법령 API로 조회
            try:
                found = find_radius_in_laws(cited, ftype, facility=facility)
            except Exception as e:
                print(f"           [법령 API 오류] {e} → web_search 폴백")
                found = {"제안값": None, "source_type": "law_api_failed"}
            # 폴백: 법령 API가 통신오류/미발견이면 web_search(감리 결과 참고)
            if found.get("제안값") is None:
                print("           법령 API 미발견 → web_search 폴백")
                try:
                    found = search_exclusion_radius(
                        r.get("summary", ftype), region, facility
                    )
                except Exception as e:
                    print(f"           [web_search 오류] {e}")
                    found = {
                        "제안값": None,
                        "출처": None,
                        "근거문장": "검색 실패",
                        "source_type": "search_failed",
                    }
            f["제안값"] = found.get("제안값")
            f["출처"] = found.get("출처")
            f["source_type"] = found.get(
                "source_type"
            )  # law_api / web_search / *_failed
            f["근거문장"] = found.get("근거문장", "")
            # 근거-시설 일치 점검: 근거문장에 facility_type 이 실제로 있는지(오추출 방지).
            #   confirmed 재판정과 같은 substring(NFC) 방식. 자동 반려 아님 — 표시만.
            근거norm = _norm(f["근거문장"])
            f["근거_시설_일치"] = bool(ftype) and _norm(ftype) in 근거norm
            f["confirmed"] = False  # 어느 경로든 HITL 최종 확인 필수
            n_filled += 1
            mark = "" if f["근거_시설_일치"] else "  ⚠근거-시설 불일치"
            if f.get("제안값") is not None and not f["근거_시설_일치"]:
                f["message"] = (
                    f"⚠근거-시설 불일치: 근거문장에 '{ftype}'이(가) 없음 — "
                    f"다른 시설 규정을 긁었을 수 있음. 사람이 반드시 확인."
                )
            print(
                f"           → 제안 {found.get('제안값')}m (source: {found.get('source_type')}){mark}"
            )
    enriched["_schema"]["상위법검색"] = (
        "exclusion_radius_missing flag 를 조례가 인용한 상위법(법령 API)"
        "에서 반경을 찾아 제안값에 채움. confirmed=false, HITL 확인 필수. "
        "근거_시설_일치=false 면 근거문장에 해당 시설이 없어 오추출 의심(사람 확인)."
    )
    json.dump(
        enriched, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2
    )
    print(f"\n[상위법 검색 완료] {n_filled}건 채움 → {out_path}")
    return out_path


def _read_int(prompt: str, lo: int, hi: int) -> int:
    while True:
        try:
            v = int(input(prompt).strip())
            if lo <= v <= hi:
                return v
        except (ValueError, EOFError):
            pass
        print(f"  → {lo}~{hi} 사이 숫자로 입력하세요.")


def _read_weight() -> float:
    while True:
        try:
            return max(-1.0, min(1.0, float(input("  가중치 크기(-1~1): ").strip())))
        except (ValueError, EOFError):
            print("  → -1~1 사이 숫자로 입력하세요.")


def apply_intent_answer(result: dict, choice: int, weight: float | None = None) -> None:
    """data_intent_unclear 답변(1~5)을 result['roles']에 결정론적으로 반영. 새 필드 없음.
    부호는 선택이 정하고 크기는 입력값(가점=+, 감점=-)."""
    if choice == 1:  # 가점
        result["roles"] = [
            {
                "role": "positive_factor",
                "weight": abs(weight),
                "rationale": "HITL 확정",
                "confirmed": True,
            }
        ]
    elif choice == 2:  # 감점
        result["roles"] = [
            {
                "role": "negative_factor",
                "weight": -abs(weight),
                "rationale": "HITL 확정",
                "confirmed": True,
            }
        ]
    elif choice == 3:  # 배제 (드묾: 표시만, 반경은 추후)
        result["roles"] = [
            {
                "role": "hard_exclusion",
                "exclusion_type": "radius",
                "facility_type": None,
                "배제반경_m": None,
                "source": None,
                "confirmed": False,
                "need_review": True,
                "rationale": "HITL 배제 승격 — 반경 미정(추후 확인)",
            }
        ]
    elif choice == 4:  # 위치선정 참조용(감리 입력 아님) — reference_only 유지
        result["roles"] = [
            {
                "role": "reference_only",
                "confirmed": True,
                "rationale": "HITL — 위치선정 참조용",
            }
        ]
    else:  # 5 제외
        result["roles"] = []


ADM_CODE_SHEET = "행정동코드"  # (폴백) 엑셀 시트명
_ADM_CODE_CACHE: dict | None = None  # {체계: {코드접두: (시도명, 시군구명)}} 세션 캐시

# 시도 표기 흔들림 흡수 — 크로스워크는 '서울특별시', 엑셀은 '서울', 사용자는 '서울시'.
_SIDO_ALIAS = {
    "서울": "서울특별시",
    "서울시": "서울특별시",
    "부산": "부산광역시",
    "대구": "대구광역시",
    "인천": "인천광역시",
    "광주": "광주광역시",
    "대전": "대전광역시",
    "울산": "울산광역시",
    "세종": "세종특별자치시",
    "세종시": "세종특별자치시",
    "경기": "경기도",
    "강원": "강원특별자치도",
    "충북": "충청북도",
    "충남": "충청남도",
    "전북": "전북특별자치도",
    "전남": "전라남도",
    "경북": "경상북도",
    "경남": "경상남도",
    "제주": "제주특별자치도",
}


def _norm_sido(name: str) -> str:
    """'서울' · '서울시' · '서울특별시' 를 한 형태로."""
    n = (name or "").strip()
    return _SIDO_ALIAS.get(n, n)


def split_region(region: str) -> tuple:
    """'서울특별시 용산구' -> ('서울특별시','용산구') · '용산구' -> ('','용산구').
    시도가 없으면 빈 문자열. 지역명은 코드에 박지 않고 문자열에서만 읽는다."""
    toks = [t for t in str(region or "").replace("\u3000", " ").split() if t]
    if not toks:
        return "", ""
    if len(toks) == 1:
        return "", toks[0]
    return _norm_sido(toks[0]), toks[-1]


def _crosswalk_path() -> str:
    """행정동 크로스워크 경로. config 에 없으면 REGION_DATA_DIR 에서 찾는다.

    gam2_weight_model · gam2_audit_ops_catalog 는 이미 이 폴백을 갖고 있는데
    여기만 없었다. config 에 ADMIN_CROSSWALK_PATH 가 정의돼 있지 않으면
    **STEP3 는 되는데 STEP1 감리만 코드 검증이 꺼지는** 상태가 된다.
    (2026-08-03 실측: CW="" → 11440(마포구) 이 검증 없이 HITL 기본값이 됐다)
    """
    p = getattr(config, "ADMIN_CROSSWALK_PATH", "")
    if p:
        return str(p)
    rd = getattr(config, "REGION_DATA_DIR", "")
    return os.path.join(str(rd), "행정동_크로스워크.csv") if rd else ""


def _load_admin_code_map() -> dict:
    """행자부 행정동코드 ↔ 시군구명 매핑을 읽어 {코드접두: 시군구명} 으로 만든다.
    5자리(자치구)와 8자리(행정동) 접두를 모두 담아 어느 길이로 걸러도 검증된다.
    파일이 없으면 빈 dict → 검증을 건너뛰고 HITL 확인만 남는다(조용히 통과시키지 않음).
    """
    global _ADM_CODE_CACHE
    if _ADM_CODE_CACHE is not None:
        return _ADM_CODE_CACHE
    _ADM_CODE_CACHE = {"행자부": {}, "통계청": {}}
    import pandas as pd

    def _put(sys_name, code, sido, gu):
        if not isinstance(code, str):
            return
        code = code.strip()
        if not (code and sido and gu):
            return
        _ADM_CODE_CACHE[sys_name][code] = (sido, gu)
        if len(code) >= 5:
            _ADM_CODE_CACHE[sys_name][code[:5]] = (sido, gu)

    # 1) 크로스워크(전국) 우선
    cw = _crosswalk_path()
    if cw and os.path.isfile(cw):
        try:
            df = pd.read_csv(cw, dtype=str)
            for _, r in df.iterrows():
                sido, gu = _norm_sido(r.get("시도명")), r.get("시군구명")
                _put("행자부", r.get("행정동코드"), sido, gu)
                _put("행자부", r.get("행정동코드8"), sido, gu)
                _put("통계청", r.get("행정구역코드"), sido, gu)
            # 파일은 읽혔는데 표가 비면 **컬럼명이 다른 것**이다.
            #   그냥 반환하면 '표 없음' 과 구분이 안 되고 verify 는 전부 unknown 이
            #   된다 — 조용한 실패다. 실제 컬럼을 찍어 원인을 바로 보게 한다.
            n_gu = len({gu for t in _ADM_CODE_CACHE.values() for _, gu in t.values()})
            if n_gu == 0:
                print(
                    f"  🔴 크로스워크를 읽었으나 코드 0건 — 컬럼명 불일치.\n"
                    f"    파일: {cw}\n"
                    f"    실제 컬럼: {list(df.columns)}\n"
                    f"    필요 컬럼: 시도명 · 시군구명 · "
                    f"행정동코드 / 행정동코드8 / 행정구역코드"
                )
            else:
                print(
                    f"  [코드표] 크로스워크 {len(df):,}행 · 시군구 {n_gu}종 "
                    f"— {os.path.basename(cw)}"
                )
            return _ADM_CODE_CACHE
        except Exception as e:
            print(
                f"  [경고] 크로스워크 로드 실패({e}) — 엑셀 폴백을 시도합니다\n"
                f"    파일: {cw}"
            )

    # 2) 엑셀 폴백 (서울 한정)
    path = str(getattr(config, "ADM_CODE_MAP", "") or "")
    if not path or not os.path.isfile(path):
        # 어느 경로를 봤는지 알려준다. 종전에는 경로를 담은 메시지가
        #   이 early return **뒤**에 있어서, 둘 다 없을 때 끝내 안 보였다.
        print(
            "  [경고] 행정동 코드표 없음 — 코드 검증 없이 HITL 확인만 수행\n"
            f"    크로스워크 : {cw or '(config 에 ADMIN_CROSSWALK_PATH 없음)'}\n"
            f"    엑셀 폴백  : {path or '(config 에 ADM_CODE_MAP 없음)'}\n"
            "    → make_admin_crosswalk.py 로 행정동_크로스워크.csv 를 만드세요."
        )
        return _ADM_CODE_CACHE
    # 폴백이 조용히 발동하면 '전국 3,555동'인 줄 알면서 실제로는 서울 424동만
    # 보게 된다. 서울 밖 도메인에서는 전부 unknown 이 되어 HITL 만 늘어난다.
    print(
        f"  ⚠ 크로스워크 없음({cw or '경로 미설정'}) — 엑셀 폴백 사용(서울 한정).\n"
        f"    전국 대응하려면 make_admin_crosswalk.py 로 크로스워크를 만드세요."
    )
    try:
        df = pd.read_excel(path, sheet_name=ADM_CODE_SHEET, dtype=str, skiprows=1)
        df.columns = [
            "통계청행정동코드",
            "행자부행정동코드",
            "시도명",
            "시군구명",
            "행정동명",
        ][: len(df.columns)]
        for _, r in df.iterrows():
            sido, gu = _norm_sido(r.get("시도명")), r.get("시군구명")
            _put("행자부", r.get("행자부행정동코드"), sido, gu)
            _put("통계청", r.get("통계청행정동코드"), sido, gu)
    except Exception as e:
        print(
            f"  [경고] 행정동 코드표 로드 실패({e}) — 코드 검증 없이 HITL 확인만 수행"
        )
    return _ADM_CODE_CACHE


def verify_code_prefix(prefix: str, region: str) -> tuple:
    """코드 접두가 대상 지역인지 대조. 반환: (판정, 설명문자열|None)

      'ok'        모든 코드 체계에서 대상 지역
      'ambiguous' 체계에 따라 다른 지역 — **자동 확정 금지**, 사람이 판단
      'mismatch'  어떤 체계로도 대상 지역이 아님
      'unknown'   표에 없음

    감리 AI 가 추측한 행정코드를 **데이터로 검증**하는 유일한 수단이다.
    (실제 사고: 용산구인데 11440(마포구)을 써서 데이터 전체가 다른 구였다)

    ⚠ 'ambiguous' 가 필요한 이유 — 11170 은 행자부로 용산구, 통계청으로 구로구다.
      체계를 모른 채 하나로 합쳐 보면 '검증 통과'가 오답이 된다.
    """
    m = _load_admin_code_map()
    pf = str(prefix or "").strip()
    if not m or not pf:
        return "unknown", None
    _, want_gu = split_region(region)
    want_sido, _ = split_region(region)

    hits = {}  # 체계 -> (시도, 시군구)
    for sys_name, table in m.items():
        got = table.get(pf)
        if got:
            hits[sys_name] = got
    if not hits:
        return "unknown", None

    def _same(v):
        sido, gu = v
        if gu != want_gu:
            return False
        return (not want_sido) or (sido == want_sido)

    oks = {k: v for k, v in hits.items() if _same(v)}
    desc = " · ".join(f"{k}={v[0]} {v[1]}" for k, v in hits.items())
    if len(oks) == len(hits):
        return "ok", desc
    if oks:
        return "ambiguous", desc
    return "mismatch", desc


def suggest_code_prefix(region: str, system: str = "행자부") -> str | None:
    """대상 지역명 -> 자치구 5자리 접두. 후보가 정확히 1개일 때만 돌려준다.

    시군구명은 전국에서 유일하지 않다(중구 6 · 동구 6 · 서구 5 · 남구 4 · 북구 4 …).
    region 에 시도가 함께 오면 252종 전부 유일해진다 → 자동 확정이 가능해진다.
    """
    m = _load_admin_code_map().get(system, {})
    want_sido, want_gu = split_region(region)
    if not want_gu:
        return None
    cands = sorted(
        {
            c
            for c, (sido, gu) in m.items()
            if len(c) == 5 and gu == want_gu and ((not want_sido) or sido == want_sido)
        }
    )
    return cands[0] if len(cands) == 1 else None


def region_is_unique(region: str) -> bool:
    """대상 지역이 전국에서 하나로 특정되는가.

    시군구명만으로는 유일하지 않다 — 중구 6 · 동구 6 · 서구 5 · 남구 4 · 북구 4 ·
    고성군 2 · 강서구 2 (전국 230종 중 7종). 시도가 함께 오면 252종 전부 유일해진다.
    유일하지 않으면 코드 검증이 '어느 중구인지' 를 못 가리므로 자동 확정하지 않는다.
    """
    m = _load_admin_code_map()
    w_sido, w_gu = split_region(region)
    if not w_gu:
        return False
    found = set()
    for table in m.values():
        for sido, gu in table.values():
            if gu == w_gu and ((not w_sido) or sido == w_sido):
                found.add((sido, gu))
    return len(found) == 1


_FIXTURE_CACHE: dict | None = None


def _code_samples(
    dataset_id: str, col: str, n: int = 8, fixtures: dict | None = None
) -> list:
    """프로파일 sample_rows 에서 해당 컬럼의 값 표본을 꺼낸다. 실패하면 빈 리스트.
    감리 결과 JSON 에는 표본이 없으므로 fixture(profiles.json)를 읽는다.
    fixtures 를 직접 받으면(감리 중) 다시 로드하지 않는다."""
    global _FIXTURE_CACHE
    if fixtures is not None:
        _FIXTURE_CACHE = _FIXTURE_CACHE or fixtures
        f = fixtures.get(dataset_id) or {}
        return [
            row.get(col)
            for row in (f.get("sample_rows") or [])
            if row.get(col) not in (None, "")
        ][:n]
    if _FIXTURE_CACHE is None:
        try:
            _FIXTURE_CACHE = build_fixtures()
        except Exception as e:
            print(f"  [경고] fixture 로드 실패({e}) — 코드 체계 판정 생략")
            _FIXTURE_CACHE = {}
    f = _FIXTURE_CACHE.get(dataset_id) or {}
    out = []
    for row in f.get("sample_rows") or []:
        v = row.get(col)
        if v not in (None, ""):
            out.append(v)
    return out[:n]


def detect_code_system(values) -> tuple:
    """코드 표본이 어느 체계인지 **데이터로** 가린다. 반환 (체계|None, 설명)

    행자부/통계청은 같은 접두를 다른 구에 쓴다(11170 = 용산구 / 구로구).
    접두만 보면 영원히 못 가리지만, 실제 값은 체계마다 코드표 적중률이 갈린다.
      실측(용산): 생활인구 값 -> 행자부 16/16 · 통계청 7/16
                  경계 SHP 값 -> 행자부  0/16 · 통계청 16/16

    표본 전체를 설명하는 체계가 **정확히 하나**일 때만 결정한다.
    새 임계값을 만들지 않으며, 애매하면 None 을 돌려 사람에게 넘긴다(fail safe).
    """
    m = _load_admin_code_map()
    vals = [str(v).strip() for v in (values or []) if str(v).strip()]
    vals = [v for v in vals if v.isdigit() and len(v) >= 5]
    if not vals or not any(m.values()):
        return None, "코드 표본 없음"
    score = {k: sum(1 for v in vals if v in t or v[:8] in t) for k, t in m.items()}
    desc = " · ".join(f"{k} {v}/{len(vals)}" for k, v in score.items())
    full = [k for k, v in score.items() if v == len(vals)]
    return (full[0] if len(full) == 1 else None), desc


def resolve_code_prefix(prefix: str, region: str, samples=None) -> dict:
    """지역 코드 접두 판정을 **한 곳에서** 내린다. 감리·HITL·프런트가 같은 답을 본다.

    반환(그대로 audit_result.json 의 params.prefix_check 에 실린다):
      status        "auto_confirmed" | "needs_review"   ← 프런트가 볼 값
      verdict       ok | ambiguous | mismatch | unknown
      system        데이터 표본으로 판정된 코드 체계(가릴 수 있었을 때만)
      resolved      이 접두가 실제로 가리키는 '시도 시군구'
      region_unique 대상 지역이 전국에서 유일한가
      suggestion    대상 지역의 자치구 코드(있으면)
      reason        사람이 읽을 판단 근거
    """
    pf = str(prefix or "").strip()
    verdict, detail = verify_code_prefix(pf, region)
    uniq = region_is_unique(region)
    m = _load_admin_code_map()
    out = {
        "status": "needs_review",
        "verdict": verdict,
        "prefix": pf,
        "region": region,
        "region_unique": bool(uniq),
        "system": None,
        "resolved": None,
        "detail": detail,
        "suggestion": suggest_code_prefix(region),
        "reason": "",
    }
    if not any(m.values()):
        out["reason"] = "행정동 코드표 없음 — 검증 불가"
        return out

    def _fill(got):
        out["resolved"] = f"{got[0]} {got[1]}" if got else None

    if verdict == "ok":
        _fill(next((t.get(pf) for t in m.values() if t.get(pf)), None))
        if uniq:
            out["status"] = "auto_confirmed"
            out["reason"] = "코드표 대조 — 이 접두를 아는 모든 체계가 대상 지역"
        else:
            out["reason"] = (
                f"'{region}' 이 전국에서 유일하지 않음 (시도를 함께 적으면 자동 확정)"
            )
        return out

    if verdict == "ambiguous":
        sysname, sdesc = detect_code_system(samples)
        out["system"] = sysname
        out["detail"] = f"{detail} / 표본판정: {sdesc}"
        if not sysname:
            out["reason"] = "코드 체계를 데이터 표본으로 가릴 수 없음"
            return out
        got = m[sysname].get(pf)
        _fill(got)
        w_sido, w_gu = split_region(region)
        same = bool(got) and got[1] == w_gu and ((not w_sido) or got[0] == w_sido)
        if same and uniq:
            out["status"] = "auto_confirmed"
            out["reason"] = f"데이터 표본이 {sysname} 체계로 판정됨"
        elif same:
            out["reason"] = (
                f"'{region}' 이 전국에서 유일하지 않음 (시도를 함께 적으면 자동 확정)"
            )
        else:
            out["reason"] = f"{sysname} 체계에서 이 접두는 대상 지역이 아님"
        return out

    out["reason"] = (
        "대상 지역이 아님" if verdict == "mismatch" else "코드표에 없는 접두"
    )
    return out


def _enrich_code_prefix(pred: dict, region: str, fixtures: dict | None) -> dict:
    """filter_by_code_prefix 판정을 감리 단계에서 미리 내려 결과에 남긴다.

    왜 감리 단계인가 — HITL 실행 중에만 판정하면 `audit_result.json` 만 읽는
    프런트가 '이 항목이 통과인지 확인 대상인지' 알 수 없다.
    """
    flags = pred.setdefault("hitl_flags", [])
    did = pred.get("dataset_id", "")
    for i, op in enumerate(pred.get("cleaning_ops") or []):
        if op.get("op_id") != "filter_by_code_prefix":
            continue
        prm = op.setdefault("params", {})
        chk = resolve_code_prefix(
            prm.get("prefix", ""),
            region,
            _code_samples(did, prm.get("col"), fixtures=fixtures),
        )
        chk["col"] = prm.get("col")
        prm["prefix_check"] = chk
        if chk["status"] == "auto_confirmed":
            prm["prefix_confirmed"] = True
            prm["prefix_confirmed_by"] = "code_table" + (
                f":{chk['system']}" if chk["system"] else ""
            )
            continue
        prm.setdefault("prefix_confirmed", False)
        if not any(
            f.get("type") == "code_prefix_unverified" and f.get("op_index") == i
            for f in flags
        ):
            flags.append(
                {
                    "type": "code_prefix_unverified",
                    "op_index": i,
                    "col": chk["col"],
                    "prefix": chk["prefix"],
                    "verdict": chk["verdict"],
                    "reason": chk["reason"],
                    "detail": chk["detail"],
                    "suggestion": chk["suggestion"],
                    # message 는 요약 출력이 쓰는 공통 필드다(다른 flag 와 동일 규약).
                    "message": (
                        f"'{chk['col']}' 접두 '{chk['prefix']}' — "
                        f"{chk['reason']}"
                        + (f" ({chk['detail']})" if chk.get("detail") else "")
                        + (
                            f" · 제안 '{chk['suggestion']}'"
                            if chk.get("suggestion")
                            else ""
                        )
                    ),
                    "confirmed": False,
                }
            )
    return pred


def review_hitl(in_path: str | None = None, out_path: str | None = None) -> str:
    """HITL — 사람이 확인·확정하는 단계. 두 종류의 flag 를 처리한다.
      1) exclusion_radius_missing : 배제반경 확인/입력 (need_review=true 인 배제)
           · 제안값 있음(search 가 상위법에서 찾음) → 보여주고 승인/수정
           · 제안값 없음(조례 없어 검색 생략)      → 근거만 보여주고 직접 입력
      2) data_intent_unclear      : 애매한 데이터의 용도 확인(1~5)
      3) filter_by_code_prefix    : 지역 코드 접두 확인(AI 가 추측한 행정코드 — 검증 불가)
    입력: audit_result_enriched.json 이 있으면 우선(제안값 포함), 없으면 audit_result.json.
    출력: audit_result_reviewed.json (원본 보존)
    """
    import os

    if in_path is None:
        enriched = _out_path("audit_result_enriched.json")
        in_path = (
            enriched if os.path.exists(enriched) else _out_path("audit_result.json")
        )
    out_path = out_path or _out_path("audit_result_reviewed.json")
    print(f"[입력] {os.path.basename(in_path)}")
    doc = json.load(open(in_path, encoding="utf-8"))
    results = doc.get("results", [])

    # ── 1) 배제반경 확인 ────────────────────────────────────────────
    radius_jobs = [
        (r, f)
        for r in results
        for f in r.get("hitl_flags", [])
        if f.get("type") == "exclusion_radius_missing" and not f.get("confirmed")
    ]
    if not radius_jobs:
        print("[HITL] 배제반경 확인 대상 없음.")
    else:
        print(f"\n{'#' * 60}\n# 배제반경 확인 — {len(radius_jobs)}건")
        print("#  AI 제안값은 확정이 아닙니다. 출처를 보고 승인하거나 수정하세요.")
        print("#" * 60)
    for r, f in radius_jobs:
        idx = f.get("role_index", 0)
        roles = r.get("roles", [])
        role = roles[idx] if idx < len(roles) else {}
        ftype = role.get("facility_type", "?")
        etype = role.get("exclusion_type", "radius")

        print("\n" + "=" * 60)
        print(f"[{r.get('dataset_id', '')}] {ftype}  (배제 방식: {etype})")
        print(f"  데이터: {r.get('summary', '')}")
        print(f"  AI 판단근거: {role.get('rationale', '')}")

        제안 = f.get("제안값")
        if 제안 is not None:
            src = f.get("출처") or "?"
            근거 = f.get("근거문장") or ""
            print(f"\n  ▶ AI 제안: {제안}m   (출처: {src})")
            if 근거:
                print(f"    근거문장: {근거[:110]}")
            if f.get("근거_시설_일치") is False:
                print(
                    f"    ⚠ 근거-시설 불일치 — 근거문장에 '{ftype}'가 없습니다."
                    f" 다른 시설 규정일 수 있으니 반드시 확인하세요."
                )
        else:
            why = (
                "조례가 없어 검색을 생략했습니다"
                if not f.get("source_type")
                else "검색에서 근거를 찾지 못했습니다"
            )
            print(f"\n  ▶ AI 제안 없음 — {why}. 직접 입력이 필요합니다.")

        radius = _read_radius(default=제안)
        if radius == "skip":
            print("  → 건너뜀 (미확정 유지 — 이 상태로는 STEP2 가 시작되지 않는다)")
            continue
        apply_radius_answer(r, f, radius)
        if radius is None:
            print("  → 반경 없음(면 배제 등)으로 확정")
        else:
            print(f"  → {radius}m 확정 (이 실행에만 적용 — 다음 실행에서 다시 묻는다)")

    # ── 2) 데이터 용도 확인 ─────────────────────────────────────────
    pending = [
        r
        for r in results
        if any(f.get("type") == "data_intent_unclear" for f in r.get("hitl_flags", []))
    ]
    if not pending:
        print("\n[HITL] 의도 확인 대상 없음(data_intent_unclear 0).")
    else:
        print(f"\n{'#' * 60}\n# 데이터 용도 확인 — {len(pending)}건\n{'#' * 60}")
    for r in pending:
        print("\n" + "=" * 60)
        print(f"[{r.get('dataset_id', '')}] {r.get('summary', '')}")
        print("  이 데이터를 어떤 용도로 넣으셨나요?")
        print("   1) 가점(수요)  2) 감점(민감도)  3) 배제(금지)")
        print("   4) 위치선정 참조용(감리 입력 아님)  5) 잘못 넣음·제외")
        choice = _read_int("  선택(1~5): ", 1, 5)
        weight = _read_weight() if choice in (1, 2) else None
        apply_intent_answer(r, choice, weight)
        role = r["roles"][0]["role"] if r["roles"] else "excluded"
        tail = f", weight={r['roles'][0]['weight']}" if choice in (1, 2) else ""
        print(f"  → {role} 확정{tail}")
        if choice in (3, 4):
            print("    ※ 표시만 — 위치선정(GIS) 단계에서 참고/처리")

    # ── 3) 지역 코드 접두 확인 ──────────────────────────────────────
    #  filter_by_code_prefix 는 감리 AI 가 '행정 코드'라는 외부 지식을 알아야 하는
    #  유일한 op 다. 다른 경로(좌표·주소·자치구명)는 데이터 안에서 검증되지만 이건 아니다.
    #  실제 사고: 용산구(11170) 대신 마포구(11440) 를 써서 데이터 전체가 다른 구였는데,
    #  마포구도 행정동이 16개라 행수 검증(11904=16x24x31)을 통과해 조용히 넘어갔다.
    code_jobs = [
        (r, op)
        for r in results
        for op in (r.get("cleaning_ops") or [])
        if op.get("op_id") == "filter_by_code_prefix"
    ]
    if code_jobs:
        region = (doc.get("facility_inference", {}) or {}).get("region", "")
        print(f"\n{'#' * 60}\n# 지역 코드 확인 — {len(code_jobs)}건")
        print(f"#  AI 가 '{region}' 의 행정 코드를 추측한 값입니다. 반드시 확인하세요.")
        print("#  (틀려도 행수가 그럴듯하게 나와 자동 검증으로는 못 걸러냅니다)")
        print("#" * 60)
        for r, op in code_jobs:
            prm = op.setdefault("params", {})
            cur = prm.get("prefix", "")
            # 감리 단계에서 이미 판정했으면 그대로 쓴다 — CLI 와 프런트가 같은 답을 본다.
            #   단 verdict=="unknown" 은 **판정이 아니라 '판정 못 함'** 이다.
            #   코드표가 없던 실행에서 박힌 값을 그대로 쓰면, 코드표를 고쳐도
            #   HITL 이 계속 '(대조 불가)' 를 보여준다 → STEP1 재실행이 강요된다.
            chk = prm.get("prefix_check")
            if not chk or chk.get("verdict") == "unknown":
                chk = resolve_code_prefix(
                    cur, region, _code_samples(r.get("dataset_id"), prm.get("col"))
                )
                prm["prefix_check"] = chk
            hint = chk.get("suggestion")
            print(f"\n[{r.get('dataset_id')}] {r.get('summary', '')[:60]}")
            print(f"  컬럼 '{prm.get('col')}' 이 '{cur}' 로 시작하는 행만 남깁니다.")
            print(f"  코드표: {chk.get('detail') or '(대조 불가)'}")
            if chk.get("status") == "auto_confirmed":
                prm["prefix_confirmed"] = True
                prm["prefix_confirmed_by"] = "code_table" + (
                    f":{chk['system']}" if chk.get("system") else ""
                )
                print(f"  ✅ {chk.get('resolved') or region} — {chk.get('reason')}")
                print("     → 코드표로 확정 (사람 확인 생략)")
                continue
            print(f"  ⚠ 확인 필요 [{chk.get('verdict')}] — {chk.get('reason')}")
            if chk.get("resolved"):
                print(f"     이 접두는 실제로 '{chk['resolved']}' 입니다.")
            if hint:
                print(f"     참고: '{region}' 의 자치구 코드는 '{hint}' 입니다.")
            default = hint if (chk.get("verdict") == "mismatch" and hint) else cur
            print(f"  ▶ 이 코드가 '{region}' 이 맞습니까?")
            ans = input(
                f"  [Enter='{default}' 적용] · 다른 코드 입력 · s=건너뜀: "
            ).strip()
            if not ans:
                ans = default if default != cur else ""
            if ans.lower() == "s":
                print("  → 건너뜀 (미확인 상태로 진행 — 결과가 다른 지역일 수 있음)")
                prm["prefix_confirmed"] = False
                continue
            if ans:
                prm["prefix"] = ans
                print(f"  → '{ans}' 로 수정")
            else:
                print(f"  → '{cur}' 확정")
            prm["prefix_confirmed"] = True

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(doc, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 미확정 잔여 요약(건너뛴 것) — 위치선정 전에 반드시 처리해야 함
    left = [
        (
            r.get("dataset_id"),
            (r.get("roles", []) + [{}])[f.get("role_index", 0)].get(
                "facility_type", "?"
            ),
        )
        for r in results
        for f in r.get("hitl_flags", [])
        if f.get("type") == "exclusion_radius_missing" and not f.get("confirmed")
    ]
    unconf = [
        r.get("dataset_id")
        for r in results
        for op in (r.get("cleaning_ops") or [])
        if op.get("op_id") == "filter_by_code_prefix"
        and not (op.get("params") or {}).get("prefix_confirmed")
    ]
    if unconf:
        print(
            f"\n⚠ 미확인 지역코드 {len(unconf)}건: {', '.join(unconf)} "
            f"— 다른 지역 데이터일 수 있습니다."
        )
    if left:
        print(
            f"\n⚠ 미확정 배제반경 {len(left)}건 남음 (건너뜀): "
            f"{', '.join(f'{d}:{t}' for d, t in left)}"
        )
        print("  위치선정(GIS) 단계 전에 다시 hitl 을 실행해 확정하세요.")
    print(f"\n[저장] {out_path}")
    return out_path


def save_results(
    judgments: list[Judgment],
    raw_preds: dict,
    model: str,
    out_dir: str | None = None,
    facility_info: dict | None = None,
) -> str:
    """감리 판정을 하나의 JSON으로 저장. 최상단 _schema에 필드 설명 포함(자기설명적).
    다음 단계(지오코딩·정제)가 이 파일만 보고 각 필드 의미를 알 수 있다."""
    import os
    from datetime import datetime

    out_dir = out_dir or STEP1_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    doc = {
        "_schema": {
            "설명": "OmniSite 감리 AI 1단계 산출물. 각 데이터셋의 역할·좌표상태·정제지시.",
            "생성모델": model,
            "생성시각": datetime.now().isoformat(timespec="seconds"),
            "필드설명": {
                "dataset_id": "데이터셋 식별자 (01, 02 … — data/ 파일명 가나다순 자동 부여)",
                "summary": "이 데이터가 대상 시설 입지에서 어떤 역할인지 한 줄 요약 (사람 HITL 확인용)",
                "roles": "입지 판단에서의 의미 role 리스트(공존 가능). 아래 role_types 참조",
                "coord_status": "좌표 상태. 다음 단계(지오코딩)가 이 값으로 처리 분기. 아래 coord_types 참조",
                "cleaning_ops": "정제에 필요한 연산 리스트(op_id + params). 정제 단계가 실행할 지시서",
                "hitl_flags": "사람 검토가 필요한 항목. role_index 로 이 데이터셋의 roles[i] 를 가리킴",
            },
            "role_types": {
                "positive_factor": "설치 수요를 높이는 가점 요인. weight(+, 0~1) 동반",
                "negative_factor": "갈등·민감도를 높이는 감점 요인. weight(-, -1~0) 동반",
                "hard_exclusion": "조례·법령상 설치 금지. weight 대신 배제반경_m·source·confirmed 동반",
                "reference_only": "입지 판정의 입력 팩터가 아님(참조·하류·무관). "
                "data_intent_unclear 플래그로 사람에게 용도를 되묻는다",
            },
            "role_필드": {
                "weight": "가중치 대략값(-1~1). HITL로 사람이 최종 조정",
                "exclusion_type": "배제 방식. radius=점+버퍼(반경 배제), polygon=구역 경계로 배제(면)",
                "배제반경_m": "radius일 때 배제 버퍼 반경(m). polygon이거나 미확정이면 null",
                "source": "LLM 이 제시한 배제 근거(조항 등). ※ confirmed=false 면 조례 대조에서 "
                "검증되지 않은 값 — HITL 에서 사람이 판단 근거로 참고만 할 것",
                "confirmed": "조례 본문 대조로 코드가 검증한 경우만 true. "
                "LLM 자가판정은 신뢰하지 않음(false → 검색·HITL)",
                "need_review": "true면 사람 확인 필요(조례 미명시·미확정)",
                "rationale": "판정 근거",
            },
            "coord_types": {
                "has_coords": "좌표 컬럼 이미 있음 → 그대로 사용",
                "needs_geocoding": "좌표 없고 주소만 있음 → 다음 단계에서 지오코딩 필요",
                "stat_join": "좌표 없는 통계 → 마스터/경계와 조인·공간조인으로 위치 부여",
                "spatial": "폴리곤(경계·지적도) 자체가 공간정보",
            },
            "주의": "roles·coord_status는 감리 AI 제안값이며 HITL 검토 후 확정됩니다.",
        },
        "results": [raw_preds[j.dataset_id] for j in judgments],
    }
    if facility_info is not None:
        doc["facility_inference"] = {
            "facility": facility_info.get("facility"),
            "region": facility_info.get("region"),
            "근거": facility_info.get("근거"),
            "mismatch": facility_info.get("mismatch", False),
            "mismatch_reason": facility_info.get("mismatch_reason", ""),
            "source_input": facility_info.get("source_input", ""),
            "confirmed": False,  # HITL 확인 대상
            "_설명": "사용자 입력+데이터명으로 확정한 선정 시설. HITL에서 확인/수정 후 confirmed=true.",
        }
    path = _out_path("audit_result.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    return path


def report(judgments: list[Judgment], raw_preds: dict | None = None) -> None:
    """감리 판정 리포트. 확정은 HITL(사람)에서 — 여기 출력은 검토 보조."""
    n = len(judgments)
    exclusion_review = []
    for j in judgments:
        role_str = (
            "+".join(
                r.get("role", "?")
                .replace("_factor", "")
                .replace("hard_exclusion", "배제")
                for r in j.roles
            )
            or "-"
        )
        print(f"[{j.dataset_id}] {role_str:24} 좌표:{j.coord_status:15}")
        print(f"     요약: {j.summary}")
        if j.exclusions:
            exclusion_review.append(j)
    print("-" * 92)
    n_excl = sum(
        1 for j in judgments if any(r.get("role") == "hard_exclusion" for r in j.roles)
    )
    n_pos = sum(
        1 for j in judgments if any(r.get("role") == "positive_factor" for r in j.roles)
    )
    n_ref = sum(
        1 for j in judgments if any(r.get("role") == "reference_only" for r in j.roles)
    )
    print(f"판정 완료: {n}개 데이터셋  (배제 {n_excl} · 가점 {n_pos} · 참조 {n_ref})")

    if exclusion_review:
        print(
            "\n[배제반경 검토 — 조례에 반경 미명시. 사람이 확인/입력 (search 로 후보 주입 가능)]"
        )
        for j in exclusion_review:
            for r in j.exclusions:
                print(
                    f"  {j.dataset_id}: 배제 대상이나 반경 미확정 → {r.get('rationale', '')[:60]}"
                )

    # 다음 단계(지오코딩)로 넘길 대상 요약
    geo = [j.dataset_id for j in judgments if j.coord_status == "needs_geocoding"]
    print(f"\n[다음 단계(지오코딩) 대상] 좌표 없어 지오코딩 필요: {geo or '없음'}")

    # HITL 대기 flag 요약(코드가 자동 생성한 것)
    if raw_preds:
        flag_items = [
            (did, f) for did, p in raw_preds.items() for f in p.get("hitl_flags", [])
        ]
        if flag_items:
            print(f"\n[HITL 대기 — 사람 입력 필요] {len(flag_items)}건")
            for did, f in flag_items:
                print(f"  {did}: {f.get('type')} — {f.get('message', '')}")


# ══════════════════════════════════════════════════════════════════
# 4. 데이터셋 프로파일 — profile.py 로 폴더 파일을 읽어 생성
# ══════════════════════════════════════════════════════════════════
# build_fixtures(폴더) = profile_folder() 출력 + 조례(a안: 전 데이터셋 주입).
# null_coords/has_addr_col/sample_rows/조례가 감리 판정의 근거.


# 조례 로드 — 소스 추상화. 지금은 업로드된 파일이지만, 나중에 DB/프론트 전달값으로
# 바꿔도 이 함수 내부만 교체하면 됨(호출부 불변).
def load_ordinance(source: str | None = None) -> str:
    """조례 텍스트를 로드. source 우선순위:
      1) source 가 조례 텍스트 자체(개행 포함 긴 문자열)면 그대로 사용 (프론트/DB 직접 전달)
      2) source 가 폴더 경로면 그 폴더의 모든 텍스트 파일을 읽어 합침
      3) None 이면 config 의 ORDINANCE_DIR(기본 ./law) 폴더 전체
    ── 추후 DB/프론트 전환 시 이 함수만 교체(예: return db.fetch_ordinances(region, facility)).
    법령 폴더에 조례+시행규칙 등 여러 파일을 넣으면 모두 합쳐 ordinance_rag 로 쓴다.
    """
    import os
    import glob
    from app.config import ORDINANCE_DIR

    # 1) 텍스트 직접 전달(프론트/DB)
    if source and ("\n" in source) and not os.path.exists(source):
        return source
    # 2/3) 폴더에서 텍스트 파일 수집 (기본: 현재 도메인의 law/, 없으면 config 기본)
    folder = source or _DOMAIN["law"] or ORDINANCE_DIR
    if not os.path.isdir(folder):
        return ""
    # PDF·DOCX·HWPX 만 있어도 읽히도록 먼저 텍스트로 변환(옆에 .txt 캐시).
    #   추출은 부가 기능이라 의존 패키지가 없으면 건너뛰고 진행한다.
    try:
        from app.services.gam2_doc_extract import ensure_text_files

        ensure_text_files(folder)
    except Exception as e:
        print(f"  ⚠ 문서 텍스트 추출 생략({e})")
    parts = []
    for path in sorted(
        glob.glob(os.path.join(folder, "*.txt"))
        + glob.glob(os.path.join(folder, "*.md"))
    ):
        try:
            with open(path, encoding="utf-8") as f:
                parts.append(f"[{os.path.basename(path)}]\n" + f.read())
        except OSError:
            continue
    return "\n\n".join(parts)


def build_fixtures(profiles_path: str | None = None,
                   force_profile: bool = False) -> dict:
    """fixture/profiles.json 로드 → 조례 (a)안 전 데이터셋 주입.
    profiles.json 이 없으면 profile.py 로 자동 생성한다(data/ 프로파일링).

    🔴 `force_profile=True` 면 **있어도 다시 만든다.** 이 파일은 `data/` 의 사본이지
       독립된 입력이 아닌데, 없을 때만 만들면 원본이 바뀌어도 낡은 사본이 계속
       이긴다 — 파일이 늘거나 줄어도 감리 AI 는 옛 목록을 본다(예외 없이 값만 틀린다).
       실제로 재활용 도메인에서 지운 데이터셋 2개가 프로파일에 남아 있었다
       (2026-08-12). 호출자는 `full` 모드뿐이다 — 거기서만 원본이 바뀔 수 있다.
    """
    path = profiles_path or _DOMAIN["profiles"]
    if not path:
        raise RuntimeError("도메인 미설정 — set_domain(<도메인폴더>) 먼저 호출 필요")

    if force_profile or not os.path.isfile(path):
        # fixture 없음(또는 강제 재생성) → data/ 를 프로파일링 (무슨 상황인지 출력)
        from app.services.gam2_profile import profile_folder, save_profiles

        data_dir = _DOMAIN["data"]
        print(f"[fixture {'재생성' if os.path.isfile(path) else '없음'}] {path}")
        if not os.path.isdir(data_dir):
            raise FileNotFoundError(
                f"데이터 폴더도 없음: {data_dir}\n"
                f"  → <도메인>/data/ 에 원본(csv·xlsx·shp·json)을 넣으세요."
            )
        print(f"[자동 프로파일링] {data_dir} 를 읽어 fixture 를 생성합니다...")
        profiles = profile_folder(data_dir)
        if not profiles:
            raise RuntimeError(
                f"프로파일 0건 — {data_dir} 에 읽을 수 있는 데이터 파일이 없습니다."
            )
        save_profiles(profiles, path)
        print(f"[자동 프로파일링 완료] {len(profiles)}개 데이터셋 → {path}\n")

    with open(path, encoding="utf-8") as f:
        profiles = json.load(f)
    ordinance = load_ordinance()  # <도메인>/law/ 조례
    if not ordinance:
        print(
            f"[조례 없음] {_DOMAIN['law']} 에 조례(txt/md) 없음 "
            f"— 모든 배제가 미확정(HITL)으로 처리됩니다."
        )
    for p in profiles.values():
        p["ordinance"] = ordinance  # (a) 전 데이터셋 주입
    return profiles


def require_region(fac: dict) -> str:
    """시설 확정 결과(`resolve_facility*`)에서 대상 지역을 꺼낸다. 없으면 멈춘다.

    🔴 예전엔 `DOMAIN = {"facility": "흡연부스", "region": "용산구"}` 를 두고
       `fac.get("region") or DOMAIN["region"]` 로 채웠다(2026-08-10 제거, 사람 승인).
       주석엔 "테스트용 폴백"이라 적혀 있었지만 실제로는 **실행 경로 3곳**에서 쓰였다
       (`gam2_run_pipeline.py:191`·이 파일 real/mock 진입점 2곳).
       MVP 도메인 값이라 성동구 입력에서 지역이 안 잡히면 조용히 **용산구** 조례·상위법을
       검색한다 — 안 터지고 근거만 틀린다. "엔진은 그대로, 데이터만 바꾼다"에도 어긋난다.
    """
    region = str(fac.get("region") or "").strip()
    if not region:
        raise SystemExit(
            "🔴 대상 지역을 확정하지 못했다. 지역을 추측하지 않는다. "
            "입력에 '<시군구>' 를 포함할 것 (예: \"용산구 흡연부스 부지 선정\"). "
            f"입력: {fac.get('source_input', '')!r}"
        )
    return region


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]

    USAGE = (
        "사용법:\n"
        '  python audit_judgment_test.py real "<입력>" <도메인폴더>\n'
        '  python audit_judgment_test.py "<입력>" <도메인폴더>          (mock)\n'
        "  python audit_judgment_test.py search <도메인폴더>\n"
        "  python audit_judgment_test.py hitl <도메인폴더>\n"
        "  ※ 먼저 python profile.py <도메인폴더> 로 fixture 생성 필요."
    )
    if not args:
        print(USAGE)
        sys.exit(1)

    # 도메인 폴더는 항상 마지막 위치 인자. set_domain 으로 경로·프리픽스 확정.
    mode = args[0] if args[0] in ("real", "search", "hitl") else "mock"
    domain_dir = args[-1]
    if (mode == "mock" and len(args) < 2) or (mode != "mock" and len(args) < 2):
        print(USAGE)
        sys.exit(1)
    set_domain(domain_dir)
    print(f"[도메인] {domain_dir}  (프리픽스: {_DOMAIN['prefix']})")

    if mode == "hitl":
        review_hitl()
    elif mode == "search":
        print("[배제반경 서핑] 조례에 반경 없는 배제 대상만 web_search 로 후보 제시")
        print("※ 제안값은 확정 아님 — 반드시 사람이 출처 확인 후 확정하세요.\n")
        enrich_with_search()
    elif mode == "real":
        # python audit_judgment_test.py real "강남구 EV 충전소 선정" EV_데이터셋
        from app.config import AUDIT_LLM_MODEL, FACILITY_LLM_MODEL

        user_input = args[1] if len(args) > 2 else ""
        fixtures = build_fixtures()  # fixture/profiles.json 로드(+조례 주입)
        print(f"[fixture] {_DOMAIN['profiles']} → {len(fixtures)}개 프로파일\n")
        fac = resolve_facility(user_input, fixtures)
        print(
            f"[시설 확정] '{fac['facility']}' / 지역 '{fac.get('region', '')}' (모델: {FACILITY_LLM_MODEL})"
        )
        print(f"  근거: {fac['근거']}")
        if fac.get("mismatch"):
            print(f"  ⚠ 입력↔데이터 불일치: {fac['mismatch_reason']}")
        print("  ※ 확정 아님 — HITL에서 확인/수정 필요\n")
        domain = {
            "facility": fac["facility"],
            "region": require_region(fac),
        }
        print(f"[감리 AI 검수 리포트] 모델: {AUDIT_LLM_MODEL}")
        print("※ 배제반경 미확정·애매 데이터는 아래 HITL 대기로 넘어갑니다.\n")
        judgments, raw_preds = run_harness(RealLLM(), fixtures, domain)
        report(judgments, raw_preds)
        path = save_results(judgments, raw_preds, AUDIT_LLM_MODEL, facility_info=fac)
        print(f"\n[저장] {path}")
    else:
        # mock: python audit_judgment_test.py "강남구 EV 충전소 선정" EV_데이터셋
        user_input = args[0] if len(args) > 1 else "부지 선정해줘"
        fixtures = build_fixtures()
        print(f"[fixture] {_DOMAIN['profiles']} → {len(fixtures)}개 프로파일")
        fac = resolve_facility_mock(user_input, fixtures)
        print(f"[시설 확정(mock)] '{fac['facility']}'  (입력: {user_input})\n")
        domain = {
            "facility": fac["facility"],
            "region": require_region(fac),
        }
        print("[MockLLM 검수 리포트] — 하네스 출력 형식 확인용\n")
        judgments, raw_preds = run_harness(MockLLM(), fixtures, domain)
        report(judgments, raw_preds)
        path = save_results(judgments, raw_preds, "mock", facility_info=fac)
        print(f"\n[저장] {path}")
