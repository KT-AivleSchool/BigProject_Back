import re
import fitz  # PyMuPDF


class PdfOcrParser:
    @staticmethod
    def extract_text_from_pdf(pdf_bytes: bytes) -> str:
        """
        PDF 바이너리 스트림으로부터 텍스트 레이어를 전부 추출합니다.
        """
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text_content = []
        for page in doc:
            text_content.append(page.get_text())
        doc.close()
        return "\n".join(text_content)

    @staticmethod
    def parse_document_metadata(text: str) -> dict:
        """
        추출된 공문 텍스트 내에서 정규식을 이용해 지번, 날짜, 인프라 유형을 파싱합니다.
        """
        metadata = {
            "parsed_jibun": None,
            "parsed_date": None,
            "facility_type": None,
            "document_no": None,
        }

        # 1. 서울시 내 지번 주소 정규식 탐지 (예: 서울특별시 용산구 이태원동 123-45)
        jibun_pattern = r"(서울(?:특별시)?\s+[가-힣]{2,4}구\s+[가-힣\d\s-]+(?:동|가|로)\s+\d+(?:-\d+)?)"
        jibun_match = re.search(jibun_pattern, text)
        if jibun_match:
            metadata["parsed_jibun"] = jibun_match.group(1).strip()

        # 2. 준공/접수 일자 탐지 (예: 2026년 07월 09일 또는 2026.07.09)
        date_pattern = (
            r"(\d{4}년\s*\d{1,2}월\s*\d{1,2}일|\d{4}\.\s*\d{1,2}\.\s*\d{1,2})"
        )
        date_match = re.search(date_pattern, text)
        if date_match:
            metadata["parsed_date"] = (
                date_match.group(1).replace(".", "-").replace(" ", "").strip()
            )

        # 3. 문서 번호 탐지 (예: 용산구-행정-12345호)
        doc_no_pattern = r"([가-힣\d]+-[가-힣\d]+-\d+호)"
        doc_no_match = re.search(doc_no_pattern, text)
        if doc_no_match:
            metadata["document_no"] = doc_no_match.group(1).strip()

        # 4. 대상 인프라 키워드 사전 기반 탐지
        facility_keywords = {
            "흡연구역": ["흡연구역", "흡연부스", "제한구역", "금연"],
            "쓰레기통": ["쓰레기통", "가로휴지통", "수거함", "무단투기"],
            "어린이집": ["어린이집", "보호구역", "스쿨존"],
            "스마트쉼터": ["스마트쉼터", "쉼터", "버스정류장"],
        }

        for facility, keywords in facility_keywords.items():
            if any(kw in text for kw in keywords):
                metadata["facility_type"] = facility
                break

        return metadata


# 파서 서비스 인스턴스 배포
pdf_parser = PdfOcrParser()
