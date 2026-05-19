from pathlib import Path
import fitz  # PyMuPDF
from .base import BaseParser


class PyMuPDFParser(BaseParser):
    name        = "pymupdf"
    label       = "PyMuPDF (быстрый)"
    description = "Простое извлечение текста. Работает мгновенно, без GPU. Таблицы — базово."
    needs_api_key = False

    def is_available(self) -> bool:
        try:
            import fitz
            return True
        except ImportError:
            return False

    def _call_api(self, *args, **kwargs) -> str:
        raise NotImplementedError("PyMuPDF не использует VLM")

    def run(self, pdf_path: str | Path, **kwargs) -> str:
        doc = fitz.open(str(pdf_path))
        pages_md = []
        for page in doc:
            text = page.get_text("markdown")
            if text.strip():
                pages_md.append(f"<!-- Page {page.number + 1} -->\n{text}")
        doc.close()
        return "\n\n---\n\n".join(pages_md)
