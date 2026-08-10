import io
import zipfile
import urllib.parse
from datetime import datetime
from typing import Dict, Any
from xml.sax.saxutils import escape as xml_escape

import re

def strip_emojis(text: str) -> str:
    """
    행정 공문서 표준 규격 준수를 위해 텍스트 내 모든 유니코드 이모지/이모티콘을 제거합니다.
    """
    if not isinstance(text, str):
        return text
    emoji_pattern = re.compile(
        r"[\U00010000-\U0010FFFF"
        r"\u2600-\u27BF"
        r"\u2300-\u23FF"
        r"\u2B50\u2B55\u2934\u2935"
        r"\u2190-\u21FF"
        r"]+",
        flags=re.UNICODE
    )
    return emoji_pattern.sub("", text).strip()

def format_official_date(ts_str: str) -> str:
    """
    공문서 날짜 표기 표준 (2025/2026 행정업무운영 편람)
    형식: YYYY. M. D. (온점 뒤 띄어쓰기 필수)
    """
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now()
    return f"{dt.year}. {dt.month}. {dt.day}."

def format_official_time(ts_str: str) -> str:
    """
    공문서 시간 표기 표준: 24시각제 (HH:MM)
    """
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now()
    return f"{dt.hour:02d}:{dt.minute:02d}"

def generate_qr_png_bytes(url: str) -> bytes:
    """
    URL을 기반으로 PNG 포맷의 QR 코드 이미지 바이너리를 생성합니다.
    """
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=4,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return b""

def build_hwpx_report(data: Dict[str, Any]) -> bytes:
    """
    이모지, 색상, 과도한 디자인 요소를 모두 배제하고
    행정업무운영 편람 표준 규격에 따라 작성된 HWPX 한글 바이너리를 생성합니다.
    (지도 QR 코드 바이너리 이미지 포함)
    """
    raw_jibun = data.get("candidate_jibun", "후보지 미지정")
    raw_addr = data.get("candidate_address") or raw_jibun
    raw_facility = data.get("facility_type", "공공시설")
    raw_intensity = data.get("intensity_level", "보통")

    candidate_jibun = xml_escape(strip_emojis(str(raw_jibun)))
    candidate_address = xml_escape(strip_emojis(str(raw_addr)))
    facility_type = xml_escape(strip_emojis(str(raw_facility)))
    lat = data.get("candidate_lat", 0.0)
    lng = data.get("candidate_lng", 0.0)
    intensity_level = xml_escape(strip_emojis(str(raw_intensity)))
    timestamp_raw = data.get("timestamp", "")
    ahp_weights = data.get("ahp_weights", {})
    scenarios = data.get("scenarios", [])

    encoded_addr = urllib.parse.quote(raw_addr)
    raw_kakao_url = f"https://map.kakao.com/link/map/{encoded_addr},{lat},{lng}"
    raw_naver_url = f"https://map.naver.com/v5/search/{encoded_addr}"
    kakao_map_url = xml_escape(raw_kakao_url)
    naver_map_url = xml_escape(raw_naver_url)

    # QR 코드 PNG 바이너리 동적 생성
    kakao_qr_png = generate_qr_png_bytes(raw_kakao_url)
    naver_qr_png = generate_qr_png_bytes(raw_naver_url)
    has_qr = len(kakao_qr_png) > 0 and len(naver_qr_png) > 0

    official_date = xml_escape(format_official_date(timestamp_raw))
    official_time = xml_escape(format_official_time(timestamp_raw))

    mimetype_content = b"application/hwp+zip"

    manifest_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">
    <manifest:file-entry manifest:full-path="/" manifest:media-type="application/hwp+zip"/>
    <manifest:file-entry manifest:full-path="Contents/header.xml" manifest:media-type="text/xml"/>
    <manifest:file-entry manifest:full-path="Contents/section0.xml" manifest:media-type="text/xml"/>
    <manifest:file-entry manifest:full-path="Contents/content.hpf" manifest:media-type="text/xml"/>
    {"<manifest:file-entry manifest:full-path=\"BinData/image1.png\" manifest:media-type=\"image/png\"/><manifest:file-entry manifest:full-path=\"BinData/image2.png\" manifest:media-type=\"image/png\"/>" if has_qr else ""}
