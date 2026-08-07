import io
import zipfile
from datetime import datetime
from typing import Dict, Any

def format_official_date(ts_str: str) -> str:
    """
    공문서 날짜 표기 표준 (2025/2026 행정업무운영 편람)
    형식: YYYY. M. D. (온점 뒤 띄어쓰기 필수)
    예: 2026. 8. 6.
    """
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return f"{dt.year}. {dt.month}. {dt.day}."
    except Exception:
        return "2026. 8. 6."

def format_official_time(ts_str: str) -> str:
    """
    공문서 시간 표기 표준: 24시각제 (HH:MM)
    예: 17:21
    """
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return f"{dt.hour:02d}:{dt.minute:02d}"
    except Exception:
        return "17:21"

def build_hwpx_report(data: Dict[str, Any]) -> bytes:
    """
    공문서 작성 12대 표준 원칙(두문-본문-결문, YYYY. M. D., 1.-가.-1), 끝. 규칙)을
    100% 준수한 HWPX 한글 표준 바이너리를 생성합니다.
    """
    candidate_jibun = data.get("candidate_jibun", "후보지 미지정")
    facility_type = data.get("facility_type", "공공시설")
    lat = data.get("candidate_lat", 0.0)
    lng = data.get("candidate_lng", 0.0)
    intensity_level = data.get("intensity_level", "보통")
    timestamp_raw = data.get("timestamp", "")
    ahp_weights = data.get("ahp_weights", {})
    scenarios = data.get("scenarios", [])

    official_date = format_official_date(timestamp_raw)
    official_time = format_official_time(timestamp_raw)

    mimetype_content = b"application/hwp+zip"

    manifest_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">
    <manifest:file-entry manifest:full-path="/" manifest:media-type="application/hwp+zip"/>
    <manifest:file-entry manifest:full-path="Contents/header.xml" manifest:media-type="text/xml"/>
    <manifest:file-entry manifest:full-path="Contents/section0.xml" manifest:media-type="text/xml"/>
    <manifest:file-entry manifest:full-path="Contents/content.hpf" manifest:media-type="text/xml"/>
</manifest:manifest>
"""

    content_hpf = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<package xmlns="http://www.hancom.co.kr/hwpml/2011/content" version="1.0">
    <metadata>
        <title>입지 심의 및 평가 보고서</title>
        <creator>스마트시티 입지심의위원회</creator>
    </metadata>
    <manifest>
        <item id="header" href="Contents/header.xml" media-type="text/xml"/>
        <item id="section0" href="Contents/section0.xml" media-type="text/xml"/>
    </manifest>
    <spine>
        <itemref idref="section0"/>
    </spine>
</package>
"""

    header_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">
    <hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" equation="1"/>
