from pathlib import Path
from .base import BaseParser


class DoclingParser(BaseParser):
    name        = "docling"
    label       = "Docling (локальный 🥈)"
    description = "Отличное качество для таблиц и сложной структуры. IBM Research."
    needs_api_key = False

    def is_available(self) -> bool:
        try:
            import docling
            return True
        except ImportError:
            return False

    def run(self, pdf_path: str | Path, **kwargs) -> str:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result    = converter.convert(str(pdf_path))
        return result.document.export_to_markdown()