</manifest:manifest>
"""

    content_hpf = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<package xmlns="http://www.hancom.co.kr/hwpml/2011/content" version="1.0">
    <metadata>
        <title>입지 심의 및 평가 결과 보고</title>
        <creator>스마트시티 입지심의위원회</creator>
    </metadata>
    <manifest>
        <item id="header" href="Contents/header.xml" media-type="text/xml"/>
        <item id="section0" href="Contents/section0.xml" media-type="text/xml"/>
        {"<item id=\"image1\" href=\"BinData/image1.png\" media-type=\"image/png\"/><item id=\"image2\" href=\"BinData/image2.png\" media-type=\"image/png\"/>" if has_qr else ""}
    </manifest>
    <spine>
        <itemref idref="section0"/>
    </spine>
</package>
"""

    header_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core">
    <hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" equation="1"/>
    {"<hh:bindataCnt count=\"2\"/><hh:bindataList><hc:bindata id=\"image1\" binDataRef=\"image1\" format=\"png\"/><hc:bindata id=\"image2\" binDataRef=\"image2\" format=\"png\"/></hh:bindataList>" if has_qr else ""}
</hh:head>
"""

    # AHP 가중치 분석 항목
    ahp_rows_xml = ""
    for idx, (k, v) in enumerate(ahp_weights.items()):
        percentage = f"{v * 100:.1f}%({v:.2f})"
        escaped_k = xml_escape(strip_emojis(str(k)))
        ahp_rows_xml += f"""
        <hp:p id="{idx + 300}">
            <hp:run>
                <hp:t>      {idx + 1}) {escaped_k}: {percentage}</hp:t>
            </hp:run>
        </hp:p>"""

    # 시나리오 심의 평가 항목
    scenario_xml = ""
    for idx, sc in enumerate(scenarios):
        sc_num = xml_escape(strip_emojis(str(sc.get('scenario', ''))))
        sc_desc = xml_escape(strip_emojis(str(sc.get('scenario_description', ''))))
        score = xml_escape(strip_emojis(str(sc.get('final_acceptance_score', ''))))
        risk_idx = sc.get('conflict_risk_index', 0)
        summary = xml_escape(strip_emojis(str(sc.get('summary', ''))))
        reason = xml_escape(strip_emojis(str(sc.get('reason', ''))))
        risk_reason = xml_escape(strip_emojis(str(sc.get('risk_reason', ''))))

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

    # 지도 QR 이미지 XML 구문 생성 (있는 경우)
    kakao_qr_xml = """
        <hp:run>
            <hp:pic id="1001" zOrder="0" numberingType="PICTURE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES">
                <hc:offset x="0" y="0"/>
                <hc:orgSz width="2800" height="2800"/>
                <hc:curSz width="2800" height="2800"/>
                <hc:img binaryItemIDRef="image1"/>
            </hp:pic>
        </hp:run>""" if has_qr else ""

    naver_qr_xml = """
        <hp:run>
            <hp:pic id="1002" zOrder="0" numberingType="PICTURE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES">
                <hc:offset x="0" y="0"/>
                <hc:orgSz width="2800" height="2800"/>
                <hc:curSz width="2800" height="2800"/>
                <hc:img binaryItemIDRef="image2"/>
            </hp:pic>
        </hp:run>""" if has_qr else ""

    # 표준 공문서 (이모지/색상 전면 제거)
    section0_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core">
    <!-- 두문 -->
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

    <!-- 본문 -->
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
    
    <hp:p id="8">
        <hp:run>
            <hp:t>  가. 후보지 기본 정보 및 모바일 지도 핀 QR코드</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="9">
        <hp:run>
            <hp:t>    1) 후보지 명칭 및 대상 시설: {candidate_jibun} ({facility_type})</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="10">
        <hp:run>
            <hp:t>    2) x, y 좌표 (위경도): 위도 {lat:.6f}, 경도 {lng:.6f}</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="11">
        <hp:run>
            <hp:t>    3) x, y 좌표 변환 주소: {candidate_address}</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="12">
        <hp:run>
            <hp:t>    4) 카카오지도 핀 연결 URL: {kakao_map_url}</hp:t>
        </hp:run>
        {kakao_qr_xml}
    </hp:p>
    <hp:p id="13">
        <hp:run>
            <hp:t>    5) 네이버지도 핀 연결 URL: {naver_map_url}</hp:t>
        </hp:run>
        {naver_qr_xml}
    </hp:p>
    <hp:p id="13_2">
        <hp:run>
            <hp:t>    6) 수요 강도 수준 및 심의 일시: {intensity_level} / {official_date} {official_time}</hp:t>
        </hp:run>
    </hp:p>

    <hp:p id="14">
        <hp:run>
            <hp:t>  나. 시나리오 심의 평가 및 종합 의견</hp:t>
        </hp:run>
    </hp:p>
    {scenario_xml}

    <hp:p id="15">
        <hp:run>
            <hp:t>  다. AHP 지표별 가중치 분석</hp:t>
        </hp:run>
    </hp:p>
    {ahp_rows_xml}

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

    <!-- 결문 -->
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
        if has_qr:
            z.writestr('BinData/image1.png', kakao_qr_png)
            z.writestr('BinData/image2.png', naver_qr_png)

    buffer.seek(0)
    return buffer.getvalue()
