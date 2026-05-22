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
            page_h = page.rect.height

            # Collect text blocks with y0 for sorting
            items: list[tuple[float, str]] = []
            for b in page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]:
                if b["type"] != 0:
                    continue
                lines = []
                for line in b.get("lines", []):
                    line_text = "".join(span.get("text", "") for span in line.get("spans", []))
                    if line_text.strip():
                        lines.append(line_text)
                text = "\n".join(lines)
                if text.strip():
                    items.append((b["bbox"][1], text))

            # Collect image items with y0 from get_image_info
            seen_xrefs: set[int] = set()
            img_counter = 0
            for info in page.get_image_info(xrefs=True):
                xref = info.get("xref", 0)
                if xref <= 0 or xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                try:
                    raw = doc.extract_image(xref)
                    if raw.get("width", 0) < 50 or raw.get("height", 0) < 50:
                        continue
                    img_counter += 1
                    bbox = info.get("bbox", (0, 0, 0, 0))
                    b64 = base64.b64encode(raw["image"]).decode()
                    ext = raw.get("ext", "png")
                    items.append((bbox[1], f"![Figure {img_counter}](data:image/{ext};base64,{b64})"))
                except Exception as e:
                    _log.warning("[pymupdf] page %d: failed xref=%d: %s", pagenum + 1, xref, e)

            _log.info("[pymupdf] page %d: %d images", pagenum + 1, img_counter)

            items.sort(key=lambda x: x[0])
            parts = [content for _, content in items]
            if parts:
                pages_md.append(f"<!-- Page {pagenum + 1} -->\n" + "\n\n".join(parts))

        doc.close()
        return "\n\n---\n\n".join(pages_md)
