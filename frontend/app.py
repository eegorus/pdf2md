import streamlit as st
import httpx
import os

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(
    page_title="PRMS Table Extractor",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── SESSION STATE ────────────────────────────────────────────────────────────
if "current_doc_id" not in st.session_state:
    st.session_state.current_doc_id = None
if "current_doc_name" not in st.session_state:
    st.session_state.current_doc_name = None

# ─── ГЛАВНАЯ СТРАНИЦА ─────────────────────────────────────────────────────────
st.title("📄 PRMS Table Extractor")
st.markdown("""
Система извлечения таблиц, текста, формул и фигур из PDF-отчётов ПМРС.

### Workflow
1. **Upload** — загрузи PDF и запусти pipeline
2. **Viewer** — просматривай документ с наложенными блоками
3. **Review** — проверяй и помечай блоки вручную
4. **Compare** — размечай обучающие пары
5. **Training** — запускай fine-tuning модели
""")

st.markdown("---")

# ─── HEALTH СТАТУС ────────────────────────────────────────────────────────────
@st.cache_data(ttl=10)
def get_health():
    try:
        r = httpx.get(f"{BACKEND_URL}/health", timeout=3)
        return r.json()
    except Exception:
        return None

health = get_health()

col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("### 🖥️ Backend")
    if health:
        status = health.get("status", "unknown")
        color = {"ok": "🟢", "degraded": "🟡", "error": "🔴"}.get(status, "⚪")
        st.markdown(f"{color} **{status}**")
        st.caption(f"v{health.get('version', '—')}")
    else:
        st.markdown("🔴 **Недоступен**")

with col2:
    st.markdown("### 🤖 Модели")
    if health:
        models = health.get("models_loaded", {})
        for model, loaded in models.items():
            icon = "✅" if loaded else "❌"
            st.markdown(f"{icon} `{model}`")
    else:
        st.caption("—")

with col3:
    st.markdown("### 🎮 GPU")
    if health:
        gpu_used = health.get("gpu_memory_used_gb")
        gpu_total = health.get("gpu_memory_total_gb")
        if gpu_used and gpu_total:
            st.progress(
                gpu_used / gpu_total,
                text=f"VRAM: {gpu_used:.1f} / {gpu_total:.1f} GB"
            )
            free = gpu_total - gpu_used
            st.caption(f"Свободно: {free:.1f} GB")
        else:
            st.caption("GPU info недоступна")
    else:
        st.caption("—")

# ─── ТЕКУЩИЙ ДОКУМЕНТ ─────────────────────────────────────────────────────────
if st.session_state.current_doc_id:
    st.markdown("---")
    st.markdown("### 📂 Текущий документ")
    doc_id = st.session_state.current_doc_id
    try:
        r = httpx.get(f"{BACKEND_URL}/processing/{doc_id}/status", timeout=3)
        s = r.json()
        st.markdown(f"**{st.session_state.current_doc_name}** · `{doc_id}`")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Страниц", s.get("page_count", "—"))
        c2.metric("Блоков", s.get("total_blocks", "—"))
        c3.metric("Обработано", s.get("processed_blocks", "—"))
        c4.metric("Статус", s.get("status", "—"))
    except Exception:
        st.caption(f"`{doc_id}`")

    if st.button("✖ Сбросить текущий документ"):
        st.session_state.current_doc_id = None
        st.session_state.current_doc_name = None
        st.rerun()

# ─── SIDEBAR (минимальный) ────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📄 PRMS Extractor")
    st.markdown("---")
    if health:
        status = health.get("status", "unknown")
        color = {"ok": "🟢", "degraded": "🟡", "error": "🔴"}.get(status, "⚪")
        st.markdown(f"{color} Backend: **{status}**")
        gpu_used = health.get("gpu_memory_used_gb")
        gpu_total = health.get("gpu_memory_total_gb")
        if gpu_used and gpu_total:
            st.progress(gpu_used / gpu_total,
                       text=f"VRAM: {gpu_used:.1f}/{gpu_total:.1f} GB")
    else:
        st.markdown("🔴 Backend недоступен")
    st.markdown("---")
    st.caption("v1.1.0 · PRMS Table Extractor")
