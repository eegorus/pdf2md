"""
OpenRouter — облачный парсер PDF → Markdown через vision модели.
Использует API-ключ из /app/data/settings.json
"""
import base64
import json
from pathlib import Path
from .base import BaseParser


class OpenRouterParser(BaseParser):
    name          = "openrouter"
    label         = "OpenRouter (облако)"
    description   = "Любая vision-модель через OpenRouter API (GPT-4o, Claude, Gemini...)"
    needs_api_key = True
    default_model = "openai/gpt-4o"

    @staticmethod
    def is_available() -> bool:
        try:
            import httpx
            return True
        except ImportError:
            return False

    def run(self, pdf_path: Path, api_key: str = "", model: str = "") -> str:
        import httpx
        import fitz  # PyMuPDF

        if not api_key:
            raise ValueError("OpenRouter API key не задан. Добавь в Settings.")

        model = model or self.default_model
        doc   = fitz.open(str(pdf_path))
        pages_md = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            # Рендерим страницу в PNG (150 DPI — баланс качество/размер)
            mat  = fitz.Matrix(150 / 72, 150 / 72)
            pix  = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            b64 = base64.b64encode(img_bytes).decode()

            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"This is page {page_num + 1} of a PDF document. "
                                    "Convert the entire page content to clean Markdown. "
                                    "Preserve tables as Markdown tables, formulas as LaTeX ($...$), "
                                    "headings as #/##/###, lists as - items. "
                                    "Return only the Markdown, no explanations."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"},
                            },
                        ],
                    }
                ],
                "max_tokens": 4096,
            }

            r = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization":  f"Bearer {api_key}",
                    "Content-Type":   "application/json",
                    "HTTP-Referer":   "https://prms-local",
                    "X-Title":        "PRMS Table Extractor",
                },
                json=payload,
                timeout=60,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            pages_md.append(f"<!-- page {page_num + 1} -->\n{content}")

        doc.close()
        return "\n\n---\n\n".join(pages_md)
