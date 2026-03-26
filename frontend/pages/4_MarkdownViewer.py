import re
import os

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(
    page_title="Markdown Viewer — PRMS",
    page_icon="📄",
    layout="wide",
)


# ─── Data fetchers ────────────────────────────────────────────────────────────

@st.cache_data(ttl=15)
def fetch_documents():
    try:
        r = httpx.get(f"{BACKEND_URL}/documents/", timeout=5)
        return r.json().get("documents", [])
    except Exception:
        return []


@st.cache_data(ttl=5)
def fetch_markdown(doc_id: str) -> str | None:
    try:
        r = httpx.get(
            f"{BACKEND_URL}/processing/{doc_id}/export-file/markdown",
            timeout=15,
        )
        if r.status_code == 200:
            return r.text
        return None
    except Exception:
        return None


def save_markdown(doc_id: str, content: str) -> bool:
    try:
        r = httpx.patch(
            f"{BACKEND_URL}/processing/{doc_id}/export-file/markdown",
            json={"content": content},
            timeout=15,
        )
        return r.status_code == 200
    except Exception:
        return False


def generate_export(doc_id: str) -> bool:
    try:
        r = httpx.post(
            f"{BACKEND_URL}/processing/{doc_id}/export?format=markdown",
            timeout=60,
        )
        return r.status_code == 200
    except Exception:
        return False


# ─── Document selector ────────────────────────────────────────────────────────

st.title("📄 Markdown Viewer")

docs = fetch_documents()
ready_docs = [d for d in docs if d.get("status") in ("ocr_done", "ocrdone")]

if not ready_docs:
    st.info("Нет документов с завершённым OCR. Перейди в Upload и запусти OCR.")
    st.stop()

default_doc_id = st.session_state.get("viewer_doc_id") or st.session_state.get("md_viewer_doc_id")

doc_options = {
    d["doc_id"]: f"{d.get('filename', d['doc_id'][:12])} ({d.get('page_count', '?')} стр.)"
    for d in ready_docs
}

selected_doc_id = st.selectbox(
    "Документ",
    options=list(doc_options.keys()),
    format_func=lambda x: doc_options[x],
    index=list(doc_options.keys()).index(default_doc_id)
          if default_doc_id in doc_options else 0,
    key="md_viewer_doc_id",
)

st.divider()

# ─── Load or generate markdown ────────────────────────────────────────────────

md_content = fetch_markdown(selected_doc_id)

if md_content is None:
    with st.spinner("Генерируем export.md..."):
        ok = generate_export(selected_doc_id)
        if ok:
            fetch_markdown.clear()
            md_content = fetch_markdown(selected_doc_id)
        else:
            st.error("Не удалось сгенерировать markdown. Проверь статус OCR.")
            st.stop()

if md_content is None:
    st.error("export.md не найден даже после генерации.")
    st.stop()

# ─── Editor state ─────────────────────────────────────────────────────────────

_edit_key = f"md_edit_{selected_doc_id}"
_dirty_key = f"md_dirty_{selected_doc_id}"

if _edit_key not in st.session_state:
    st.session_state[_edit_key] = md_content
    st.session_state[_dirty_key] = False

# ─── Toolbar ──────────────────────────────────────────────────────────────────

col_mode, col_save, col_reset, col_dl = st.columns([3, 1.2, 1.2, 1.2])

with col_mode:
    view_mode = st.radio(
        "Режим",
        ["👁 Просмотр", "✏️ Редактор", "↕️ Split"],
        horizontal=True,
        key="md_view_mode",
        label_visibility="collapsed",
    )

is_dirty = st.session_state.get(_dirty_key, False)

with col_save:
    if st.button(
        "💾 Сохранить",
        disabled=not is_dirty,
        use_container_width=True,
        type="primary" if is_dirty else "secondary",
        key="btn_md_save",
    ):
        ok = save_markdown(selected_doc_id, st.session_state[_edit_key])
        if ok:
            st.session_state[_dirty_key] = False
            fetch_markdown.clear()
            st.success("Сохранено!")
            st.rerun()
        else:
            st.error("Ошибка сохранения")

with col_reset:
    if st.button(
        "↩️ Сбросить",
        disabled=not is_dirty,
        use_container_width=True,
        key="btn_md_reset",
    ):
        st.session_state[_edit_key] = md_content
        st.session_state[_dirty_key] = False
        st.rerun()

with col_dl:
    st.download_button(
        "⬇️ Скачать",
        data=st.session_state.get(_edit_key, md_content).encode("utf-8"),
        file_name=f"{selected_doc_id[:8]}_export.md",
        mime="text/markdown",
        use_container_width=True,
        key="btn_md_dl",
    )

st.divider()

# ─── Render helpers ───────────────────────────────────────────────────────────

_HTML_BLOCK_RE = re.compile(
    r'(<div[^>]*>.*?</div>|<table[^>]*>.*?</table>)',
    re.DOTALL | re.IGNORECASE,
)


def render_preview(content: str):
    for part in _HTML_BLOCK_RE.split(content):
        stripped = part.strip()
        if stripped:
            st.markdown(stripped, unsafe_allow_html=True)


def render_editor(content: str, key: str) -> str:
    return st.text_area(
        "Markdown",
        value=content,
        height=800,
        key=key + "_area",
        label_visibility="collapsed",
    )


# ─── Content area ─────────────────────────────────────────────────────────────

if view_mode == "👁 Просмотр":
    render_preview(st.session_state.get(_edit_key, md_content))

elif view_mode == "✏️ Редактор":
    new_content = render_editor(st.session_state[_edit_key], _edit_key)
    if new_content != st.session_state[_edit_key]:
        st.session_state[_edit_key] = new_content
        st.session_state[_dirty_key] = True

else:  # Split
    col_left, col_right = st.columns(2)
    with col_left:
        st.caption("👁 Превью")
        render_preview(st.session_state.get(_edit_key, md_content))
    with col_right:
        st.caption("✏️ Редактор")
        new_content = render_editor(st.session_state[_edit_key], _edit_key)
        if new_content != st.session_state[_edit_key]:
            st.session_state[_edit_key] = new_content
            st.session_state[_dirty_key] = True
