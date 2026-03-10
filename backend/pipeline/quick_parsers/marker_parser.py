from pathlib import Path
from .base import BaseParser


class MarkerParser(BaseParser):
    name        = "marker"
    label       = "Marker (локальный 🥇)"
    description = "Лучшее качество для текста, формул, структуры. Требует GPU ~4GB VRAM."
    needs_api_key = False

    def is_available(self) -> bool:
        try:
            from marker.converters.pdf import PdfConverter
            return True
        except ImportError:
            return False

    def run(self, pdf_path: str | Path, **kwargs) -> str:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.output import text_from_rendered

        converter = PdfConverter(artifact_dict=create_model_dict())
        rendered  = converter(str(pdf_path))
        text, _, _ = text_from_rendered(rendered)
        return text
