import re
import os

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
PUBLIC_BACKEND_URL = os.getenv("PUBLIC_BACKEND_URL", "http://localhost:8000")

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

col_mode, col_save, col_reset, col_dl, col_zip = st.columns([3, 1.2, 1.2, 1.2, 1.2])

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

with col_zip:
    _zip_key = f"zip_bytes_{selected_doc_id}"
    if _zip_key not in st.session_state:
        try:
            r = httpx.get(
                f"{BACKEND_URL}/processing/{selected_doc_id}/export-zip",
                timeout=30,
            )
            st.session_state[_zip_key] = r.content if r.status_code == 200 else None
        except Exception:
            st.session_state[_zip_key] = None
    _zip_bytes = st.session_state.get(_zip_key)
    if _zip_bytes:
        st.download_button(
            "📦 ZIP",
            data=_zip_bytes,
            file_name=f"{selected_doc_id[:8]}_export.zip",
            mime="application/zip",
            use_container_width=True,
            key="btn_md_zip",
        )
    else:
        st.button("📦 ZIP", disabled=True, use_container_width=True, key="btn_md_zip")

st.divider()

_IMG_RE = re.compile(r'!\[([^\]]*)\]\(\./blocks/([^)]+)\)')


@st.cache_data(ttl=300)
def _fetch_image_b64(doc_id: str, filename: str) -> str | None:
    """Загружает PNG блока через внутренний Docker URL, возвращает base64."""
    import base64
    try:
        r = httpx.get(
            f"{BACKEND_URL}/processing/{doc_id}/media/{filename}",
            timeout=10,
        )
        if r.status_code == 200:
            return base64.b64encode(r.content).decode("ascii")
    except Exception:
        pass
    return None


def resolve_media_urls(content: str, doc_id: str) -> str:
    """Заменяет ./blocks/filename.png на inline base64 <img> для рендера в браузере."""
    def _replace(m: re.Match) -> str:
        alt = m.group(1).replace('"', "'")
        filename = m.group(2)
        b64 = _fetch_image_b64(doc_id, filename)
        if b64:
            return f'<img src="data:image/png;base64,{b64}" alt="{alt}" style="max-width:100%;height:auto;" />'
        return f"_{alt if alt else 'Figure'}_"

    return _IMG_RE.sub(_replace, content)


# ─── LaTeX editor tools ───────────────────────────────────────────────────────

def render_latex_toolbar(edit_key: str) -> None:
    """Сниппеты LaTeX для копирования в буфер и вставки в нужное место."""
    SNIPPETS = [
        ("x²  — Степень 2",          "$x^{2}$"),
        ("xₙ  — Нижний индекс",      "$x_{n}$"),
        ("10³ft³ — Объём газа",       "$10^3\\,\\text{ft}^3$"),
        ("10³bbl — Объём нефти",      "$10^3\\,\\text{bbl}$"),
        ("10⁶bbl",                    "$10^6\\,\\text{bbl}$"),
        ("$/bbl  — Цена нефти",       "\\$/\\text{bbl}"),
        ("$/Mscf — Цена газа",        "\\$/\\text{Mscf}"),
        ("CO₂",                       "CO$_{2}$"),
        ("H₂S",                       "H$_{2}$S"),
        ("± / ×",                     "$\\pm$ / $\\times$"),
        ("a/b  — Дробь",              "$\\frac{a}{b}$"),
        ("∑  — Сумма",                "$$\\sum_{i=1}^{n} x_i$$"),
    ]

    with st.expander("⚡ LaTeX сниппеты — кликни поле, Ctrl+A, Ctrl+C, потом вставь в редактор", expanded=False):
        cols = st.columns(3)
        for i, (label, latex) in enumerate(SNIPPETS):
            with cols[i % 3]:
                st.text_input(
                    label,
                    value=latex,
                    key=f"snip_{i}_{edit_key}",
                    disabled=True,
                )


def render_find_replace(edit_key: str) -> None:
    """Панель поиска и замены с поддержкой regex."""
    with st.expander("🔍 Найти и заменить", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            find_val = st.text_input(
                "Найти",
                key=f"fr_find_{edit_key}",
                placeholder=r"10\^?3ft\^?3",
            )
        with col2:
            repl_val = st.text_input(
                "Заменить на",
                key=f"fr_repl_{edit_key}",
                placeholder=r"$10^3\,\text{ft}^3$",
            )

        col_chk, col_cnt, col_go = st.columns([2, 1, 1])
        with col_chk:
            use_regex = st.checkbox(
                "Использовать regex",
                value=True,
                key=f"fr_re_{edit_key}",
            )
        with col_cnt:
            if st.button("Посчитать", key=f"fr_cnt_{edit_key}",
                         use_container_width=True):
                content = st.session_state.get(edit_key, "")
                if find_val:
                    try:
                        if use_regex:
                            matches = re.findall(find_val, content)
                        else:
                            matches = content.split(find_val)[:-1]
                        st.info(f"Найдено: {len(matches)}")
                    except re.error as e:
                        st.error(f"Regex ошибка: {e}")
        with col_go:
            if st.button("Заменить всё", key=f"fr_do_{edit_key}",
                         use_container_width=True, type="primary"):
                content = st.session_state.get(edit_key, "")
                if find_val:
                    try:
                        if use_regex:
                            new_content, count = re.subn(find_val, repl_val, content)
                        else:
                            count = content.count(find_val)
                            new_content = content.replace(find_val, repl_val)
                        st.session_state[edit_key] = new_content
                        st.success(f"✅ Заменено: {count}")
                        st.rerun()
                    except re.error as e:
                        st.error(f"Regex ошибка: {e}")


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
    resolved = resolve_media_urls(st.session_state.get(_edit_key, md_content), selected_doc_id)
    render_preview(resolved)

elif view_mode == "✏️ Редактор":
    render_latex_toolbar(_edit_key)
    render_find_replace(_edit_key)
    new_content = render_editor(st.session_state[_edit_key], _edit_key)
    if new_content != st.session_state[_edit_key]:
        st.session_state[_edit_key] = new_content
        st.session_state[_dirty_key] = True

else:  # Split
    render_latex_toolbar(_edit_key)
    render_find_replace(_edit_key)
    col_left, col_right = st.columns(2)
    with col_left:
        st.caption("👁 Превью")
        with st.container(height=800, border=False):
            resolved = resolve_media_urls(st.session_state.get(_edit_key, md_content), selected_doc_id)
            render_preview(resolved)
    with col_right:
        st.caption("✏️ Редактор")
        new_content = render_editor(st.session_state[_edit_key], _edit_key)
        if new_content != st.session_state[_edit_key]:
            st.session_state[_edit_key] = new_content
            st.session_state[_dirty_key] = True
