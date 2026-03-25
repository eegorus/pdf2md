"""
figure_processor.py — описание рисунков через Ollama (qwen2.5vl)

Рисунки не OCR-ятся — они описываются через vision LLM.
Возвращает текстовое описание на том же языке что документ.

Вход:  PIL Image рисунка
Выход: текстовое описание (caption/alt-text)
"""
import base64
import io
import logging
import os

import httpx
from PIL import Image

logger = logging.getLogger("prms.figure_processor")

FIGURE_PROMPT = (
    "Describe this figure/chart/diagram from a technical document concisely. "
    "Focus on: what type of visualization it is, what data it shows, "
    "key values or trends visible. "
    "Be factual and brief (2-4 sentences). "
    "If it contains text, include the key text content."
)


class FigureProcessor:
    def __init__(self):
        self.ollama_url   = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
        self.model        = os.getenv("OLLAMA_FIGURE_MODEL",
                              os.getenv("OLLAMA_FALLBACK_MODEL", "qwen2.5vl:3b"))
        self.timeout      = int(os.getenv("OLLAMA_TIMEOUT", "120"))
        logger.info(f"FigureProcessor: {self.model} @ {self.ollama_url}")

    def _image_to_base64(self, image: Image.Image) -> str:
        """Конвертируем PIL Image в base64 для Ollama API."""
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def describe(self, image: Image.Image, model_override: str | None = None) -> str:
        """
        Отправляет изображение в Ollama и получает описание.

        Ollama API: POST /api/generate
        {
          "model": "qwen2.5vl:7b",
          "prompt": "...",
          "images": ["base64..."],
          "stream": false
        }
        """
        from shared.utils import resize_for_inference
        image, was_resized = resize_for_inference(image, max_pixels=640*640)
        if was_resized:
            logger.debug(f"figure image resized to {image.size} для Ollama")
        image = image.convert("RGB")
        img_b64 = self._image_to_base64(image)

        model = model_override or self.model
        payload = {
            "model":  model,
            "prompt": FIGURE_PROMPT,
            "images": [img_b64],
            "stream": False,
            "options": {
                "temperature": 0.1,   # Низкая температура = фактичность
                "num_ctx": 1024,      # Уменьшаем KV-кэш в VRAM (figure не требует длинного контекста)
                "num_predict": 256,   # Ограничиваем длину описания
            },
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.ollama_url}/api/generate",
                    json=payload,
                )
                resp.raise_for_status()
                result = resp.json()
                description = result.get("response", "").strip()
                logger.debug(f"Figure description: {description[:80]}...")
                return description

        except httpx.TimeoutException:
            logger.warning(f"Ollama timeout ({self.timeout}s) - пропускаем")
            return "[figure: timeout - requires manual review]"
        except Exception as e:
            logger.error(f"FigureProcessor error: {e}")
            return f"[Figure: error — {type(e).__name__}]"

    def describe_file(self, image_path) -> str:
        image = Image.open(str(image_path)).convert("RGB")
        return self.describe(image)
