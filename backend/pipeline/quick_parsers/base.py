from abc import ABC, abstractmethod
from pathlib import Path

SYSTEM_PROMPT = """You are an expert document digitization engine. Convert a PDF page image to clean, complete GitHub-Flavored Markdown.

TABLES — this is critical:
- Every table MUST be fully reproduced. Never skip rows, columns, or cells.
- Use standard GFM pipe tables:
  | Header 1 | Header 2 | Header 3 |
  |----------|:--------:|----------:|
  | value    | value    | value     |
- Always include the separator row (---|---|---) after the header row.
- Use `:---` for left-align, `:---:` for center, `---:` for right-align based on content type
  (numbers → right-align, text → left-align, headers → center).
- If a cell spans multiple columns (colspan) or rows (rowspan), use an HTML <table> instead of GFM.
- Multi-line cell content: collapse to single line with "; " separator.
- Empty cells: use a single space, never leave them blank.
- If a table has no clear header row, treat the first row as header.

FIGURES & IMAGES — this is critical:
- Never output an empty placeholder like ![image]() or [Figure].
- For each figure, chart, diagram, photograph, or illustration, output:
  ![<type>]()
  where <type> is: chart | diagram | photo | map | screenshot | illustration
- Example: ![chart]()
- For complex figures, add a one-paragraph caption below the image tag starting with **Figure:**
- Describe axis labels, legend items, and key values if visible.

HEADINGS:
- Use ## for section headings, ### for subsections. Never use # (reserved for document title).
- Preserve the visual hierarchy from the page.

FORMULAS:
- Inline: \\(...\\)  Block: \\[...\\]  — strict LaTeX notation, never Unicode math symbols.
- Subscripts/superscripts: LaTeX only (10^3, H_2O, ft^3), never ³ ² ₂ Unicode.

LAYOUT:
- Multi-column pages: merge into single-column reading order (left-to-right, top-to-bottom).
- Page headers/footers: OMIT (skip repeated text at top/bottom edges).
- Footnotes: include at the bottom, separated by a horizontal rule (---).

OUTPUT:
- Return ONLY the Markdown content for this page.
- No explanations, no code fences, no "Here is the markdown:" preamble.
- Preserve all data — do not summarize or truncate any content.
"""

PAGE_USER_MSG = (
    "Page {page} of {total}.\n\n"
    "Convert this page image to clean Markdown following your system instructions exactly.\n\n"
    "Key reminders for this page:\n"
    "- If you see a table: reproduce ALL rows and columns completely in GFM format.\n"
    "- If you see a figure, chart, or photo: describe it fully — never output an empty placeholder.\n"
    "- If you see a formula: use LaTeX notation only.\n\n"
    "Return only the Markdown content, nothing else."
)


class BaseParser(ABC):
    name: str
    label: str
    description: str
    needs_api_key: bool

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def run(self, pdf_path: str | Path, **kwargs) -> str: ...
