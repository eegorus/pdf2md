import streamlit as st
import httpx
import os

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(
    page_title="pdf2md",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── SESSION STATE ────────────────────────────────────────────────────────────
if "current_doc_id" not in st.session_state:
    st.session_state.current_doc_id = None
if "current_doc_name" not in st.session_state:
    st.session_state.current_doc_name = None

if not st.session_state.get("access_token"):
    st.switch_page("pages/0_Auth.py")

from utils.styles import inject_global_styles
inject_global_styles()

# ─── HOME PAGE ────────────────────────────────────────────────────────────────
st.title("📄 pdf2md")
st.markdown("""
Table, text, formula and figure extraction from PDF reports.

### Workflow
1. **Upload** — upload a PDF and run the pipeline
2. **Viewer** — view document with overlaid blocks
3. **Review** — review and label blocks manually
4. **Compare** — annotate training pairs
5. **Training** — run model fine-tuning
""")

st.markdown("---")

# ─── HEALTH STATUS ────────────────────────────────────────────────────────────
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
        st.markdown("🔴 **Unavailable**")

with col2:
    st.markdown("### 🤖 Models")
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
            st.caption(f"Free: {free:.1f} GB")
        else:
            st.caption("GPU info unavailable")
    else:
        st.caption("—")

# ─── CURRENT DOCUMENT ─────────────────────────────────────────────────────────
if st.session_state.current_doc_id:
    st.markdown("---")
    st.markdown("### 📂 Current document")
    doc_id = st.session_state.current_doc_id
    try:
        r = httpx.get(f"{BACKEND_URL}/processing/{doc_id}/status", timeout=3)
        s = r.json()
        st.markdown(f"**{st.session_state.current_doc_name}** · `{doc_id}`")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Pages", s.get("page_count", "—"))
        c2.metric("Blocks", s.get("total_blocks", "—"))
        c3.metric("Processed", s.get("processed_blocks", "—"))
        c4.metric("Status", s.get("status", "—"))
    except Exception:
        st.caption(f"`{doc_id}`")

    if st.button("✖ Clear current document"):
        st.session_state.current_doc_id = None
        st.session_state.current_doc_name = None
        st.rerun()

# ─── SIDEBAR ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📄 pdf2md")
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
        st.markdown("🔴 Backend unavailable")
    st.markdown("---")
    st.caption("v1.1.0 · pdf2md")
