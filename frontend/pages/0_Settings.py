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


st.title("⚙️ Settings")
st.caption("API keys are stored encrypted in the database.")

if not st.session_state.get("access_token"):
    st.warning("Please sign in to manage API keys.")
    st.stop()

keys_list = api("GET", "/users/me/api-keys") or []
keys_status = {k["provider"]: k["is_set"] for k in keys_list}

st.markdown("## 🔑 API Keys")

payload = {}
with st.form("keys_form"):
    for pid, meta in PROVIDER_META.items():
        is_set = keys_status.get(pid, False)

        col_label, col_status = st.columns([3, 1])
        with col_label:
            st.markdown(f"**{meta['icon']} {meta['label']}**  [get key ↗]({meta['url']})")
        with col_status:
            if is_set:
                st.success("✅ set", icon=None)
            else:
                st.warning("not set", icon=None)

        val = st.text_input(
            f"Key {meta['label']}",
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
            "💾 Save", type="primary", use_container_width=True
        )
    with col_clear:
        clear = st.form_submit_button(
            "🗑 Delete all keys", use_container_width=True
        )

if submitted:
    to_send = {pid: v for pid, v in payload.items() if v.strip()}
    if not to_send:
        st.info("No new keys to save — fields are empty.")
    else:
        saved = []
        for pid, val in to_send.items():
            res = api("PUT", f"/users/me/api-keys/{pid}", json={"key": val})
            if res:
                saved.append(pid)
        if saved:
            st.success(f"✅ Saved: {', '.join(saved)}")
            st.rerun()

if clear:
    deleted = []
    for pid in PROVIDER_META:
        if keys_status.get(pid):
            res = api("DELETE", f"/users/me/api-keys/{pid}")
            if res:
                deleted.append(pid)
    st.success(f"🗑 Deleted: {', '.join(deleted)}" if deleted else "Nothing to delete")
    st.rerun()

st.markdown("---")
st.markdown("## 🖥 Service Status")

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

st.markdown("---")
st.markdown("## ⚡ Available Parsers")

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
            note = " — ⚠️ key not set"

        st.caption(f"{icon} {key_icon} **{p['label']}**{note}")
