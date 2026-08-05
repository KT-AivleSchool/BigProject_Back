import os
from io import BytesIO
from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright

class PdfReportBuilder:
    def __init__(self):
        # templates 폴더 절대경로 추적
        current_dir = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(current_dir, "../templates")
        self.env = Environment(loader=FileSystemLoader(template_path))

    async def generate_feasibility_pdf(self, data: dict) -> BytesIO:
        """HTML에 데이터를 매핑한 뒤 Playwright를 이용해 PDF 바이트 스트림을 리턴합니다."""
        template = self.env.get_template("report_template.html")
        rendered_html = template.render(**data)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # HTML 주입 후 로드 완료 대기
            await page.set_content(rendered_html, wait_until="networkidle")
            
            # PDF 변환 (A4 사이즈 및 여백 설정)
            pdf_bytes = await page.pdf(
                format="A4", 
                margin={"top": "20mm", "bottom": "20mm", "left": "20mm", "right": "20mm"},
                print_background=True
            )
            await browser.close()

        pdf_buffer = BytesIO(pdf_bytes)
        pdf_buffer.seek(0)
        return pdf_buffer

# 서비스 싱글톤 인스턴스 배포
pdf_builder = PdfReportBuilder()