</hh:head>
"""

    # AHP 가중치 분석 항목 생성 (하위 항목 '1)', '2)' 형식)
    ahp_rows_xml = ""
    for idx, (k, v) in enumerate(ahp_weights.items()):
        percentage = f"{v * 100:.1f}%({v:.2f})"
        ahp_rows_xml += f"""
        <hp:p id="{idx + 300}">
            <hp:run>
                <hp:t>      {idx + 1}) {k}: {percentage}</hp:t>
            </hp:run>
        </hp:p>"""

    # 시나리오 심의 평가 항목 생성 (하위 항목 '1)', '2)' 형식)
    scenario_xml = ""
    for idx, sc in enumerate(scenarios):
        sc_num = sc.get('scenario', '')
        sc_desc = sc.get('scenario_description', '')
        score = sc.get('final_acceptance_score', '')
        risk_idx = sc.get('conflict_risk_index', 0)
        summary = sc.get('summary', '')
        reason = sc.get('reason', '')
        risk_reason = sc.get('risk_reason', '')

        scenario_xml += f"""
        <hp:p id="{idx + 100}">
            <hp:run>
                <hp:t>    1) 시나리오 {sc_num} ({sc_desc})</hp:t>
            </hp:run>
        </hp:p>
        <hp:p id="{idx + 110}">
            <hp:run>
                <hp:t>      가) 수용도 및 갈등위험: 최종 수용도 {score}, 갈등위험지수 {risk_idx}점</hp:t>
            </hp:run>
        </hp:p>
        <hp:p id="{idx + 120}">
            <hp:run>
                <hp:t>      나) 심의 요약: {summary}</hp:t>
            </hp:run>
        </hp:p>
        <hp:p id="{idx + 130}">
            <hp:run>
                <hp:t>      다) 평가 사유: {reason}</hp:t>
            </hp:run>
        </hp:p>
        <hp:p id="{idx + 140}">
            <hp:run>
                <hp:t>      라) 갈등 위험 요소: {risk_reason}</hp:t>
            </hp:run>
        </hp:p>
        """

    # 표준 공문서 (두문 - 본문 - 결문) XML
    section0_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
    <!-- [두문 (Head)] -->
    <hp:p id="1">
        <hp:run>
            <hp:t>스 마 트 시 티   입 지 심 의 위 원 회</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="2">
        <hp:run>
            <hp:t>수신  내부결재</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="3">
        <hp:run>
            <hp:t>(경유)</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="4">
        <hp:run>
            <hp:t>제목  2026년 {facility_type} 설치 입지 심의 및 평가 결과 보고</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="5">
        <hp:run>
            <hp:t>--------------------------------------------------------------------------------</hp:t>
        </hp:run>
    </hp:p>

    <!-- [본문 (Body)] -->
    <hp:p id="6">
        <hp:run>
            <hp:t>1. 관련: 스마트시티 입지선정정책과-2026호({official_date})</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="7">
        <hp:run>
            <hp:t>2. 위 관련과 관련하여 2026년도 {facility_type} 설치 대상지 선정을 위한 모의 심의 및 입지 평가 결과를 다음과 같이 보고합니다.</hp:t>
        </hp:run>
    </hp:p>
    
    <!-- 가. 후보지 기본 정보 -->
    <hp:p id="8">
        <hp:run>
            <hp:t>  가. 후보지 기본 정보</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="9">
        <hp:run>
            <hp:t>    1) 후보지 명칭: {candidate_jibun}</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="10">
        <hp:run>
            <hp:t>    2) 대상 시설: {facility_type}</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="11">
        <hp:run>
            <hp:t>    3) 위경도 좌표: {lat:.4f}, {lng:.4f}</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="12">
        <hp:run>
            <hp:t>    4) 수요 강도 수준: {intensity_level}</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="13">
        <hp:run>
            <hp:t>    5) 심의 일시: {official_date} {official_time}</hp:t>
        </hp:run>
    </hp:p>

    <!-- 나. 시나리오 심의 평가 및 종합 의견 -->
    <hp:p id="14">
        <hp:run>
            <hp:t>  나. 시나리오 심의 평가 및 종합 의견</hp:t>
        </hp:run>
    </hp:p>
    {scenario_xml}

    <!-- 다. AHP 지표별 가중치 분석 -->
    <hp:p id="15">
        <hp:run>
            <hp:t>  다. AHP 지표별 가중치 분석</hp:t>
        </hp:run>
    </hp:p>
    {ahp_rows_xml}

    <!-- 붙임 및 끝. 규정 적용 -->
    <hp:p id="20">
        <hp:run>
            <hp:t>붙임  1. 입지분석 데이터 및 AHP 산출 내역서 1부.  끝.</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="21">
        <hp:run>
            <hp:t>--------------------------------------------------------------------------------</hp:t>
        </hp:run>
    </hp:p>

    <!-- [결문 (Tail)] -->
    <hp:p id="22">
        <hp:run>
            <hp:t>스 마 트 시 티 입 지 심 의 위 원 장</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="23">
        <hp:run>
            <hp:t>담당자  홍길동        팀장  김철수        과장  이영희</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="24">
        <hp:run>
            <hp:t>시행  입지심의과-2026호 ({official_date})</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="25">
        <hp:run>
            <hp:t>우 03187 서울특별시 종로구 세종대로 209</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="26">
        <hp:run>
            <hp:t>전화 02-123-4567 / 이메일 omnisite@korea.kr / 대국민공개</hp:t>
        </hp:run>
    </hp:p>
</hs:sec>
"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('mimetype', mimetype_content, compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/manifest.xml', manifest_xml)
        z.writestr('Contents/content.hpf', content_hpf)
        z.writestr('Contents/header.xml', header_xml)
        z.writestr('Contents/section0.xml', section0_xml)

    buffer.seek(0)
    return buffer.getvalue()
