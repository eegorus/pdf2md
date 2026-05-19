"""
OpenRouter — облачный парсер PDF → Markdown через vision модели.
Использует API-ключ из /app/data/settings.json
"""
import json
from pathlib import Path
from .base import BaseParser, SYSTEM_PROMPT
from .cloud_parser import _openai_content


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

    def _call_api(self, page_b64, figure_parts, user_msg, api_key, **kwargs) -> str:
        import httpx
        model = kwargs.get("model") or self.default_model
        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization":  f"Bearer {api_key}",
                "HTTP-Referer":   "https://prms-local",
                "X-Title":        "PRMS PDF Parser",
                "Content-Type":   "application/json",
            },
            json={
                "model": model,
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
