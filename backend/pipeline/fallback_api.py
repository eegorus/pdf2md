"""
fallback_api.py — Ollama как универсальный fallback для любого блока

Используется когда:
- dots.ocr не загружен → таблица → Ollama
- TexTeller не работает → формула → Ollama
- EasyOCR дал пустой результат → текст → Ollama
- Любой другой сбой специализированного модуля

Промпты адаптированы под каждый тип блока.
"""
import base64
import io
import logging
import os

import httpx
from PIL import Image

logger = logging.getLogger("prms.fallback_api")

# Промпты для каждого типа блока
PROMPTS = {
    "text": (
        "Extract all text from this image exactly as written. "
        "Preserve line breaks and formatting. "
        "Return only the text content, no explanations."
    ),
    "table": (
        "Convert this table image to HTML format. "
        "Use <table>, <tr>, <th>, <td> tags. "
        "Preserve all cell content, merged cells (use colspan/rowspan attributes), "
        "and multi-level headers exactly as shown. "
        "Start your response with <table and end with </table>. "
        "Return only the HTML table, no explanations, no markdown code blocks."
    ),
    "formula": (
        "Convert this mathematical formula to LaTeX. "
        "Return only the LaTeX expression without $ delimiters. "
        "Example: \\frac{a}{b} + c^2"
    ),
    "figure": (
        "Describe this figure/chart from a technical document. "
        "Be concise (2-3 sentences), focus on what data it shows."
    ),
}


class FallbackAPI:
    def __init__(self):
        self.ollama_url = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
        self.model      = os.getenv("OLLAMA_FALLBACK_MODEL", "qwen2.5vl:7b")
        self.timeout    = int(os.getenv("OLLAMA_TIMEOUT", "120"))
        logger.info(f"FallbackAPI: {self.model} @ {self.ollama_url}")

    def _to_base64(self, image: Image.Image) -> str:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def process(self, image: Image.Image, block_type: str) -> str:
        """
        Обрабатывает блок через Ollama.

        block_type: text / table / formula / figure
        Возвращает строку с результатом.
        """
        image   = image.convert("RGB")
        prompt  = PROMPTS.get(block_type, PROMPTS["text"])
        img_b64 = self._to_base64(image)

        num_predict = 4096 if block_type == "table" else 1024
        payload = {
            "model":  self.model,
            "prompt": prompt,
            "images": [img_b64],
            "stream": False,
            "options": {
                "temperature": 0.05,
                "num_predict": num_predict,
            },
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.ollama_url}/api/generate",
                    json=payload,
                )
                resp.raise_for_status()
                result = resp.json().get("response", "").strip()
                if block_type == "table":
                    from pipeline.table_recognizer import TableRecognizer
                    result = TableRecognizer._clean_html(result)
                    result = TableRecognizer._add_table_styles(result)
                return result

        except httpx.TimeoutException:
            logger.error(f"Fallback timeout ({self.timeout}s) для {block_type}")
            return f"[{block_type}: timeout]"
        except Exception as e:
            logger.error(f"Fallback error ({block_type}): {e}")
            return f"[{block_type}: error — {type(e).__name__}]"

    def process_with_model(self, image: Image.Image, block_type: str,
                           model: str | None = None, prompt: str | None = None) -> str:
        """Ollama с конкретной моделью и промптом (для формул, фигур и т.п.)."""
        image   = image.convert("RGB")
        model   = model or self.model
        prompt  = prompt or PROMPTS.get(block_type, PROMPTS["text"])
        img_b64 = self._to_base64(image)

        payload = {
            "model":  model,
            "prompt": prompt,
            "images": [img_b64],
            "stream": False,
            "options": {"temperature": 0.05, "num_predict": 512, "num_ctx": 2048},
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.ollama_url}/api/generate", json=payload)
                resp.raise_for_status()
                return resp.json().get("response", "").strip()
        except Exception as e:
            logger.error(f"process_with_model ({model}): {e}")
            return f"formula error: {type(e).__name__}"
