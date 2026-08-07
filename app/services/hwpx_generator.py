import io
import zipfile
from typing import Dict, Any

def build_hwpx_report(data: Dict[str, Any]) -> bytes:
    """
    파이프라인 심의 데이터를 바탕으로 HWPX (한글 XML 표준) 바이너리를 생성합니다.
    """
    candidate_jibun = data.get("candidate_jibun", "후보지 미지정")
    facility_type = data.get("facility_type", "공공시설")
    lat = data.get("candidate_lat", 0.0)
    lng = data.get("candidate_lng", 0.0)
    intensity_level = data.get("intensity_level", "보통")
    timestamp = data.get("timestamp", "")
    ahp_weights = data.get("ahp_weights", {})
    scenarios = data.get("scenarios", [])

    # HWPX 필수 mimetype (압축 방식: STORED)
    mimetype_content = b"application/hwp+zip"

    # META-INF/manifest.xml
    manifest_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">
    <manifest:file-entry manifest:full-path="/" manifest:media-type="application/hwp+zip"/>
    <manifest:file-entry manifest:full-path="Contents/header.xml" manifest:media-type="text/xml"/>
    <manifest:file-entry manifest:full-path="Contents/section0.xml" manifest:media-type="text/xml"/>
    <manifest:file-entry manifest:full-path="Contents/content.hpf" manifest:media-type="text/xml"/>
</manifest:manifest>
"""

    # Contents/content.hpf
    content_hpf = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<package xmlns="http://www.hancom.co.kr/hwpml/2011/content" version="1.0">
    <metadata>
        <title>입지 심의 및 평가 보고서</title>
        <creator>BigProject Engine</creator>
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

    # Contents/header.xml
    header_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">
    <hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" equation="1"/>
</hh:head>
"""

    # Contents/section0.xml 조립
    # AHP 지표 텍스트 생성
    ahp_rows_xml = ""
    for idx, (k, v) in enumerate(ahp_weights.items()):
        percentage = f"{v * 100:.1f}% ({v:.2f})"
        ahp_rows_xml += f"""
        <hp:p id="{idx + 10}">
            <hp:run>
                <hp:t>  • {k} : {percentage}</hp:t>
            </hp:run>
        </hp:p>"""

    # 시나리오 텍스트 생성
    scenario_xml = ""
    for idx, sc in enumerate(scenarios):
        scenario_xml += f"""
        <hp:p id="{idx + 100}">
            <hp:run>
                <hp:t>■ 시나리오 {sc.get('scenario', '')}: {sc.get('scenario_description', '')}</hp:t>
            </hp:run>
        </hp:p>
        <hp:p id="{idx + 200}">
            <hp:run>
                <hp:t>  - 최종 수용도: {sc.get('final_acceptance_score', '')} | 갈등위험지수: {sc.get('conflict_risk_index', 0)}점</hp:t>
            </hp:run>
        </hp:p>
        <hp:p id="{idx + 300}">
            <hp:run>
                <hp:t>  - 심의 요약: {sc.get('summary', '')}</hp:t>
            </hp:run>
        </hp:p>
        <hp:p id="{idx + 400}">
            <hp:run>
                <hp:t>  - 수용 사유: {sc.get('reason', '')}</hp:t>
            </hp:run>
        </hp:p>
        <hp:p id="{idx + 500}">
            <hp:run>
                <hp:t>  - 갈등 위험 요소: {sc.get('risk_reason', '')}</hp:t>
            </hp:run>
        </hp:p>
        """

    section0_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
    <hp:p id="1">
        <hp:run>
            <hp:t>[입지 심의 제출용 한글 보고서]</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="2">
        <hp:run>
            <hp:t>==================================================</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="3">
        <hp:run>
            <hp:t>제목: {facility_type} 설치 입지 심의 및 평가 보고서</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="4">
        <hp:run>
            <hp:t>발행일시: {timestamp}</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="5">
        <hp:run>
            <hp:t>==================================================</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="6">
        <hp:run>
            <hp:t>1. 후보지 기본 정보</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="7">
        <hp:run>
            <hp:t> - 후보지 명칭: {candidate_jibun}</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="8">
        <hp:run>
            <hp:t> - 시설 유형: {facility_type}</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="9">
        <hp:run>
            <hp:t> - 위경도 좌표: {lat}, {lng}</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="10">
        <hp:run>
            <hp:t> - 수요 강도: {intensity_level}</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="11">
        <hp:run>
            <hp:t>--------------------------------------------------</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="12">
        <hp:run>
            <hp:t>2. 시나리오 심의 평가 및 종합 의견</hp:t>
        </hp:run>
    </hp:p>
    {scenario_xml}
    <hp:p id="13">
        <hp:run>
            <hp:t>--------------------------------------------------</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="14">
        <hp:run>
            <hp:t>3. AHP 지표별 가중치 분석</hp:t>
        </hp:run>
    </hp:p>
    {ahp_rows_xml}
    <hp:p id="15">
        <hp:run>
            <hp:t>==================================================</hp:t>
        </hp:run>
    </hp:p>
    <hp:p id="16">
        <hp:run>
            <hp:t>심의 제출 확인인:                         (인)</hp:t>
        </hp:run>
    </hp:p>
</hs:sec>
"""

    # Zip 바이너리 패키징
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        # mimetype은 uncompressed STORED 이어야 한글에서 올바르게 인식함
        z.writestr('mimetype', mimetype_content, compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/manifest.xml', manifest_xml)
        z.writestr('Contents/content.hpf', content_hpf)
        z.writestr('Contents/header.xml', header_xml)
        z.writestr('Contents/section0.xml', section0_xml)

    buffer.seek(0)
    return buffer.getvalue()
