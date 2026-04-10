"""
fallback_api.py — Ollama как универсальный fallback для любого блока

Используется когда:
- dots.ocr не загружен → таблица → Ollama
- TexTeller не работает → формула → Ollama
- EasyOCR дал пустой результат → текст → Ollama
- Любой другой сбой специализированного модуля

Для таблиц использует тот же промпт что и dots.ocr (DOTS_SYSTEM_PROMPT / DOTS_USER_PROMPT).
"""
import base64
import io
import logging
import os

import httpx
from PIL import Image

from pipeline.table_recognizer import (
    DOTS_SYSTEM_PROMPT,
    DOTS_USER_PROMPT,
    TableRecognizer,
)

logger = logging.getLogger("prms.fallback_api")

TABLE_BLOCK_TYPES = {"table", "table_simple", "table_complex"}

PROMPTS = {
    "text": (
        "Extract all text from this image exactly as written. "
        "Preserve line breaks and formatting. "
        "Return only the text content, no explanations."
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

    def _call_ollama(
        self,
        image: Image.Image,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        num_predict: int = 2048,
    ) -> str | None:
        """Базовый вызов Ollama generate API. Возвращает текст или None при ошибке."""
        model   = model or self.model
        img_b64 = self._to_base64(image)

        payload = {
            "model":  model,
            "prompt": prompt,
            "images": [img_b64],
            "stream": False,
            "options": {
                "temperature": 0.05,
                "num_predict": num_predict,
                "num_ctx":     4096,
            },
        }
        if system:
            payload["system"] = system

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.ollama_url}/api/generate", json=payload)
                resp.raise_for_status()
                result = resp.json().get("response", "").strip()
                logger.debug(f"Ollama {model} [{len(result)} chars]: {result[:80]!r}")
                return result or None
        except Exception as e:
            logger.error(f"Ollama {model}: {e}")
            return None

    def process(self, image: Image.Image, block_type: str) -> str:
        """Обрабатывает блок через Ollama. Для таблиц использует промпт dots.ocr."""
        image = image.convert("RGB")

        if block_type in TABLE_BLOCK_TYPES:
            return self._process_table(image)

        prompt = PROMPTS.get(block_type, PROMPTS["text"])
        result = self._call_ollama(image, prompt=prompt)
        if result is None:
            return f"[{block_type}: timeout]"
        return result

    def _process_table(self, image: Image.Image) -> str:
        """Обрабатывает таблицу с промптом dots.ocr → нормализует HTML."""
        raw = self._call_ollama(
            image,
            prompt=DOTS_USER_PROMPT,
            system=DOTS_SYSTEM_PROMPT,
            num_predict=4096,
        )
        if not raw:
            return "[table: timeout]"
        cleaned = TableRecognizer._clean_html(raw)
        return TableRecognizer._add_table_styles(cleaned) if cleaned else raw

    def process_with_model(
        self,
        image: Image.Image,
        block_type: str,
        model: str | None = None,
        prompt: str | None = None,
    ) -> str:
        """Ollama с конкретной моделью и промптом (для формул, фигур и т.п.)."""
        image  = image.convert("RGB")
        prompt = prompt or PROMPTS.get(block_type, PROMPTS["text"])
        result = self._call_ollama(image, prompt=prompt, model=model, num_predict=512)
        if result is None:
            return f"formula error: timeout"
        return result
