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
