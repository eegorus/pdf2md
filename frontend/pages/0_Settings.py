"""
0_Settings.py — управление API-ключами и настройками
"""
import os
import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Settings — PRMS", layout="centered")

def api(method, path, **kw):
    try:
        r = httpx.request(method, f"{BACKEND_URL}{path}", timeout=10, **kw)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API {path}: {e}")
        return None

# ── Заголовок ──────────────────────────────────────────────────────────────
st.title("⚙️ Настройки")
st.caption("API-ключи хранятся на сервере в `/app/data/settings.json`. Не передаются третьим лицам.")

# ── Загружаем текущие ключи ────────────────────────────────────────────────
providers_data = (api("GET", "/settings/keys") or {}).get("providers", {})

if not providers_data:
    st.error("Не удалось загрузить настройки с backend")
    st.stop()

# ── Форма ──────────────────────────────────────────────────────────────────
st.markdown("## 🔑 API-ключи")

PROVIDER_ICONS = {
    "openrouter": "🌐",
    "llamaparse": "🦙",
    "openai":     "🤖",
    "anthropic":  "🔵",
}

payload = {}
with st.form("keys_form"):
    for pid, meta in providers_data.items():
        icon    = PROVIDER_ICONS.get(pid, "🔑")
        is_set  = meta.get("is_set", False)
        masked  = meta.get("masked", "")
        label   = meta.get("label", pid)
        url     = meta.get("url", "")
        ph      = meta.get("placeholder", "")

        col_label, col_status = st.columns([3, 1])
        with col_label:
            st.markdown(f"**{icon} {label}**  [получить ключ ↗]({url})")
        with col_status:
            if is_set:
                st.success("✅ задан", icon=None)
            else:
                st.warning("не задан", icon=None)

        val = st.text_input(
            f"Ключ {label}",
            value="",
            placeholder=f"{masked}" if is_set else ph,
            type="password",
            key=f"key_{pid}",
            label_visibility="collapsed",
            help=f"Текущий: {masked}" if is_set else "Не задан",
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
    # Отправляем только непустые или намеренно очищаемые
    to_send = {pid: v for pid, v in payload.items() if v.strip()}
    if not to_send:
        st.info("Нет новых ключей для сохранения — поля пустые.")
    else:
        res = api("POST", "/settings/keys", json=to_send)
        if res:
            st.success(f"✅ Сохранено: {', '.join(res.get('saved', []))}")
            st.rerun()

if clear:
    # Передаём пустые строки для всех провайдеров — удалит все ключи
    res = api("POST", "/settings/keys",
              json={pid: "" for pid in providers_data})
    if res:
        st.success("🗑 Все ключи удалены")
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
        avail = p.get("available", False)
        needs_key = p.get("needs_api_key", False)
        icon  = "✅" if avail else "❌"
        key_icon = "🔑" if needs_key else "  "

        pid_map = {
            "gpt4o":      "openai",
            "claude":     "anthropic",
            "llamaparse": "llamaparse",
        }
        key_set = True
        if needs_key:
            mapped = pid_map.get(p["name"])
            if mapped:
                key_set = providers_data.get(mapped, {}).get("is_set", False)

        note = ""
        if needs_key and not key_set:
            note = " — ⚠️ ключ не задан"

        st.caption(f"{icon} {key_icon} **{p['label']}**{note}")
