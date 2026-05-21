import base64
import io
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

    def _call_api(self, *args, **kwargs) -> str:
        raise NotImplementedError("Marker не использует VLM")

    def run(self, pdf_path: str | Path, **kwargs) -> str:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.output import text_from_rendered

        converter = PdfConverter(artifact_dict=create_model_dict())
        rendered  = converter(str(pdf_path))
        text, _, images = text_from_rendered(rendered)

        if isinstance(images, dict):
            for img_name, img_obj in images.items():
                try:
                    buf = io.BytesIO()
                    img_obj.save(buf, format="PNG", optimize=True)
                    b64 = base64.b64encode(buf.getvalue()).decode()
                    text = text.replace(img_name, f"data:image/png;base64,{b64}")
                except Exception:
                    pass

        return text
