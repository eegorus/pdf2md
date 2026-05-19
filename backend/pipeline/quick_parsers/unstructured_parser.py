"""
Unstructured — локальный парсер PDF → Markdown.
pip install "unstructured[pdf]"
"""
from pathlib import Path
from .base import BaseParser


class UnstructuredParser(BaseParser):
    name  = "unstructured"
    label = "Unstructured (локальный)"
    description = "Open-source парсер. Хорош для текста и простых таблиц. Без GPU."
    needs_api_key = False

    @staticmethod
    def is_available() -> bool:
        try:
            from unstructured.partition.pdf import partition_pdf
            return True
        except ImportError:
            return False

    def _call_api(self, *args, **kwargs) -> str:
        raise NotImplementedError("Unstructured не использует VLM")

    def run(self, pdf_path: Path, api_key: str = "") -> str:
        from unstructured.partition.pdf import partition_pdf

        elements = partition_pdf(
            filename=str(pdf_path),
            strategy="fast",          # fast / hi_res / ocr_only
            include_page_breaks=True,
        )

        lines = []
        for el in elements:
            category = getattr(el, "category", "")
            text     = str(el).strip()
            if not text:
                continue

            if category == "Title":
                lines.append(f"## {text}\n")
            elif category == "PageBreak":
                lines.append("\n---\n")
            elif category in ("Table",):
                # Unstructured отдаёт таблицы как HTML через metadata
                html = getattr(el.metadata, "text_as_html", None)
                if html:
                    lines.append(f"\n{html}\n")
                else:
                    lines.append(f"\n{text}\n")
            elif category in ("ListItem",):
                lines.append(f"- {text}")
            else:
                lines.append(text)

        return "\n".join(lines)
