import base64
import logging
from pathlib import Path

import fitz  # PyMuPDF

from .base import BaseParser

_log = logging.getLogger("prms.quickparser")


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
        for pagenum in range(len(doc)):
            page = doc[pagenum]
            text = page.get_text("text")

            image_list = page.get_images(full=True)
            for img_idx, img_info in enumerate(image_list, 1):
                xref = img_info[0]
                try:
                    base_image = doc.extract_image(xref)
                    w = base_image.get("width", 0)
                    h = base_image.get("height", 0)
                    if w < 50 or h < 50:
                        continue
                    b64 = base64.b64encode(base_image["image"]).decode()
                    ext = base_image.get("ext", "png")
                    text += f"\n\n![Figure {img_idx}](data:image/{ext};base64,{b64})\n\n"
                except Exception as e:
                    _log.warning("[pymupdf] page %d: failed to extract xref=%d: %s", pagenum + 1, xref, e)

            _log.info("[pymupdf] page %d: %d images", pagenum + 1, len(image_list))
            if text.strip():
                pages_md.append(f"<!-- Page {pagenum + 1} -->\n{text}")

        doc.close()
        return "\n\n---\n\n".join(pages_md)
