from __future__ import annotations

import base64
import io
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path

import fitz
from PIL import Image as PILImage

_log = logging.getLogger("prms.quickparser")


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
    "This page has {figure_count} embedded figure(s) "
    "provided as separate images after the page screenshot.\n\n"
    "Convert this page to Markdown per your instructions.\n\n"
    "Reminders:\n"
    "- Tables: ALL rows/columns in GFM.\n"
    "- Figures: replace each <figure_placeholder_N> with the matching "
    "image I've provided, then write **Figure N:** caption.\n"
    "- If figure_count is 0, skip figure instructions.\n"
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
    vector_dpi: int = 200,
) -> list[dict]:
    """
    Extracts figures from a page — raster xref images first, then vector drawings.
    Returns list of: [{index, b64, width, height, bbox_norm, source}]
    bbox_norm — (x0, y0, x1, y1) as fractions of page size (0..1)
    """
    page = doc[pagenum]
    page_rect = page.rect
    results: list[dict] = []

    # --- Raster xref images ---
    seen_xrefs: set[int] = set()
    for info in page.get_image_info(hashes=False, xrefs=True):
        xref = info.get("xref", 0)
        if xref <= 0 or xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        try:
            raw = doc.extract_image(xref)
            pil = PILImage.open(io.BytesIO(raw["image"])).convert("RGB")
        except Exception:
            continue

        w, h = pil.size
        if w < min_w or h < min_h:
            continue
        if max(w, h) > max_side:
            ratio = max_side / max(w, h)
            pil = pil.resize((int(w * ratio), int(h * ratio)), PILImage.LANCZOS)

        bbox = info.get("bbox", (0, 0, page_rect.width, page_rect.height))
        bbox_norm = (
            bbox[0] / page_rect.width,
            bbox[1] / page_rect.height,
            bbox[2] / page_rect.width,
            bbox[3] / page_rect.height,
        )
        buf = io.BytesIO()
        pil.save(buf, format="PNG", optimize=True)
        results.append({
            "index": len(results),
            "b64": base64.b64encode(buf.getvalue()).decode(),
            "width": pil.width,
            "height": pil.height,
            "bbox_norm": bbox_norm,
            "source": "xref",
        })

    if results:
        return results

    # --- Vector drawings fallback: cluster paths and render each cluster ---
    drawings = page.get_drawings()
    if not drawings:
        return []

    page_area = page_rect.width * page_rect.height
    rects: list[fitz.Rect] = []
    for d in drawings:
        r = d.get("rect")
        if not r:
            continue
        r = fitz.Rect(r)
        if r.width < min_w or r.height < min_h:
            continue
        # Skip near-full-page bounding boxes (borders, backgrounds)
        if (r.width * r.height) / page_area > 0.80:
            continue
        rects.append(r)

    if not rects:
        return []

    # Merge overlapping/nearby rects into clusters
    MERGE_GAP = 20
    clusters: list[fitz.Rect] = []
    for r in sorted(rects, key=lambda x: (x.y0, x.x0)):
        merged = False
        for i, c in enumerate(clusters):
            expanded = fitz.Rect(
                c.x0 - MERGE_GAP, c.y0 - MERGE_GAP,
                c.x1 + MERGE_GAP, c.y1 + MERGE_GAP,
            )
            if expanded.intersects(r):
                clusters[i] = c | r
                merged = True
                break
        if not merged:
            clusters.append(fitz.Rect(r))

    mat = fitz.Matrix(vector_dpi / 72, vector_dpi / 72)
    pad = 10
    for cluster_rect in clusters:
        if cluster_rect.width < min_w or cluster_rect.height < min_h:
            continue

        crop = fitz.Rect(
            max(0, cluster_rect.x0 - pad),
            max(0, cluster_rect.y0 - pad),
            min(page_rect.width,  cluster_rect.x1 + pad),
            min(page_rect.height, cluster_rect.y1 + pad),
        )
        pix = page.get_pixmap(matrix=mat, clip=crop)
        try:
            pil = PILImage.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        except Exception:
            continue

        w, h = pil.size
        if max(w, h) > max_side:
            ratio = max_side / max(w, h)
            pil = pil.resize((int(w * ratio), int(h * ratio)), PILImage.LANCZOS)

        bbox_norm = (
            crop.x0 / page_rect.width,
            crop.y0 / page_rect.height,
            crop.x1 / page_rect.width,
            crop.y1 / page_rect.height,
        )
        buf = io.BytesIO()
        pil.save(buf, format="PNG", optimize=True)
        results.append({
            "index": len(results),
            "b64": base64.b64encode(buf.getvalue()).decode(),
            "width": pil.width,
            "height": pil.height,
            "bbox_norm": bbox_norm,
            "source": "vector",
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


def _positional_inject(md: str, figures: list[dict]) -> str:
    """
    Insert figure images at geometrically correct paragraph positions.
    Uses bbox_norm[1..3] (y-fraction) from _extract_figures to map each figure
    to the closest paragraph break in the markdown text.
    Falls back to appending at end if markdown has no paragraph breaks.
    """
    if not figures:
        return md

    sorted_figs = sorted(
        figures,
        key=lambda f: (f["bbox_norm"][1] + f["bbox_norm"][3]) / 2,
    )

    paragraphs = re.split(r"\n\n+", md.strip())
    n = len(paragraphs)
    if n == 0:
        return md

    insertions: dict[int, list[str]] = {}
    for fig in sorted_figs:
        y_center = (fig["bbox_norm"][1] + fig["bbox_norm"][3]) / 2
        idx = min(int(y_center * n), n - 1)
        # _extract_figures always saves as PNG
        img_tag = f"![figure](data:image/png;base64,{fig['b64']})"
        insertions.setdefault(idx, []).append(img_tag)

    parts: list[str] = []
    for i, para in enumerate(paragraphs):
        parts.append(para)
        if i in insertions:
            parts.extend(insertions[i])

    return "\n\n".join(parts)


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
            _log.info(
                "[%s] page %d/%d: %d figures%s",
                self.name, pagenum + 1, total, len(figures),
                (" (" + ", ".join(
                    f"{f['width']}x{f['height']}px [{f.get('source','?')}]"
                    for f in figures
                ) + ")") if figures else "",
            )

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

            figure_count = len(figures)
            user_msg = PAGE_USER_MSG.format(
                page=pagenum + 1,
                total=total,
                figure_count=figure_count,
            )

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

            md_page = _positional_inject(md_raw, figures)
            pages_md.append(f"<!-- page {pagenum + 1} -->\n{md_page}")

        doc.close()
        return "\n\n---\n\n".join(pages_md)
