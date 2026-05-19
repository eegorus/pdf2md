from __future__ import annotations

import base64
import io
from abc import ABC, abstractmethod
from pathlib import Path

import fitz
from PIL import Image as PILImage


# ── Промпты ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert document digitization engine. Convert a PDF page image to \
clean, complete GitHub-Flavored Markdown.

TABLES — critical:
- Reproduce every table fully. Never skip rows, columns, or cells.
- GFM pipe tables with separator row after the header.
  | Header 1 | Header 2 |
  |:---------|----------:|
  | value    | value     |
- Use :--- left, :---: center, ---: right based on content type.
- Use HTML <table> only for merged cells (colspan/rowspan).
- Empty cells: single space. Multi-line cells: collapse with "; ".
- No clear header row → treat first row as header.

FIGURES & IMAGES — critical:
You will receive the full page image PLUS each extracted figure
as a separate labeled image (FIGURE 1, FIGURE 2, ...).

For EACH figure in the page:
- Replace its <figure_placeholder_N> with the base64 tag I will inject.
- Write a **Figure N:** caption (1-3 sentences) immediately after:
  - include figure type (chart/diagram/photo/map/schematic/illustration)
  - axis labels and key values if visible
  - trend or key finding

Format exactly:
  <figure_placeholder_N>
  **Figure N:** <caption>

NEVER write empty ![image]() or bare [Figure] placeholders.
Unreadable figure → ![unreadable figure]()

HEADINGS: ## sections, ### subsections. Never #.

FORMULAS: Inline \\(...\\) Block \\[...\\] — LaTeX only, never Unicode math.
Subscripts/superscripts: LaTeX (x^3, H_2O), never ³ ₂.

LAYOUT:
- Multi-column → single-column, left-to-right, top-to-bottom.
- Page headers/footers: OMIT.
- Footnotes: include at bottom after ---.

OUTPUT: Return ONLY Markdown. No code fences, no explanations.
"""

PAGE_USER_MSG = (
    "Page {page} of {total}.\n\n"
    "Convert this page to Markdown per your instructions.\n\n"
    "Reminders:\n"
    "- Tables: ALL rows/columns in GFM.\n"
    "- Figures: replace each <figure_placeholder_N> with the matching image "
    "I've provided, then write **Figure N:** caption.\n"
    "- Formulas: LaTeX only.\n\n"
    "Return only the Markdown, nothing else."
)


# ── Утилиты ────────────────────────────────────────────────────────────────────

def _render_page_b64(page: fitz.Page, dpi: int = 150) -> str:
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    return base64.b64encode(pix.tobytes("png")).decode()


def _extract_figures(
    doc: fitz.Document,
    pagenum: int,
    min_w: int = 80,
    min_h: int = 80,
    max_side: int = 1200,
) -> list[dict]:
    """
    Извлекает встроенные растровые изображения со страницы.
    Возвращает список: [{index, b64, width, height, bbox_norm}]
    bbox_norm — (x0, y0, x1, y1) в долях страницы (0..1)
    """
    page = doc[pagenum]
    page_rect = page.rect
    results: list[dict] = []

    for idx, img_info in enumerate(page.get_images(full=True)):
        xref = img_info[0]  # get_images returns tuples; first element is xref
        try:
            raw = doc.extract_image(xref)
        except Exception:
            continue

        try:
            pil = PILImage.open(io.BytesIO(raw["image"])).convert("RGB")
        except Exception:
            continue

        w, h = pil.size
        if w < min_w or h < min_h:
            continue

        if max(w, h) > max_side:
            ratio = max_side / max(w, h)
            pil = pil.resize((int(w * ratio), int(h * ratio)), PILImage.LANCZOS)

        buf = io.BytesIO()
        pil.save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode()

        bbox_norm = (0.0, 0.0, 1.0, 1.0)
        try:
            for rect in page.get_image_rects(xref):
                bbox_norm = (
                    rect.x0 / page_rect.width,
                    rect.y0 / page_rect.height,
                    rect.x1 / page_rect.width,
                    rect.y1 / page_rect.height,
                )
                break
        except Exception:
            pass

        results.append({
            "index": idx,
            "b64": b64,
            "width": pil.width,
            "height": pil.height,
            "bbox_norm": bbox_norm,
        })

    return results


def _inject_figures(md: str, figure_map: dict[str, str]) -> str:
    """
    Заменяет плейсхолдеры <figure_placeholder_N> на base64-теги.
    Если модель не вставила плейсхолдер — добавляет тег в конец.
    """
    for placeholder, img_tag in figure_map.items():
        if placeholder in md:
            md = md.replace(placeholder, img_tag)
        else:
            md = md + f"\n\n{img_tag}\n"
    return md


# ── Базовый класс ──────────────────────────────────────────────────────────────

class BaseParser(ABC):
    name: str = ""
    label: str = ""
    description: str = ""
    needs_api_key: bool = False

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def _call_api(
        self,
        page_b64: str,
        figure_parts: list[dict],
        user_msg: str,
        api_key: str,
        **kwargs,
    ) -> str:
        """
        Один запрос к VLM для одной страницы.
        page_b64     — base64 PNG страницы
        figure_parts — [{label: str, b64: str}, ...] для каждой фигуры на странице
        user_msg     — текст инструкции для этой страницы
        api_key      — ключ API (пустая строка для локальных парсеров)
        """
        ...

    def run(self, pdf_path: Path, api_key: str = "", **kwargs) -> str:
        """
        Шаблонный метод: рендер страниц, извлечение фигур, вызов _call_api,
        инжект base64 фигур в Markdown.
        Локальные парсеры (PyMuPDF, Marker, Docling, ...) переопределяют run()
        и оставляют _call_api с raise NotImplementedError.
        """
        doc = fitz.open(str(pdf_path))
        total = len(doc)
        pages_md: list[str] = []

        for pagenum in range(total):
            page_b64 = _render_page_b64(doc[pagenum], dpi=150)
            figures  = _extract_figures(doc, pagenum)

            figure_parts: list[dict] = []
            figure_map: dict[str, str] = {}
            for i, fig in enumerate(figures, start=1):
                placeholder = f"<figure_placeholder_{i}>"
                img_tag = (
                    f"![figure {i}](data:image/png;base64,{fig['b64']})"
                )
                figure_map[placeholder] = img_tag
                figure_parts.append({
                    "label": (
                        f"FIGURE {i} — size {fig['width']}×{fig['height']}px, "
                        f"page position top={fig['bbox_norm'][1]:.2f} "
                        f"left={fig['bbox_norm'][0]:.2f}"
                    ),
                    "b64": fig["b64"],
                })

            user_msg = PAGE_USER_MSG.format(page=pagenum + 1, total=total)

            try:
                md_raw = self._call_api(
                    page_b64=page_b64,
                    figure_parts=figure_parts,
                    user_msg=user_msg,
                    api_key=api_key,
                    **kwargs,
                )
            except Exception as e:
                md_raw = f"<!-- ERROR page {pagenum + 1}: {e} -->"

            md_page = _inject_figures(md_raw, figure_map)
            pages_md.append(f"<!-- page {pagenum + 1} -->\n{md_page}")

        doc.close()
        return "\n\n---\n\n".join(pages_md)
