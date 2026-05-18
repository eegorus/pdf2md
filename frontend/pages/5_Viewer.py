import re
import io
import os

import httpx
from PIL import Image
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
    page_title="pdf2md",
    page_icon="📄",
    layout="wide",
)

from utils.auth import ensure_authenticated
if not ensure_authenticated():
    st.stop()

from utils.styles import inject_global_styles
inject_global_styles()

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

# ─── Split page state ─────────────────────────────────────────────────────────

_prev_doc_key = "split_prev_doc_id"
if st.session_state.get(_prev_doc_key) != selected_doc_id:
    _old_doc = st.session_state.get(_prev_doc_key)
    if _old_doc:
        for _k in list(st.session_state.keys()):
            if _k.startswith(f"split_page_img_{_old_doc}_"):
                del st.session_state[_k]
    st.session_state["split_page"] = 1
    st.session_state[_prev_doc_key] = selected_doc_id

if "split_page" not in st.session_state:
    st.session_state["split_page"] = 1

# ─── Toolbar ──────────────────────────────────────────────────────────────────

col_mode, col_save, col_reset, col_dl, col_zip = st.columns([3, 1.2, 1.2, 1.2, 1.2])

with col_mode:
    view_mode = st.radio(
        "Mode",
        ["👁 Preview", "↕️ Split"],
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


# ─── Split helpers ────────────────────────────────────────────────────────────

def fetch_split_page_image(doc_id: str, page_num: int):
    cache_key = f"split_page_img_{doc_id}_{page_num}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    token = st.session_state.get("access_token", "")
    try:
        resp = httpx.get(
            f"{BACKEND_URL}/documents/{doc_id}/page-image/{page_num}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code == 200:
            img = Image.open(io.BytesIO(resp.content))
            st.session_state[cache_key] = img
            return img
    except Exception:
        pass
    return None


def extract_page_markdown(full_md: str, page_num: int) -> str:
    parts = re.split(
        r'(?:---\s*\n)?##\s*(?:Страница|Page)\s+(\d+)',
        full_md, flags=re.IGNORECASE,
    )
    pages: dict[int, str] = {}
    if len(parts) >= 3:
        for i in range(1, len(parts) - 1, 2):
            try:
                pages[int(parts[i])] = parts[i + 1].strip()
            except (ValueError, IndexError):
                continue
    if not pages:
        # Quick-mode docs have no page headers — show full document
        return full_md
    result = pages.get(page_num)
    if not result:
        return f"*(No content for page {page_num})*"
    return result


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


# ─── Content area ─────────────────────────────────────────────────────────────

if view_mode == "👁 Preview":
    resolved = resolve_media_urls(st.session_state.get(_edit_key, md_content), selected_doc_id)
    render_preview(resolved)

else:  # Split — PDF page left, Markdown page right
    _doc_info = next((d for d in ready_docs if d["doc_id"] == selected_doc_id), {})
    total_pages = int(_doc_info.get("page_count") or 1)

    col_prev, col_num, col_next = st.columns([1, 2, 1])

    with col_prev:
        prev_clicked = st.button(
            "← Prev",
            disabled=st.session_state["split_page"] <= 1,
            use_container_width=True,
        )
    with col_next:
        next_clicked = st.button(
            "Next →",
            disabled=st.session_state["split_page"] >= total_pages,
            use_container_width=True,
        )

    if prev_clicked:
        st.session_state["split_page"] = max(1, st.session_state["split_page"] - 1)
    if next_clicked:
        st.session_state["split_page"] = min(total_pages, st.session_state["split_page"] + 1)

    with col_num:
        st.number_input(
            "Page", min_value=1, max_value=total_pages,
            key="split_page",
            label_visibility="collapsed",
        )
        st.caption(f"of {total_pages}")

    current_page = st.session_state["split_page"]
    col_pdf, col_md = st.columns(2)

    with col_pdf:
        st.caption(f"PDF — page {current_page}")
        img = fetch_split_page_image(selected_doc_id, current_page)
        if img:
            st.image(img, use_container_width=True)
        else:
            st.warning("Could not load page image.")

    with col_md:
        st.caption(f"Markdown — page {current_page}")
        _live_content = st.session_state.get(_edit_key, md_content)
        page_md = extract_page_markdown(_live_content, current_page)
        render_preview(resolve_media_urls(page_md, selected_doc_id))
