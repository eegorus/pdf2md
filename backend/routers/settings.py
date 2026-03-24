"""
Роутер: управление настройками и API-ключами
GET  /settings/keys          — получить ключи (замаскированные)
POST /settings/keys          — сохранить ключи
GET  /settings/keys/raw      — получить ключи в открытом виде (для подстановки в запросы)
"""
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Body

logger = logging.getLogger("prms.router.settings")
router = APIRouter()

DATA_DIR    = Path(os.getenv("DATA_DIR", "/app/data"))
SETTINGS_FILE = DATA_DIR / "settings.json"

# Все поддерживаемые провайдеры
PROVIDERS = {
    "openrouter": {
        "label":       "OpenRouter",
        "placeholder": "sk-or-v1-...",
        "url":         "https://openrouter.ai/keys",
        "required_for": ["openrouter"],
    },
    "llamaparse": {
        "label":       "LlamaParse",
        "placeholder": "llx-...",
        "url":         "https://cloud.llamaindex.ai/api-key",
        "required_for": ["llamaparse"],
    },
    "openai": {
        "label":       "OpenAI (GPT-4o)",
        "placeholder": "sk-...",
        "url":         "https://platform.openai.com/api-keys",
        "required_for": ["gpt4o"],
    },
    "anthropic": {
        "label":       "Anthropic (Claude)",
        "placeholder": "sk-ant-...",
        "url":         "https://console.anthropic.com/settings/keys",
        "required_for": ["claude"],
    },
}


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except Exception:
            pass
    return {"keys": {}}


def _save_settings(data: dict):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _mask(value: str) -> str:
    """sk-or-v1-abcdef1234 → sk-or-v1-****1234"""
    if not value or len(value) < 8:
        return "****"
    return value[:8] + "****" + value[-4:]


@router.get("/keys", summary="Список ключей (замаскированные)")
async def get_keys():
    settings = _load_settings()
    keys     = settings.get("keys", {})
    result   = {}
    for provider_id, meta in PROVIDERS.items():
        raw = keys.get(provider_id, "")
        result[provider_id] = {
            **meta,
            "is_set":  bool(raw),
            "masked":  _mask(raw) if raw else "",
        }
    return {"providers": result}


@router.post("/keys", summary="Сохранить API-ключи")
async def save_keys(payload: dict = Body(...)):
    """
    payload: {"openrouter": "sk-or-...", "llamaparse": "llx-...", ...}
    Пустая строка = удалить ключ.
    """
    settings = _load_settings()
    keys     = settings.get("keys", {})

    updated = []
    for provider_id in PROVIDERS:
        if provider_id in payload:
            val = (payload[provider_id] or "").strip()
            if val:
                keys[provider_id] = val
                updated.append(provider_id)
            elif provider_id in keys:
                del keys[provider_id]
                updated.append(f"{provider_id} (удалён)")

    settings["keys"] = keys
    _save_settings(settings)
    logger.info(f"Настройки сохранены: {updated}")
    return {"saved": updated, "total_keys": len(keys)}


@router.get("/keys/raw", summary="Ключи в открытом виде (только для backend)")
async def get_keys_raw():
    """Используется другими роутерами для подстановки ключей в запросы."""
    settings = _load_settings()
    return settings.get("keys", {})


# Карта: тип блока → какие модели могут его обработать
BLOCK_TYPE_MODELS = {
    "text": [
        {"id": "easyocr",      "label": "EasyOCR (локальный)",     "requires": "easyocr"},
        {"id": "gpt4o",        "label": "GPT-4o (облако)",          "requires_key": "openai"},
        {"id": "claude",       "label": "Claude (облако)",           "requires_key": "anthropic"},
        {"id": "openrouter",   "label": "OpenRouter",               "requires_key": "openrouter"},
    ],
    "figure": [
        {"id": "ollama",       "label": "Ollama qwen2.5vl (локальный)", "requires": "ollama"},
        {"id": "gpt4o",        "label": "GPT-4o (облако)",          "requires_key": "openai"},
        {"id": "claude",       "label": "Claude (облако)",           "requires_key": "anthropic"},
        {"id": "openrouter",   "label": "OpenRouter",               "requires_key": "openrouter"},
    ],
    "table_simple": [
        {"id": "dots_ocr",     "label": "dots.ocr (локальный 🥇)",  "requires": "dots_ocr"},
        {"id": "gpt4o",        "label": "GPT-4o (облако)",          "requires_key": "openai"},
        {"id": "openrouter",   "label": "OpenRouter",               "requires_key": "openrouter"},
    ],
    "table_complex": [
        {"id": "dots_ocr",     "label": "dots.ocr (локальный 🥇)",  "requires": "dots_ocr"},
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
        {"id": "gpt4o",        "label": "GPT-4o (облако)",          "requires_key": "openai"},
        {"id": "openrouter",   "label": "OpenRouter",               "requires_key": "openrouter"},
    ],
}

# Дефолтные модели для каждого типа (первая доступная)
DEFAULT_MODEL = {
    "text":          "easyocr",
    "figure":        "ollama",
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
