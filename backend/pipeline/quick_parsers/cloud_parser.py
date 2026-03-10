from pathlib import Path
from .base import BaseParser


class LlamaParseParser(BaseParser):
    name        = "llamaparse"
    label       = "LlamaParse (облако)"
    description = "Облачный парсер от LlamaIndex. Высокое качество, платный."
    needs_api_key = True

    def is_available(self) -> bool:
        try:
            import llama_parse
            return True
        except ImportError:
            return False

    def run(self, pdf_path: str | Path, api_key: str = "", **kwargs) -> str:
        from llama_parse import LlamaParse
        parser = LlamaParse(api_key=api_key, result_type="markdown")
        docs   = parser.load_data(str(pdf_path))
        return "\n\n---\n\n".join(d.text for d in docs)


class GPT4oParser(BaseParser):
    name        = "gpt4o"
    label       = "GPT-4o (облако)"
    description = "Постраничная обработка через GPT-4o Vision. Дорого, но очень высокое качество."
    needs_api_key = True

    def is_available(self) -> bool:
        try:
            import openai
            return True
        except ImportError:
            return False

    def run(self, pdf_path: str | Path, api_key: str = "", model: str = "gpt-4o", **kwargs) -> str:
        import fitz, base64
        from openai import OpenAI

        client  = OpenAI(api_key=api_key)
        doc     = fitz.open(str(pdf_path))
        results = []

        for page in doc:
            pix  = page.get_pixmap(dpi=150)
            b64  = base64.b64encode(pix.tobytes("png")).decode()
            resp = client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        {"type": "text",
                         "text": "Extract all content from this page as clean Markdown. "
                                 "Preserve tables as Markdown tables. Keep formulas in LaTeX."},
                    ],
                }],
                max_tokens=4096,
            )
            results.append(f"<!-- Page {page.number + 1} -->\n"
                           + resp.choices[0].message.content)
        doc.close()
        return "\n\n---\n\n".join(results)


class ClaudeParser(BaseParser):
    name        = "claude"
    label       = "Claude (облако)"
    description = "Постраничная обработка через Claude Vision. Хорош для сложных таблиц."
    needs_api_key = True

    def is_available(self) -> bool:
        try:
            import anthropic
            return True
        except ImportError:
            return False

    def run(self, pdf_path: str | Path, api_key: str = "",
            model: str = "claude-3-5-sonnet-20241022", **kwargs) -> str:
        import fitz, base64
        import anthropic as ant

        client  = ant.Anthropic(api_key=api_key)
        doc     = fitz.open(str(pdf_path))
        results = []

        for page in doc:
            pix = page.get_pixmap(dpi=150)
            b64 = base64.b64encode(pix.tobytes("png")).decode()
            msg = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image",
                         "source": {"type": "base64",
                                    "media_type": "image/png",
                                    "data": b64}},
                        {"type": "text",
                         "text": "Extract all content from this page as clean Markdown. "
                                 "Preserve tables as Markdown tables. Keep formulas in LaTeX."},
                    ],
                }],
            )
            results.append(f"<!-- Page {page.number + 1} -->\n"
                           + msg.content[0].text)
        doc.close()
        return "\n\n---\n\n".join(results)
