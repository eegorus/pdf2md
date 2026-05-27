import base64
import re as _re
from pathlib import Path

from .base import BaseParser, SYSTEM_PROMPT


def _normalize_latex_delimiters(text: str) -> str:
    """
    Convert all LaTeX delimiter variants to KaTeX-compatible \(...\) and \[...\].

    LlamaParse parse_page_with_lvm outputs formulas as:
      [ \command ... ]          ← single or multiline, block
      [ \command ... \ldots(N)] ← with equation number
      $...$  or  $$...$$        ← dollar variants

    KaTeX understands ONLY:
      \( ... \)   inline
      \[ ... \]   block
    """
    # ── Step 1: protect base64 data URIs ─────────────────────────────────
    _protected: list[str] = []

    def _protect(m: _re.Match) -> str:
        idx = len(_protected)
        _protected.append(m.group(0))
        return f"\x00P{idx}\x00"

    text = _re.sub(
        r'data:image/[^;]+;base64,[A-Za-z0-9+/=\n\r]+',
        _protect, text
    )

    # ── Step 2: protect already-correct \[...\] and \(...\) ──────────────
    text = _re.sub(r'\\\[.*?\\\]', _protect, text, flags=_re.DOTALL)
    text = _re.sub(r'\\\(.*?\\\)', _protect, text, flags=_re.DOTALL)

    # ── Step 3: $$...$$ → \[...\]  (display, must precede $...$) ─────────
    text = _re.sub(
        r'\$\$(.+?)\$\$',
        lambda m: r'\[' + m.group(1) + r'\]',
        text, flags=_re.DOTALL
    )

    # ── Step 4: $...$ → \(...\)  (inline) ────────────────────────────────
    text = _re.sub(
        r'(?<!\$)\$([^\$]{1,400}?)\$(?!\$)',
        lambda m: r'\(' + m.group(1) + r'\)',
        text, flags=_re.DOTALL
    )

    # ── Step 5: [ ... ] → \[...\]  (LlamaParse lvm style) ──────────────
    # Lookahead (?=[\s\S]*\\[a-zA-Z]) ensures the bracket contains at least
    # one LaTeX command, distinguishing math from markdown links/footnotes.
    # Handles both single-line and multiline block formulas.
    text = _re.sub(
        r'\[\s*((?=[\s\S]*\\[a-zA-Z])[\s\S]{1,2000}?)\s*\]',
        lambda m: r'\[' + m.group(1).strip() + r'\]',
        text, flags=_re.DOTALL
    )

    # ── Step 6: restore protected blocks ─────────────────────────────────
    for idx, original in enumerate(_protected):
        text = text.replace(f"\x00P{idx}\x00", original)

    return text


def _openai_content(page_b64: str, figure_parts: list[dict], user_msg: str) -> list:
    content = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{page_b64}"}},
    ]
    for fp in figure_parts:
        content.append({"type": "text", "text": fp["label"] + ":"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{fp['b64']}"}})
    content.append({"type": "text", "text": user_msg})
    return content


def _anthropic_content(page_b64: str, figure_parts: list[dict], user_msg: str) -> list:
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": page_b64}},
    ]
    for fp in figure_parts:
        content.append({"type": "text", "text": fp["label"] + ":"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": fp["b64"]}})
    content.append({"type": "text", "text": user_msg})
    return content


class LlamaParseParser(BaseParser):
    name          = "llamaparse"
    label         = "LlamaParse (облако)"
    description   = "Облачный парсер от LlamaIndex. Высокое качество, платный."
    needs_api_key = True

    def is_available(self) -> bool:
        try:
            import llama_parse
            return True
        except ImportError:
            return False

    def _call_api(self, *args, **kwargs) -> str:
        raise NotImplementedError("LlamaParse не использует постраничный VLM")

    def run(self, pdf_path: str | Path, api_key: str = "", **kwargs) -> str:
        import re
        import fitz
        from llama_parse import LlamaParse

        parser = LlamaParse(
            api_key=api_key,
            result_type="markdown",
            parse_mode="parse_page_with_lvm",
        )
        docs   = parser.load_data(str(pdf_path))

        doc = fitz.open(str(pdf_path))
        result_pages = []

        for i, d in enumerate(docs, 1):
            page_num  = i - 1
            page_text = d.text

            if page_num >= len(doc):
                result_pages.append(f"<!-- Page {i} -->\n{page_text}")
                continue

            page   = doc[page_num]
            page_h = page.rect.height

            image_items: list[tuple[float, str]] = []
            seen_xrefs: set[int] = set()
            for info in page.get_image_info(xrefs=True):
                xref = info.get("xref", 0)
                if xref <= 0 or xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                try:
                    raw = doc.extract_image(xref)
                    if raw.get("width", 0) < 50 or raw.get("height", 0) < 50:
                        continue
                    bbox = info.get("bbox", (0, 0, 0, page_h))
                    y_center = (bbox[1] + bbox[3]) / 2
                    rel_pos  = y_center / page_h
                    b64 = base64.b64encode(raw["image"]).decode()
                    ext = raw.get("ext", "png")
                    image_items.append((rel_pos, f"![Figure](data:image/{ext};base64,{b64})"))
                except Exception:
                    pass

            if not image_items:
                result_pages.append(f"<!-- Page {i} -->\n{page_text}")
                continue

            image_items.sort(key=lambda x: x[0])
            paragraphs = re.split(r"\n\n+", page_text.strip())
            n = len(paragraphs)

            insertions: dict[int, list[str]] = {}
            for rel_pos, img_tag in image_items:
                idx = min(int(rel_pos * n), n - 1)
                insertions.setdefault(idx, []).append(img_tag)

            parts: list[str] = []
            for j, para in enumerate(paragraphs):
                parts.append(para)
                if j in insertions:
                    parts.extend(insertions[j])

            result_pages.append(f"<!-- Page {i} -->\n" + "\n\n".join(parts))

        doc.close()
        result = "\n\n---\n\n".join(result_pages)
        result = _normalize_latex_delimiters(result)
        return result


class GPT4oParser(BaseParser):
    name          = "gpt4o"
    label         = "GPT-4o (облако)"
    description   = "Постраничная обработка через GPT-4o Vision. Дорого, но очень высокое качество."
    needs_api_key = True

    def is_available(self) -> bool:
        try:
            import httpx
            return True
        except ImportError:
            return False

    def _call_api(self, page_b64, figure_parts, user_msg, api_key, **kwargs) -> str:
        import httpx
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "gpt-4o",
                "max_tokens": 4096,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _openai_content(page_b64, figure_parts, user_msg)},
                ],
            },
            timeout=90,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class ClaudeParser(BaseParser):
    name          = "claude"
    label         = "Claude (облако)"
    description   = "Постраничная обработка через Claude Vision. Хорош для сложных таблиц."
    needs_api_key = True

    def is_available(self) -> bool:
        try:
            import httpx
            return True
        except ImportError:
            return False

    def _call_api(self, page_b64, figure_parts, user_msg, api_key, **kwargs) -> str:
        import httpx
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 4096,
                "system": SYSTEM_PROMPT,
                "messages": [
                    {"role": "user", "content": _anthropic_content(page_b64, figure_parts, user_msg)},
                ],
            },
            timeout=90,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]
