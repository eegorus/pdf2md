import re
import os

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
PUBLIC_BACKEND_URL = os.getenv("PUBLIC_BACKEND_URL", "http://localhost:8000")


def api(method: str, path: str, **kw):
    """Authenticated httpx request; returns raw Response or None on error."""
    try:
        headers = kw.pop("headers", {})
        token = st.session_state.get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        kw.setdefault("timeout", 60)
        return httpx.request(method, f"{BACKEND_URL}{path}", headers=headers, **kw)
    except Exception as e:
        st.error(f"API error ({path}): {e}")
        return None

st.set_page_config(
    page_title="Markdown Viewer — PRMS",
    page_icon="📄",
    layout="wide",
)

from utils.auth import ensure_authenticated
if not ensure_authenticated():
    st.stop()

from components.auth_guard import require_auth, render_sidebar_user
current_user = require_auth()
render_sidebar_user()


# ─── Data fetchers ────────────────────────────────────────────────────────────

@st.cache_data(ttl=15)
def fetch_documents(token: str = ""):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = httpx.get(f"{BACKEND_URL}/documents/", headers=headers, timeout=5)
        return r.json().get("documents", [])
    except Exception:
        return []


@st.cache_data(ttl=5)
def fetch_markdown(doc_id: str, token: str = "") -> str | None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = httpx.get(
            f"{BACKEND_URL}/processing/{doc_id}/export-file/markdown",
            headers=headers,
            timeout=15,
        )
        if r.status_code == 200:
            return r.text
        return None
    except Exception:
        return None


def save_markdown(doc_id: str, content: str) -> bool:
    r = api("PATCH", f"/processing/{doc_id}/export-file/markdown",
            json={"content": content}, timeout=15)
    return r is not None and r.status_code == 200


def generate_export(doc_id: str) -> bool:
    r = api("POST", f"/processing/{doc_id}/export?format=markdown", timeout=60)
    return r is not None and r.status_code == 200


# ─── Document selector ────────────────────────────────────────────────────────

st.title("📄 Markdown Viewer")

docs = fetch_documents(st.session_state.get("access_token", ""))
ready_docs = [d for d in docs if d.get("status") in ("ocr_done", "ocrdone", "done")]

if not ready_docs:
    st.info("No processed documents. Upload a PDF and run processing.")
    st.stop()

default_doc_id = st.session_state.get("viewer_doc_id") or st.session_state.get("md_viewer_doc_id")

doc_options = {
    d["doc_id"]: (
        f"⚡ {d.get('filename', d['doc_id'][:12])} ({d.get('page_count', '?')} pp.)"
        if d.get("mode") == "quick" else
        f"🔬 {d.get('filename', d['doc_id'][:12])} ({d.get('page_count', '?')} pp.)"
    )
    for d in ready_docs
}

selected_doc_id = st.selectbox(
    "Document",
    options=list(doc_options.keys()),
    format_func=lambda x: doc_options[x],
    index=list(doc_options.keys()).index(default_doc_id)
          if default_doc_id in doc_options else 0,
    key="md_viewer_doc_id",
)

st.divider()

# ─── Load or generate markdown ────────────────────────────────────────────────

_token = st.session_state.get("access_token", "")
md_content = fetch_markdown(selected_doc_id, _token)

if md_content is None:
    with st.spinner("Generating export.md..."):
        ok = generate_export(selected_doc_id)
        if ok:
            fetch_markdown.clear()
            md_content = fetch_markdown(selected_doc_id, _token)
        else:
            st.error("Failed to generate markdown. Check OCR status.")
            st.stop()

if md_content is None:
    st.error("export.md not found even after generation.")
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
        "Mode",
        ["👁 Preview", "✏️ Editor", "↕️ Split"],
        horizontal=True,
        key="md_view_mode",
        label_visibility="collapsed",
    )

is_dirty = st.session_state.get(_dirty_key, False)

with col_save:
    if st.button(
        "💾 Save",
        disabled=not is_dirty,
        use_container_width=True,
        type="primary" if is_dirty else "secondary",
        key="btn_md_save",
    ):
        ok = save_markdown(selected_doc_id, st.session_state[_edit_key])
        if ok:
            st.session_state[_dirty_key] = False
            fetch_markdown.clear()
            st.success("Saved!")
            st.rerun()
        else:
            st.error("Save error")

with col_reset:
    if st.button(
        "↩️ Reset",
        disabled=not is_dirty,
        use_container_width=True,
        key="btn_md_reset",
    ):
        st.session_state[_edit_key] = md_content
        st.session_state[_dirty_key] = False
        st.rerun()

