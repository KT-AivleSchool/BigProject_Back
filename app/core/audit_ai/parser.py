import re
import fitz  # PyMuPDF


class PdfOcrParser:
    @staticmethod
    def extract_text_from_pdf(pdf_bytes: bytes) -> str:
        """
        PDF 바이너리 스트림으로부터 텍스트 레이어를 전부 추출합니다.
        - with 컨텍스트 매니저로 예외 발생 여부와 무관하게 파일 스트림 핸들을 자동 해제합니다.
          (기존 doc.close() 방식은 루프 도중 예외 발생 시 자원 누수 리스크 존재 — 리뷰 반영)
        """
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            text_content = [page.get_text() for page in doc]
        return "\n".join(text_content)

    @staticmethod
    def parse_document_metadata(
        text: str, facility_vocab: dict[str, list[str]] | None = None
    ) -> dict:
        """
        추출된 공문 텍스트 내에서 정규식을 이용해 지번, 날짜, 인프라 유형을 파싱합니다.

        🔴 2026-08-11. `facility_vocab` 은 **주입받는다.** 예전엔 이 함수 안에
           `{"흡연구역": [...], "쓰레기통": [...], "어린이집": [...],
           "스마트쉼터": [...]}` 사전이 박혀 있었다 — 도메인 값 하드코딩이라
           원칙 2 에 걸리고, 더 나쁜 건 **여기 없는 시설은 영영 `None`** 이라는
           점이다. 성동구 재활용정거장 공문은 이 넷에 없으니 `facility_type` 이
           안 나오는데, 그게 「못 찾았다」인지 「사전에 없다」인지 구분이 안 된다.

           지금은 호출자(`/audit/verify`)가 **그 시뮬레이션의 시설**로 어휘를
           만들어 넘긴다. 안 넘기면 `None` 이다 — 추측해서 채우지 않는다(원칙 1).
        """
        metadata = {
            "parsed_jibun": None,
            "parsed_date": None,
            "facility_type": None,
            "document_no": None,
        }

        # 1. 지번 주소 정규식 탐지 (예: 서울특별시 용산구 이태원동 123-45)
        #    🔴 2026-08-11. `서울` 로 시작하는 전체주소만 잡았다 — 그런데 우리가
        #    대조할 상대인 `booth_candidates.jibun` 은 **시를 뺀** `용산구 이태원동
        #    123-45` 형식이다. 공문도 보통 시를 안 쓴다. `서울…` 을 선택적으로 바꾼다.
        #    시가 있으면 그것까지 포함해 잡는다(있는 정보를 버리지 않는다).
        jibun_pattern = (
            r"((?:서울(?:특별시)?\s+)?[가-힣]{2,4}구\s+[가-힣\d\s-]+?(?:동|가|로)\s+\d+(?:-\d+)?)"
        )
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

        # 3. 문서 번호 탐지.
        #    🔴 2026-08-11. 예전엔 `([가-힣\d]+-[가-힣\d]+-\d+호)` **하나**였다 —
        #    「세 토막 + 끝에 호」라는 특정 모양만 잡는다. 실제 행정 공문의 문서번호는
        #    「처리과명-일련번호」(예: `도시계획과-12345`) 두 토막이고 `호` 가 없다.
        #    그래서 문서번호가 **버젓이 적혀 있는데 null** 이 나갔고, 그 null 이 그대로
        #    `/save` 로 넘어가 `verified_precedents.document_no` 가 빈다(원칙 4).
        #    아래 순서로 본다 — 위쪽일수록 근거가 확실하다. 못 찾으면 `None` 이다.
        doc_no_patterns = (
            # ⓐ 「문서번호:」 라벨이 붙어 있으면 그 값을 그대로 쓴다(가장 확실).
            r"문서\s*번호\s*[:：]?\s*([^\s\n]+)",
            # ⓑ 제2026-1234호 — 고시·공고문 관례.
            r"(제\s*\d{2,4}\s*-\s*\d+\s*호)",
            # ⓒ 기존 세 토막 형식(용산구-행정-12345호).
            r"([가-힣\d]+-[가-힣\d]+-\d+호)",
            # ⓓ 처리과명-일련번호 — 행정업무규정상의 기본형.
            #    숫자만 3자리 이상이라야 잡는다. 「이태원동 123-45」 같은 지번은
            #    앞이 한글+숫자 혼합이 아니라 공백이 끼므로 여기 안 걸린다.
            r"([가-힣]{2,10}과-\d{3,10})",
        )
        for pattern in doc_no_patterns:
            m = re.search(pattern, text)
            if m:
                metadata["document_no"] = re.sub(r"\s+", "", m.group(1)).strip()
                break

        # 4. 대상 인프라 탐지 — 어휘는 **호출자가 준다**(위 docstring).
        for facility, keywords in (facility_vocab or {}).items():
            if any(kw and kw in text for kw in keywords):
                metadata["facility_type"] = facility
                break

        return metadata


# 파서 서비스 인스턴스 배포
pdf_parser = PdfOcrParser()
