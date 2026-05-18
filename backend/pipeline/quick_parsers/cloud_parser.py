from pathlib import Path
from .base import BaseParser, SYSTEM_PROMPT, PAGE_USER_MSG


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
        output_dir = kwargs.get("output_dir")
        parser = LlamaParse(
            api_key=api_key,
            result_type="markdown",
            **({"output_dir": str(output_dir)} if output_dir else {}),
        )
        docs = parser.load_data(str(pdf_path))
        parts = []
        for i, d in enumerate(docs, 1):
            parts.append(f"<!-- Page {i} -->\n{d.text}")
        return "\n\n---\n\n".join(parts)


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

        client      = OpenAI(api_key=api_key)
        doc         = fitz.open(str(pdf_path))
        total_pages = len(doc)
        results     = []

        for page in doc:
            pix  = page.get_pixmap(dpi=150)
            b64  = base64.b64encode(pix.tobytes("png")).decode()
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url",
                             "image_url": {"url": f"data:image/png;base64,{b64}"}},
                            {"type": "text",
                             "text": PAGE_USER_MSG.format(
                                 page=page.number + 1, total=total_pages)},
                        ],
                    },
                ],
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

        client      = ant.Anthropic(api_key=api_key)
        doc         = fitz.open(str(pdf_path))
        total_pages = len(doc)
        results     = []

        for page in doc:
            pix = page.get_pixmap(dpi=150)
            b64 = base64.b64encode(pix.tobytes("png")).decode()
            msg = client.messages.create(
                model=model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image",
                         "source": {"type": "base64",
                                    "media_type": "image/png",
                                    "data": b64}},
                        {"type": "text",
                         "text": PAGE_USER_MSG.format(
                             page=page.number + 1, total=total_pages)},
                    ],
                }],
            )
            results.append(f"<!-- Page {page.number + 1} -->\n"
                           + msg.content[0].text)
        doc.close()
        return "\n\n---\n\n".join(results)
