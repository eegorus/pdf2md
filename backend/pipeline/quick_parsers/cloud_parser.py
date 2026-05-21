import base64
from pathlib import Path

from .base import BaseParser, SYSTEM_PROMPT


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
        import fitz
        from llama_parse import LlamaParse

        parser = LlamaParse(api_key=api_key, result_type="markdown")
        docs   = parser.load_data(str(pdf_path))

        doc = fitz.open(str(pdf_path))
        parts = []
        for i, d in enumerate(docs, 1):
            page_text = d.text
            page_idx  = i - 1
            if page_idx < len(doc):
                page = doc[page_idx]
                for img_idx, img_info in enumerate(page.get_images(full=True), 1):
                    xref = img_info[0]
                    try:
                        base_image = doc.extract_image(xref)
                        w = base_image.get("width", 0)
                        h = base_image.get("height", 0)
                        if w < 50 or h < 50:
                            continue
                        b64  = base64.b64encode(base_image["image"]).decode()
                        ext  = base_image.get("ext", "png")
                        page_text += f"\n\n![Figure {img_idx}](data:image/{ext};base64,{b64})\n\n"
                    except Exception:
                        pass
            parts.append(f"<!-- Page {i} -->\n{page_text}")
        doc.close()
        return "\n\n---\n\n".join(parts)


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
