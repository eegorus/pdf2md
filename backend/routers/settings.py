import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter

logger = logging.getLogger("prms.router.settings")
router = APIRouter()

DATA_DIR      = Path(os.getenv("DATA_DIR", "/app/data"))
SETTINGS_FILE = DATA_DIR / "settings.json"


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except Exception:
            pass
    return {"keys": {}}


# Карта: тип блока → какие модели могут его обработать
BLOCK_TYPE_MODELS = {
    "text": [
        {"id": "easyocr",      "label": "EasyOCR (локальный)",     "requires": "easyocr"},
        {"id": "gpt4o",        "label": "GPT-4o (облако)",          "requires_key": "openai"},
        {"id": "claude",       "label": "Claude (облако)",           "requires_key": "anthropic"},
        {"id": "openrouter",   "label": "OpenRouter",               "requires_key": "openrouter"},
    ],
    "figure": [
        {"id": "ollama_3b",    "label": "Ollama qwen2.5vl:3b (быстрый)",  "requires": "ollama"},
        {"id": "ollama_7b",    "label": "Ollama qwen2.5vl:7b (качество)", "requires": "ollama"},
        {"id": "gpt4o",        "label": "GPT-4o (облако)",                "requires_key": "openai"},
        {"id": "claude",       "label": "Claude (облако)",                 "requires_key": "anthropic"},
        {"id": "openrouter",   "label": "OpenRouter",                     "requires_key": "openrouter"},
    ],
    "table_simple": [
        {"id": "dots_ocr",     "label": "dots.ocr (локальный 🥇)",  "requires": "dots_ocr"},
        {"id": "ollama_7b",    "label": "Ollama qwen2.5vl:7b (fallback)", "requires": "ollama"},
        {"id": "gpt4o",        "label": "GPT-4o (облако)",          "requires_key": "openai"},
        {"id": "openrouter",   "label": "OpenRouter",               "requires_key": "openrouter"},
    ],
    "table_complex": [
        {"id": "dots_ocr",     "label": "dots.ocr (локальный 🥇)",  "requires": "dots_ocr"},
        {"id": "ollama_7b",    "label": "Ollama qwen2.5vl:7b (fallback)", "requires": "ollama"},
        {"id": "gpt4o",        "label": "GPT-4o (облако)",          "requires_key": "openai"},
        {"id": "claude",       "label": "Claude (облако)",           "requires_key": "anthropic"},
        {"id": "openrouter",   "label": "OpenRouter",               "requires_key": "openrouter"},
    ],
    "formula": [
        {"id": "texteller",    "label": "TexTeller (локальный 🥇)", "requires": "texteller"},
        {"id": "ollama",       "label": "Ollama qwen2.5vl (локальный)", "requires": "ollama"},
        {"id": "gpt4o",        "label": "GPT-4o (облако)",          "requires_key": "openai"},
        {"id": "openrouter",   "label": "OpenRouter",               "requires_key": "openrouter"},
    ],
    # legacy — обратная совместимость
    "table": [
        {"id": "dots_ocr",     "label": "dots.ocr (локальный 🥇)",  "requires": "dots_ocr"},
        {"id": "ollama_7b",    "label": "Ollama qwen2.5vl:7b (fallback)", "requires": "ollama"},
        {"id": "gpt4o",        "label": "GPT-4o (облако)",          "requires_key": "openai"},
        {"id": "openrouter",   "label": "OpenRouter",               "requires_key": "openrouter"},
    ],
}

# Дефолтные модели для каждого типа (первая доступная)
DEFAULT_MODEL = {
    "text":          "easyocr",
    "figure":        "ollama_3b",
    "table_simple":  "dots_ocr",
    "table_complex": "dots_ocr",
    "formula":       "texteller",
    "table":         "dots_ocr",
}


@router.get("/available-models", summary="Доступные модели по типам блоков")
async def get_available_models():
    """
    Возвращает список моделей для каждого типа блока.
    available=true если модель загружена / ключ задан.
    """
    # Читаем статус моделей напрямую из памяти — без HTTP запроса
    try:
        from main import models as _models
        models_loaded = dict(_models.status)
    except Exception as _e:
        logger.warning("Не удалось импортировать models из main: %s", _e)
        models_loaded = {}

    # Получаем заданные ключи
    settings  = _load_settings()
    keys      = settings.get("keys", {})

    result = {}
    for block_type, models in BLOCK_TYPE_MODELS.items():
        enriched = []
        for m in models:
            avail = False
            reason = ""
            if "requires" in m:
                avail  = models_loaded.get(m["requires"], False)
                reason = "" if avail else f"модель {m['requires']} не загружена"
            elif "requires_key" in m:
                avail  = bool(keys.get(m["requires_key"], ""))
                reason = "" if avail else f"API-ключ {m['requires_key']} не задан"

            enriched.append({
                "id":        m["id"],
                "label":     m["label"],
                "available": avail,
                "reason":    reason,
            })

        # Определяем дефолт — первая доступная или первая в списке
        default_id = DEFAULT_MODEL.get(block_type, enriched[0]["id"])
        if not any(e["id"] == default_id and e["available"] for e in enriched):
            available_first = next((e["id"] for e in enriched if e["available"]), default_id)
            default_id = available_first

        result[block_type] = {
            "models":  enriched,
            "default": default_id,
        }

    return result
