import os
from io import BytesIO
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML


class PdfReportBuilder:
    def __init__(self):
        # templates 폴더 절대경로 추적
        current_dir = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(current_dir, "../templates")
        self.env = Environment(loader=FileSystemLoader(template_path))

    def generate_feasibility_pdf(self, data: dict) -> BytesIO:
        """HTML에 데이터를 매핑한 뒤 PDF 바이트 스트림을 리턴합니다."""
        template = self.env.get_template("report_template.html")

        # OS 독립적 한글 폰트 절대 경로 주입 (Docker/서버 환경에서도 한글 깨짐 방지)
        font_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "../static/fonts/NanumGothic-Regular.ttf",
        )
        render_data = {**data, "font_path": font_path}
        rendered_html = template.render(**render_data)

        pdf_buffer = BytesIO()
        # HTML 텍스트를 파싱하여 PDF 바이너리로 버퍼에 출력
        HTML(string=rendered_html, base_url=".").write_pdf(pdf_buffer)
        pdf_buffer.seek(0)

        return pdf_buffer


# 서비스 싱글톤 인스턴스 배포
pdf_builder = PdfReportBuilder()
