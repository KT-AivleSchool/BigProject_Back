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

from app import config
from app.services.gam2_audit_ops_catalog import describe_all
from app.config import STEP1_OUTPUT_DIR, SEARCH_CACHE_DIR, EXCLUSION_CACHE_PATH


# ── 도메인 컨텍스트 (실행 시 set_domain 으로 채움; 전까지는 기존 기본값) ──
_DOMAIN = {
    "prefix": "",
    "data": None,
    "law": None,
    "fixture": None,
    "profiles": None,
    "cache_path": EXCLUSION_CACHE_PATH,
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
        cache_path=os.path.join(
            SEARCH_CACHE_DIR, f"{p['prefix']}_exclusion_radius_cache.json"
        ),
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
- hard_exclusion 이면 facility_type 에 시설 유형명을 넣는다(예: "어린이집", "학교",
  "버스정류소", "지하철역", "도시공원"). 이 값은 배제반경 캐시의 키로 쓰이므로 일반적인
  시설 유형명으로 적는다(파일명·데이터셋ID 말고 시설 종류).
- hard_exclusion 의 배제반경_m 은 exclusion_type=radius 일 때만 조례 근거로 채운다.
- **한 데이터셋의 hard_exclusion 은 1개만 낸다.** 데이터에 시설 종류 컬럼이 있어
  여러 유형(어린이집·초등학교·유치원 등)이 섞여 있어도 나누지 마라.
  배제는 현재 데이터셋 단위로 적용되므로, 유형을 나눠도 행마다 다른 반경을 적용할 수 없다
  (같은 데이터셋이 HITL 에 두 번 올라와 사람만 두 번 묻게 된다).
  이 경우 facility_type 은 데이터 전체를 대표하는 이름(예: "어린이보호구역")으로 하고,
  반경은 섞인 유형 중 가장 보수적인(넓은) 값을 쓴다.
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
        f"- region 은 자치구/시군구 단위로(예: '용산구', '강남구'). 조례 검색에 쓰인다. "
        f"사용자 입력에 지역이 있으면 그것을, 없으면 데이터 파일명·내용에서 추론하라.\n"
        f"- 근거를 쓸 때 [데이터 목록]의 실제 파일명을 확인하고 인용하라. 목록에 있는 데이터를 "
        f"'없다'고 하지 마라(예: 담배꽁초·금연구역 파일이 있으면 그것을 근거로 들라).\n"
        f"- 사용자 입력의 시설과 데이터 목록이 안 맞으면(예: 입력은 흡연부스인데 데이터는 전부 EV 관련) "
        f"mismatch=true 로 표시하고 이유를 적어라.\n"
        f"- 사용자 입력이 비었으면 데이터 목록만으로 추론하라.\n"
        f"JSON 하나만 출력(설명 금지):\n"
        f'{{"facility": "<시설명>", "region": "<자치구>", "근거": "<판단 근거>", '
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
    ordinance = (
        ordinance_rag
        if ordinance_rag is not None
        else ([profile["ordinance"]] if profile.get("ordinance") else [])
    )
    facility = domain.get("facility", "대상 시설")
    user = {
        "domain_context": domain,  # {facility, region}
        "dataset": {
            "dataset_id": profile.get("dataset_id"),
            "filename": profile.get("filename"),
            "extension": profile.get("extension"),
            "schema": profile.get("columns"),
            "sample_rows": profile.get("sample_rows", []),
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
                    "facility_type": "시설 유형명(캐시 키). 예: 어린이집·학교·버스정류소",
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
            "allowed 값이 그 컬럼의 sample_rows 에 실제로 나타나는 형태인지 확인하라. "
            "(실패 사례: '행정기관' 컬럼 값은 '왕십리제2동'·'합계' 인데 allowed=['성동구'] 를 걸어 "
            "18행이 0행이 됐다. 컬럼에 없는 값으로 거르면 레이어가 통째로 사라진다) "
            "allowed 에는 '걸러내려는 기준값'만 넣는다 — 지역 필터면 대상 지역명, "
            "운영상태 필터면 남길 상태값. 그리고 데이터가 이미 대상 지역 전용이면(파일명·내용상) "
            "지역 필터 자체를 넣지 마라.",
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
        )  # 프로파일 dataset_id 사용(manifest 기반)
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
        pred = enrich_hitl_flags(pred)  # 배제반경 null 등 → hitl_flags 자동 생성(코드)
        raw_preds[did] = pred
        out.append(review_one(pred, did))
        if progress:
            progress(did)
    return out, raw_preds


def apply_radius_answer(
    result: dict, flag: dict, radius_m: int | None, source: str = "human_confirmed"
) -> None:
    """HITL 답변을 roles·flag 에 반영(메모리). radius_m=None 이면 '반경 없음(면 배제 등)'.
    사람이 확정한 값만 confirmed=true → 캐시 저장(다음 실행에서 재사용).
    """
    idx = flag.get("role_index", 0)
    roles = result.get("roles", [])
    if idx >= len(roles):
        return
    role = roles[idx]
    ftype = role.get("facility_type")

    role["배제반경_m"] = radius_m
    role["confirmed"] = True  # 사람이 확인함 → 확정
    role["need_review"] = False
    role["source"] = source
    flag["제안값"] = radius_m
    flag["confirmed"] = True
    flag["confirmed_by_human"] = True

    # 사람 확정값만 캐시 (반경이 실제로 있는 경우만 — None 은 캐시 의미 없음)
    if ftype and radius_m is not None:
        save_to_exclusion_cache(ftype, radius_m, source, confirmed_by="human")


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
    """HITL 담당자가 서핑 제안값을 확인·확정할 때 호출. confirmed=true 로 바꾸고 캐시에 저장.
    (서핑 제안값은 confirmed=false 라 캐시 안 됨 → 사람이 이 함수로 확정해야 캐시됨)"""
    doc = json.load(open(enriched_path, encoding="utf-8"))
    for r in doc["results"]:
        if not r["dataset_id"].startswith(dataset_id):
            continue
        for f in r.get("hitl_flags", []):
            if f.get("type") != "exclusion_radius_missing":
                continue
            idx = f.get("role_index", 0)
            roles = r.get("roles", [])
            # facility_type 은 flag 에 중복 저장하지 않는다 — role_index 로 roles[i] 에서 조회.
            ftype = roles[idx].get("facility_type") if idx < len(roles) else None
            f["제안값"] = radius_m
            f["confirmed_by_human"] = True
            # roles 쪽도 확정 반영
            if idx < len(r.get("roles", [])):
                r["roles"][idx]["배제반경_m"] = radius_m
                r["roles"][idx]["confirmed"] = True
                r["roles"][idx]["need_review"] = False
            # 사람 확정 → 캐시 저장
            if ftype:
                save_to_exclusion_cache(
                    ftype,
                    radius_m,
                    f.get("출처", "human_confirmed"),
                    confirmed_by="human",
                )
    path = out_path or enriched_path
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return path


def load_exclusion_cache(path: str | None = None) -> dict:
    """시설유형→배제반경 캐시 로드. confirmed=true 로 확인된 값만 들어있다."""
    import os

    path = path or _DOMAIN["cache_path"]
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_to_exclusion_cache(
    facility_type: str,
    radius_m,
    source: str,
    confirmed_by: str,
    path: str | None = None,
) -> None:
    """confirmed=true 값만 캐시에 저장(호출부에서 confirmed 확인). 키=시설유형."""
    import os
    from datetime import date

    path = path or _DOMAIN["cache_path"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cache = load_exclusion_cache(path)
    cache[facility_type] = {
        "배제반경_m": radius_m,
        "출처": source,
        "confirmed_by": confirmed_by,
        "date": date.today().isoformat(),
    }
    json.dump(cache, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _norm(s: str) -> str:
    """조례 대조용 정규화(NFC). 시설유형·값이 조례 텍스트에 있는지 substring 비교에 사용."""
    import unicodedata

    return unicodedata.normalize("NFC", s or "")


def enrich_hitl_flags(pred: dict) -> dict:
    """LLM 판정을 받은 뒤, 사람 검토가 필요한 항목을 코드가 결정론적으로 hitl_flags에 채운다.
    (LLM 판정 실수와 무관하게 항상 보장 — '판정=LLM, 확정=코드' 원칙)
    배제 confirmed 는 LLM 이 emit 한 값을 신뢰하지 않고 코드가 조례로 재판정한다:
      confirmed=True  ⟺  radius 값이 있고 + 시설유형·값이 조례 텍스트에 실제로 있을 때(→캐시)
      그 외(조례에 없음/값 null/polygon) 전부 → confirmed=False + exclusion_radius_missing(검색·HITL).
    캐시 히트(사람·조례로 이미 확정)면 즉시 채움.
    """
    flags = list(pred.get("hitl_flags", []))
    pred.get("dataset_id", "")
    cache = load_exclusion_cache()
    ord_norm = _norm(load_ordinance())  # 현재 도메인 조례 텍스트(검증 근거)
    existing = {(f.get("type"), f.get("role_index")) for f in flags}
    for i, r in enumerate(pred.get("roles", [])):
        if r.get("role") != "hard_exclusion":
            continue
        ftype = r.get("facility_type")
        radius = r.get("배제반경_m")
        is_radius = r.get("exclusion_type", "radius") == "radius"

        # 1) 캐시 히트(사람·조례로 이미 확정된 값) → 즉시 채움
        if is_radius and radius is None and ftype and ftype in cache:
            c = cache[ftype]
            r.update(
                배제반경_m=c["배제반경_m"],
                source=c.get("출처"),
                confirmed=True,
                need_review=False,
                from_cache=True,
            )
            continue

        # 2) 조례 대조: radius 값 존재 + 시설유형·값이 조례에 실제로 있어야만 confirmed
        ftype_in_ord = bool(ftype) and _norm(ftype) in ord_norm
        value_in_ord = radius is not None and str(radius) in ord_norm
        if is_radius and radius is not None and ftype_in_ord and value_in_ord:
            r["confirmed"] = True
            r["need_review"] = False
            save_to_exclusion_cache(
                ftype, radius, r.get("source", "ordinance"), confirmed_by="ordinance"
            )
            continue

        # 3) 그 외 전부 → 미확정. LLM 자가확정(조례 근거 없는 confirmed)을 여기서 false 로 벗긴다.
        #    (조례에 없음 / 값 null / polygon) → 검색·사람 확인 대상.
        #    ※ source·rationale 은 지우지 않는다 — HITL 에서 사람이 판단 근거로 봐야 하므로.
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
                    "message": "배제 대상이나 조례에서 반경/근거 확인 안 됨(LLM 자가판정). 검색·사람 확인 필요.",
                    "제안값": None,
                    "출처": None,
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
    return pred


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
    region: str = "용산구",
    ordinance_rag: str = "",
) -> str:
    """audit_result.json 의 exclusion_radius_missing flag 를, 조례가 인용한 상위법을
    법령 API 로 조회해 배제반경 후보로 채워 별도 저장. 원본 보존, confirmed=false(HITL 확인).
    ordinance_rag: 업로드된 조례 본문(「」 인용 법령 파싱용). 없으면 조례 텍스트 파일 사용."""
    import copy
    import os
    from app.services.gam2_ordinance_acquisition import (
        extract_cited_laws,
        find_radius_in_laws,
    )

    in_path = in_path or _out_path("audit_result.json")
    out_path = out_path or _out_path("audit_result_enriched.json")
    doc = json.load(open(in_path, encoding="utf-8"))
    enriched = copy.deepcopy(doc)

    # 조례 본문에서 인용된 상위법 목록 추출(한 번만)
    rag = ordinance_rag or load_ordinance()
    cited = extract_cited_laws(rag)
    print(f"  조례 인용 상위법: {cited}")

    # ── [조례 없음] 검색 스킵 → HITL 직행 ─────────────────────────────
    # 검색의 출발점은 '조례가 인용한 상위법'이다. 조례가 없으면 법령 API 진입로가 없고,
    # 남는 건 web_search 뿐인데 실측 결과 비용·시간만 쓰고 소득이 없었다(128s, 제안 대부분 null).
    # 애초에 배제반경을 조례에서 정하는 시설(재활용정거장 등)은 조례가 없으면
    # 법·웹 어디에도 근거가 없다 → 사람이 HITL 에서 직접 입력하는 것이 정확하고 싸다.
    if not cited:
        n_missing = sum(
            1
            for r in enriched["results"]
            for f in r.get("hitl_flags", [])
            if f.get("type") == "exclusion_radius_missing"
        )
        print("\n  ※ 조례(또는 인용 상위법) 없음 → 배제반경 검색을 건너뜁니다.")
        print(f"     미확정 배제반경 {n_missing}건은 HITL 에서 직접 확인·입력하세요:")
        print("       python audit_judgment_test.py hitl <도메인폴더>")
        enriched["_schema"]["상위법검색"] = (
            "조례(인용 상위법) 없음 → 검색 생략. 배제반경은 HITL 에서 사람이 입력."
        )
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(enriched, f, ensure_ascii=False, indent=2)
        print(f"\n[검색 생략] 원본 그대로 저장 → {out_path}")
        return out_path
    # ─────────────────────────────────────────────────────────────

    # facility(폴백 web_search 프롬프트용) — 결과 JSON의 facility_inference에서
    facility = (doc.get("facility_inference", {}) or {}).get("facility", "")

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


ADM_CODE_SHEET = "행정동코드"  # 시트명(행자부행정동코드↔시군구명)
_ADM_CODE_CACHE: dict | None = None  # {코드접두: 시군구명} 세션 캐시


def _load_admin_code_map() -> dict:
    """행자부 행정동코드 ↔ 시군구명 매핑을 읽어 {코드접두: 시군구명} 으로 만든다.
    5자리(자치구)와 8자리(행정동) 접두를 모두 담아 어느 길이로 걸러도 검증된다.
    파일이 없으면 빈 dict → 검증을 건너뛰고 HITL 확인만 남는다(조용히 통과시키지 않음).
    """
    global _ADM_CODE_CACHE
    if _ADM_CODE_CACHE is not None:
        return _ADM_CODE_CACHE
    _ADM_CODE_CACHE = {}
    path = getattr(config, "ADM_CODE_MAP", "")
    if not path or not os.path.isfile(path):
        return _ADM_CODE_CACHE
    try:
        import pandas as pd

        df = pd.read_excel(path, sheet_name=ADM_CODE_SHEET, dtype=str, skiprows=1)
        df.columns = [
            "통계청행정동코드",
            "행자부행정동코드",
            "시도명",
            "시군구명",
            "행정동명",
        ][: len(df.columns)]
        for code, gu in zip(df["행자부행정동코드"], df["시군구명"]):
            if not isinstance(code, str) or not isinstance(gu, str):
                continue
            code, gu = code.strip(), gu.strip()
            if not code or not gu:
                continue
            _ADM_CODE_CACHE[code] = gu  # 8자리(행정동)
            _ADM_CODE_CACHE[code[:5]] = gu  # 5자리(자치구)
    except Exception as e:
        print(
            f"  [경고] 행정동 코드표 로드 실패({e}) — 코드 검증 없이 HITL 확인만 수행"
        )
    return _ADM_CODE_CACHE


def verify_code_prefix(prefix: str, region: str) -> tuple:
    """코드 접두가 대상 지역인지 대조. 반환: (판정, 실제지역명|None)
      판정: 'ok'(일치) | 'mismatch'(다른 지역) | 'unknown'(매핑에 없음/표 없음)
    감리 AI 가 추측한 행정코드를 **데이터로 검증**하는 유일한 수단이다.
    (실제 사고: 용산구인데 11440(마포구)을 써서 데이터 전체가 다른 구였다)
    """
    m = _load_admin_code_map()
    if not m or not prefix:
        return "unknown", None
    got = m.get(str(prefix).strip())
    if got is None:
        return "unknown", None
    return ("ok" if got == region else "mismatch"), got


def suggest_code_prefix(region: str) -> str | None:
    """대상 지역명으로 올바른 자치구 코드 접두를 찾아준다(HITL 기본값 제시용)."""
    m = _load_admin_code_map()
    cands = sorted({c for c, g in m.items() if g == region and len(c) == 5})
    return cands[0] if len(cands) == 1 else None


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
            print("  → 건너뜀 (미확정 유지 — 위치선정 전에 다시 확인 필요)")
            continue
        apply_radius_answer(r, f, radius)
        if radius is None:
            print("  → 반경 없음(면 배제 등)으로 확정")
        else:
            print(f"  → {radius}m 확정 (캐시 저장 — 다음 실행부터 재사용)")

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
            verdict, actual = verify_code_prefix(cur, region)
            hint = suggest_code_prefix(region)
            print(f"\n[{r.get('dataset_id')}] {r.get('summary', '')[:60]}")
            print(f"  컬럼 '{prm.get('col')}' 이 '{cur}' 로 시작하는 행만 남깁니다.")
            if verdict == "ok":
                print(f"  ✅ 코드표 대조: '{cur}' = {actual} — 대상 지역과 일치")
            elif verdict == "mismatch":
                print(
                    f"  ❌ 코드표 대조: '{cur}' 는 **{actual}** 입니다. 대상은 '{region}' 입니다!"
                )
                if hint:
                    print(f"     → '{region}' 의 코드는 '{hint}' 입니다.")
            else:
                print("  ⚠ 코드표에서 확인 불가 — 사람이 직접 판단해야 합니다.")
                if hint:
                    print(f"     참고: '{region}' 의 자치구 코드는 '{hint}' 입니다.")
            default = hint if (verdict == "mismatch" and hint) else cur
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
                "dataset_id": "데이터셋 식별자 (01, 02 … — data/_manifest.json 의 매핑값)",
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


def build_fixtures(profiles_path: str | None = None) -> dict:
    """fixture/profiles.json 로드 → 조례 (a)안 전 데이터셋 주입.
    profiles.json 이 없으면 profile.py 로 자동 생성한다(data/ 프로파일링).
    """
    path = profiles_path or _DOMAIN["profiles"]
    if not path:
        raise RuntimeError("도메인 미설정 — set_domain(<도메인폴더>) 먼저 호출 필요")

    if not os.path.isfile(path):
        # fixture 없음 → data/ 를 프로파일링해서 자동 생성 (무슨 상황인지 출력)
        from app.services.gam2_profile import profile_folder, save_profiles

        data_dir = _DOMAIN["data"]
        print(f"[fixture 없음] {path}")
        if not os.path.isdir(data_dir):
            raise FileNotFoundError(
                f"데이터 폴더도 없음: {data_dir}\n"
                f"  → <도메인>/data/ 에 원본(csv·xlsx·shp)과 _manifest.json 을 넣으세요."
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


# 테스트용 폴백 기본값. 실제 실행 시 resolve_facility 가 사용자 입력에서 facility·region 을
# 추출해 이 값을 대체한다(도메인 무관). 사용자 입력이 비었을 때만 이 값이 쓰인다.
DOMAIN = {"facility": "흡연부스", "region": "용산구"}


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
            "region": fac.get("region") or DOMAIN["region"],
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
            "region": fac.get("region") or DOMAIN["region"],
        }
        print("[MockLLM 검수 리포트] — 하네스 출력 형식 확인용\n")
        judgments, raw_preds = run_harness(MockLLM(), fixtures, domain)
        report(judgments, raw_preds)
        path = save_results(judgments, raw_preds, "mock", facility_info=fac)
        print(f"\n[저장] {path}")