with col_dl:
    st.download_button(
        "⬇️ Download",
        data=st.session_state.get(_edit_key, md_content).encode("utf-8"),
        file_name=f"{selected_doc_id[:8]}_export.md",
        mime="text/markdown",
        use_container_width=True,
        key="btn_md_dl",
    )

with col_zip:
    _zip_key = f"zip_bytes_{selected_doc_id}"
    if _zip_key not in st.session_state:
        r = api("GET", f"/processing/{selected_doc_id}/export-zip", timeout=30)
        st.session_state[_zip_key] = r.content if r and r.status_code == 200 else None
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
def _fetch_image_b64(doc_id: str, filename: str, token: str = "") -> str | None:
    import base64
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = httpx.get(
            f"{BACKEND_URL}/processing/{doc_id}/media/{filename}",
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            return base64.b64encode(r.content).decode("ascii")
    except Exception:
        pass
    return None


def resolve_media_urls(content: str, doc_id: str) -> str:
    token = st.session_state.get("access_token", "")

    def _replace(m: re.Match) -> str:
        alt = m.group(1).replace('"', "'")
        filename = m.group(2)
        b64 = _fetch_image_b64(doc_id, filename, token)
        if b64:
            return f'<img src="data:image/png;base64,{b64}" alt="{alt}" style="max-width:100%;height:auto;" />'
        return f"_{alt if alt else 'Figure'}_"

    return _IMG_RE.sub(_replace, content)


# ─── LaTeX editor tools ───────────────────────────────────────────────────────

def render_latex_toolbar(edit_key: str) -> None:
    SNIPPETS = [
        ("x²  — Superscript 2",    "$x^{2}$"),
        ("xₙ  — Subscript",        "$x_{n}$"),
        ("10³ft³ — Gas volume",    "$10^3\\,\\text{ft}^3$"),
        ("10³bbl — Oil volume",    "$10^3\\,\\text{bbl}$"),
        ("10⁶bbl",                  "$10^6\\,\\text{bbl}$"),
        ("$/bbl  — Oil price",     "\\$/\\text{bbl}"),
        ("$/Mscf — Gas price",     "\\$/\\text{Mscf}"),
        ("CO₂",                     "CO$_{2}$"),
        ("H₂S",                     "H$_{2}$S"),
        ("± / ×",                   "$\\pm$ / $\\times$"),
        ("a/b  — Fraction",        "$\\frac{a}{b}$"),
        ("∑  — Sum",               "$$\\sum_{i=1}^{n} x_i$$"),
    ]

    with st.expander("⚡ LaTeX snippets — click field, Ctrl+A, Ctrl+C, then paste into editor", expanded=False):
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
    with st.expander("🔍 Find & Replace", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            find_val = st.text_input(
                "Find",
                key=f"fr_find_{edit_key}",
                placeholder=r"10\^?3ft\^?3",
            )
        with col2:
            repl_val = st.text_input(
                "Replace with",
                key=f"fr_repl_{edit_key}",
                placeholder=r"$10^3\,\text{ft}^3$",
            )

        col_chk, col_cnt, col_go = st.columns([2, 1, 1])
        with col_chk:
            use_regex = st.checkbox(
                "Use regex",
                value=True,
                key=f"fr_re_{edit_key}",
            )
        with col_cnt:
            if st.button("Count", key=f"fr_cnt_{edit_key}",
                         use_container_width=True):
                content = st.session_state.get(edit_key, "")
                if find_val:
                    try:
                        if use_regex:
                            matches = re.findall(find_val, content)
                        else:
                            matches = content.split(find_val)[:-1]
                        st.info(f"Found: {len(matches)}")
                    except re.error as e:
                        st.error(f"Regex error: {e}")
        with col_go:
            if st.button("Replace all", key=f"fr_do_{edit_key}",
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
                        st.success(f"✅ Replaced: {count}")
                        st.rerun()
                    except re.error as e:
                        st.error(f"Regex error: {e}")


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

if view_mode == "👁 Preview":
    resolved = resolve_media_urls(st.session_state.get(_edit_key, md_content), selected_doc_id)
    render_preview(resolved)

elif view_mode == "✏️ Editor":
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
        st.caption("👁 Preview")
        with st.container(height=800, border=False):
            resolved = resolve_media_urls(st.session_state.get(_edit_key, md_content), selected_doc_id)
            render_preview(resolved)
    with col_right:
        st.caption("✏️ Editor")
        new_content = render_editor(st.session_state[_edit_key], _edit_key)
        if new_content != st.session_state[_edit_key]:
            st.session_state[_edit_key] = new_content
            st.session_state[_dirty_key] = True
