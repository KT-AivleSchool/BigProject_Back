# -*- coding: utf-8 -*-
"""§1 프롬프트 빌더 + 시설 확정 — 카탈로그를 **동적 주입**한다(op 목록을 코드에 안 박는다)."""
from __future__ import annotations

import json
import re

from app.services.gam2_audit_ops_catalog import describe_all

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
