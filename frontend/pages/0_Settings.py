"""
0_Settings.py — управление API-ключами и настройками
"""
import os
import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Settings — PRMS", layout="centered")

PROVIDER_META = {
    "openrouter": {
        "label":       "OpenRouter",
        "icon":        "🌐",
        "placeholder": "sk-or-v1-...",
        "url":         "https://openrouter.ai/keys",
    },
    "llamaparse": {
        "label":       "LlamaParse",
        "icon":        "🦙",
        "placeholder": "llx-...",
        "url":         "https://cloud.llamaindex.ai/api-key",
    },
    "openai": {
        "label":       "OpenAI (GPT-4o)",
        "icon":        "🤖",
        "placeholder": "sk-...",
        "url":         "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "label":       "Anthropic (Claude)",
        "icon":        "🔵",
        "placeholder": "sk-ant-...",
        "url":         "https://console.anthropic.com/settings/keys",
    },
}

_PARSER_PROVIDER = {
    "gpt4o":      "openai",
    "claude":     "anthropic",
    "llamaparse": "llamaparse",
    "openrouter": "openrouter",
}


def api(method, path, **kw):
    try:
        headers = kw.pop("headers", {})
        token = st.session_state.get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = httpx.request(method, f"{BACKEND_URL}{path}", timeout=10, headers=headers, **kw)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API {path}: {e}")
        return None


# ── Заголовок ──────────────────────────────────────────────────────────────
st.title("⚙️ Настройки")
st.caption("API-ключи хранятся в зашифрованном виде в базе данных.")

# ── Загружаем текущие ключи ────────────────────────────────────────────────
if not st.session_state.get("access_token"):
    st.warning("Войдите в систему для управления API-ключами.")
    st.stop()

keys_list = api("GET", "/users/me/api-keys") or []
keys_status = {k["provider"]: k["is_set"] for k in keys_list}

# ── Форма ──────────────────────────────────────────────────────────────────
st.markdown("## 🔑 API-ключи")

payload = {}
with st.form("keys_form"):
    for pid, meta in PROVIDER_META.items():
        is_set = keys_status.get(pid, False)

        col_label, col_status = st.columns([3, 1])
        with col_label:
            st.markdown(f"**{meta['icon']} {meta['label']}**  [получить ключ ↗]({meta['url']})")
        with col_status:
            if is_set:
                st.success("✅ задан", icon=None)
            else:
                st.warning("не задан", icon=None)

        val = st.text_input(
            f"Ключ {meta['label']}",
            value="",
            placeholder=meta["placeholder"],
            type="password",
            key=f"key_{pid}",
            label_visibility="collapsed",
        )
        payload[pid] = val
        st.markdown("")

    st.markdown("---")
    col_save, col_clear = st.columns([2, 1])
    with col_save:
        submitted = st.form_submit_button(
            "💾 Сохранить", type="primary", use_container_width=True
        )
    with col_clear:
        clear = st.form_submit_button(
            "🗑 Удалить все ключи", use_container_width=True
        )

# ── Обработка ──────────────────────────────────────────────────────────────
if submitted:
    to_send = {pid: v for pid, v in payload.items() if v.strip()}
    if not to_send:
        st.info("Нет новых ключей для сохранения — поля пустые.")
    else:
        saved = []
        for pid, val in to_send.items():
            res = api("PUT", f"/users/me/api-keys/{pid}", json={"key": val})
            if res:
                saved.append(pid)
        if saved:
            st.success(f"✅ Сохранено: {', '.join(saved)}")
            st.rerun()

if clear:
    deleted = []
    for pid in PROVIDER_META:
        if keys_status.get(pid):
            res = api("DELETE", f"/users/me/api-keys/{pid}")
            if res:
                deleted.append(pid)
    st.success(f"🗑 Удалены: {', '.join(deleted)}" if deleted else "Нечего удалять")
    st.rerun()

# ── Статус моделей ─────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("## 🖥 Статус сервисов")

health = api("GET", "/health") or {}
models_loaded = health.get("models_loaded", {})
overall       = health.get("status", "unknown")

status_color = {"ok": "🟢", "partial": "🟡", "error": "🔴"}.get(overall, "⚪")
st.markdown(f"Backend: {status_color} **{overall}**")

if models_loaded:
    cols = st.columns(3)
    for i, (model, loaded) in enumerate(models_loaded.items()):
        with cols[i % 3]:
            icon = "✅" if loaded else "❌"
            st.caption(f"{icon} {model}")

# ── Парсеры ────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("## ⚡ Доступные парсеры")

parsers = (api("GET", "/quick/parsers") or [])
if parsers:
    for p in parsers:
        avail     = p.get("available", False)
        needs_key = p.get("needs_api_key", False)
        icon      = "✅" if avail else "❌"
        key_icon  = "🔑" if needs_key else "  "

        key_set = True
        if needs_key:
            mapped = _PARSER_PROVIDER.get(p["name"])
            if mapped:
                key_set = keys_status.get(mapped, False)

        note = ""
        if needs_key and not key_set:
            note = " — ⚠️ ключ не задан"

        st.caption(f"{icon} {key_icon} **{p['label']}**{note}")
