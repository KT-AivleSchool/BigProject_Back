import io
import fitz  # PyMuPDF
import docx  # python-docx
import olefile  # HWP5

from langchain_text_splitters import RecursiveCharacterTextSplitter
from typing import List


class StatuteDocumentLoader:
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 150):
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", " ", ""],
        )

    def extract_text_from_pdf(self, file_bytes: bytes) -> str:
        """sort=True: 텍스트 블록을 좌표(위→아래, 왼→오른쪽) 기준으로 정렬해 읽기 순서를 복원한다.
        기본값은 PDF 내부 저장 순서라, 조판에 따라 조문 첫 줄이 앞 조문 꼬리에 붙는 현상이 발생한다
        (실제 사례: 용산구 조례 PDF에서 '제1조(목적)' 다음 줄이 문장 중간부터 시작 → 파싱 전량 오정렬).
        """
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text_content = []
        for page in doc:
            text_content.append(page.get_text("text", sort=True))
        doc.close()
        return "\n".join(text_content)

    def extract_text_from_docx(self, file_bytes: bytes) -> str:
        doc = docx.Document(io.BytesIO(file_bytes))
        return "\n".join([para.text for para in doc.paragraphs])

    def extract_text_from_hwp(self, file_bytes: bytes) -> str:
        """
        HWP5 파일의 구조를 olefile로 해독하여 텍스트를 추출합니다.
        가장 안정적인 방식인 PrvText(미리보기 텍스트) 스트림을 우선 추출합니다.
        """
        try:
            with olefile.OleFileIO(file_bytes) as f:
                if f.exists("PrvText"):
                    stream = f.openstream("PrvText")
                    data = stream.read()
                    # PrvText는 utf-16-le로 인코딩되어 있음
                    return data.decode("utf-16le", errors="ignore")
                else:
                    raise ValueError(
                        "해당 HWP 파일에는 텍스트 정보(PrvText)가 포함되어 있지 않아 단순 추출이 불가능합니다."
                    )
        except olefile.OleError as e:
            raise ValueError(f"올바른 HWP 파일 형식이 아닙니다: {str(e)}")

    def process_document(self, file_bytes: bytes, extension: str) -> List[str]:
        """확장자에 따라 적절한 텍스트 추출기를 호출하고 청킹하여 반환합니다."""
        ext = extension.lower()
        if ext == ".pdf":
            text = self.extract_text_from_pdf(file_bytes)
        elif ext in [".docx", ".doc"]:
            text = self.extract_text_from_docx(file_bytes)
        elif ext == ".hwp":
            text = self.extract_text_from_hwp(file_bytes)
        else:
            raise ValueError(f"지원하지 않는 파일 확장자입니다: {extension}")

        if not text.strip():
            return []
        return self.text_splitter.split_text(text)


# 로더 서비스 인스턴스 배포
statute_document_loader = StatuteDocumentLoader()
