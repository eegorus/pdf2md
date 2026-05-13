import streamlit as st
import httpx
import os
import time

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="pdf2md", page_icon="📄", layout="wide")

from utils.auth import ensure_authenticated
if not ensure_authenticated():
    st.stop()

from utils.styles import inject_global_styles
inject_global_styles()

from components.auth_guard import require_auth, render_sidebar_user
current_user = require_auth()
render_sidebar_user()


def api(method, path, **kw):
    try:
        headers = kw.pop("headers", {})
        token = st.session_state.get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return httpx.request(method, f"{BACKEND_URL}{path}", timeout=60, headers=headers, **kw)
    except Exception:
        return None


_PARSER_META = {
    "pymupdf":      {"speed": 5, "quality": 2, "name": "PyMuPDF",
                     "desc": "Instant text extraction. Works offline, no GPU."},
    "marker":       {"speed": 2, "quality": 4, "name": "Marker",
                     "desc": "Best local quality for text, formulas, and structure."},
    "docling":      {"speed": 2, "quality": 4, "name": "Docling",
                     "desc": "Great for tables and complex page layouts."},
    "unstructured": {"speed": 3, "quality": 3, "name": "Unstructured",
                     "desc": "Good for text and simple tables without GPU."},
    "llamaparse":   {"speed": 3, "quality": 5, "name": "LlamaParse",
                     "desc": "High-quality cloud parser from LlamaIndex."},
    "gpt4o":        {"speed": 2, "quality": 5, "name": "GPT-4o",
                     "desc": "Page-by-page Vision parsing. Top quality."},
    "claude":       {"speed": 2, "quality": 5, "name": "Claude",
                     "desc": "Claude Vision. Excellent for complex tables."},
    "openrouter":   {"speed": 2, "quality": 3, "name": "OpenRouter",
                     "desc": "Any vision model via OpenRouter API."},
}

_PID_MAP = {
    "gpt4o":      "openai",
    "claude":     "anthropic",
    "llamaparse": "llamaparse",
    "openrouter": "openrouter",
}

_ERROR_MSGS = {
    "key":     "API key required. Add it in Settings → API Keys.",
    "install": "This parser is not installed. Try a different one.",
    "timeout": "Parsing took too long. Try a faster parser or a smaller file.",
}


def _dots(n, total=5):
    return "●" * n + "○" * (total - n)


def _human_error(raw: str) -> str:
    low = raw.lower()
    if any(w in low for w in ("api key", "key", "требует", "ключ")):
        return _ERROR_MSGS["key"]
    if any(w in low for w in ("не установлен", "not installed")):
        return _ERROR_MSGS["install"]
    if any(w in low for w in ("timeout", "timed out")):
        return _ERROR_MSGS["timeout"]
    return raw


for k, v in {
    "upload_step":    1,
    "upload_doc_id":  None,
    "upload_doc_name": None,
    "upload_mode":    None,
    "upload_parser":  None,
    "upload_parsing": False,
    "upload_error":   None,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# If coming from My Documents "Re-parse": skip step 1
if st.session_state.get("reparse_docid") and st.session_state.upload_step == 1:
    _rid = st.session_state.pop("reparse_docid")
    r = api("GET", "/documents/")
    if r and r.status_code == 200:
        _doc = next((d for d in r.json().get("documents", []) if d["doc_id"] == _rid), None)
        if _doc:
            st.session_state.upload_doc_id   = _rid
            st.session_state.upload_doc_name = _doc.get("filename", _rid)
            st.session_state.upload_step     = 2
            st.rerun()

st.markdown("""
<style>
/* File uploader drop zone border */
[data-testid="stFileUploaderDropzone"] {
    border: 2px dashed #475569 !important;
    border-radius: 12px !important;
    transition: border-color 0.2s;
}
[data-testid="stFileUploaderDropzone"]:hover {
    border-color: #7C3AED !important;
}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — File selection
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.upload_step == 1:
    st.markdown("## Upload PDF")

    uploaded = st.file_uploader("Drop your PDF here or click to select", type=["pdf"])

    if uploaded:
        size_mb = len(uploaded.getvalue()) / (1024 * 1024)
        st.markdown(f"**{uploaded.name}** &nbsp;·&nbsp; {size_mb:.1f} MB",
                    unsafe_allow_html=True)

        if st.button("Continue →", type="primary"):
            with st.spinner("Uploading…"):
                r = api("POST", "/documents/upload",
                        files={"file": (uploaded.name, uploaded.getvalue(), "application/pdf")})
            if not r or r.status_code != 200:
                st.error("Upload failed. Please try again.")
                st.stop()

            doc_id = r.json()["doc_id"]
            st.session_state.upload_doc_id   = doc_id
            st.session_state.upload_doc_name = uploaded.name

            with st.spinner("Splitting PDF into pages…"):
                for _ in range(120):
                    time.sleep(1)
                    s = api("GET", f"/processing/{doc_id}/status")
                    if s and s.status_code == 200 and s.json().get("status") == "split_done":
                        break
                else:
                    st.error("PDF splitting timed out. Check backend logs.")
                    st.stop()

            st.session_state.upload_step = 2
            st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Mode selection
# ─────────────────────────────────────────────────────────────────────────────
elif st.session_state.upload_step == 2:
    doc_name = st.session_state.upload_doc_name or ""
    st.markdown("## Select parsing mode")
    if doc_name:
        st.caption(doc_name)

    _CARD_STYLE = (
        "padding:28px 20px;background:#1e293b;border-radius:12px;"
        "border:{border};text-align:center;margin-bottom:12px"
    )

    col1, col2 = st.columns(2)

    with col1:
        sel = st.session_state.upload_mode == "quick"
        st.markdown(
            f"<div style='{_CARD_STYLE.format(border='2px solid #7C3AED' if sel else '2px solid #334155')}'>"
            "<div style='font-size:1.8em;margin-bottom:10px'>⚡</div>"
            "<b style='font-size:1.1em'>Quick</b><br><br>"
            "<span style='color:#94a3b8'>Fast one-click parsing for simple reports.</span>"
            "</div>",
            unsafe_allow_html=True,
        )
        if st.button("Select Quick", use_container_width=True, key="btn_quick",
                     type="primary" if sel else "secondary"):
            st.session_state.upload_mode    = "quick"
            st.session_state.upload_step    = 3
            st.session_state.upload_error   = None
            st.session_state.upload_parsing = False
            st.rerun()

    with col2:
        sel = st.session_state.upload_mode == "detailed"
        st.markdown(
            f"<div style='{_CARD_STYLE.format(border='2px solid #7C3AED' if sel else '2px solid #334155')}'>"
            "<div style='font-size:1.8em;margin-bottom:10px'>🔬</div>"
            "<b style='font-size:1.1em'>Detailed</b><br><br>"
            "<span style='color:#94a3b8'>Careful page-by-page parsing for complex reports "
            "with tables and figures.</span>"
            "</div>",
            unsafe_allow_html=True,
        )
        if st.button("Select Detailed", use_container_width=True, key="btn_detail",
                     type="primary" if sel else "secondary"):
            st.session_state.upload_mode    = "detailed"
            st.session_state.upload_step    = 3
            st.session_state.upload_error   = None
            st.session_state.upload_parsing = False
            st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Parser selection + launch
# ─────────────────────────────────────────────────────────────────────────────
elif st.session_state.upload_step == 3:
    doc_id = st.session_state.upload_doc_id
    mode   = st.session_state.upload_mode

    st.caption(
        f"{st.session_state.upload_doc_name} · {'Quick' if mode == 'quick' else 'Detailed'}"
    )

    if not st.session_state.upload_parsing:
        if st.button("← Back"):
            st.session_state.upload_step    = 2
            st.session_state.upload_parser  = None
            st.session_state.upload_error   = None
            st.session_state.upload_parsing = False
            st.rerun()

    # ── QUICK mode ────────────────────────────────────────────────────────────
    if mode == "quick":

        # Polling state
        if st.session_state.upload_parsing:
            with st.spinner(f"Parsing with {st.session_state.upload_parser}…"):
                time.sleep(3)
                r = api("GET", f"/quick/{doc_id}/status")
            if r and r.status_code == 200:
                job = r.json()
                if job.get("status") == "done":
                    st.session_state.upload_parsing = False
                    st.session_state["viewer_doc_id"] = doc_id
                    st.toast(f"Parsed successfully with {st.session_state.upload_parser} ✓")
                    st.switch_page("pages/5_Viewer.py")
                elif job.get("status") == "error":
                    st.session_state.upload_parsing = False
                    st.session_state.upload_error   = _human_error(job.get("error", "Unknown error"))
                    st.rerun()
                else:
                    st.rerun()
            else:
                st.rerun()

        # Error state
        elif st.session_state.upload_error:
            st.error(st.session_state.upload_error)
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("Try again", use_container_width=True):
                    st.session_state.upload_error   = None
                    st.session_state.upload_parsing = False
                    st.rerun()
            with col_b:
                if st.button("Choose another parser", use_container_width=True):
                    st.session_state.upload_parser  = None
                    st.session_state.upload_error   = None
                    st.rerun()

        # Parser selection
        else:
            st.markdown("### Choose a parser")

            parsers_data: list = []
            r = api("GET", "/quick/parsers")
            if r and r.status_code == 200:
                parsers_data = r.json()

            keys_r    = api("GET", "/users/me/api-keys")
            keys_list = keys_r.json() if keys_r and keys_r.status_code == 200 else []
            keys_dict = {k["provider"]: k for k in keys_list}

            free_parsers  = [p for p in parsers_data if not p["needs_api_key"]]
            cloud_parsers = [p for p in parsers_data if p["needs_api_key"]]

            def _parser_card(p, enabled: bool = True):
                meta     = _PARSER_META.get(p["name"],
                               {"speed": 3, "quality": 3,
                                "name": p["name"], "desc": p.get("description", "")})
                selected = st.session_state.upload_parser == p["name"]
                border   = ("2px solid #7C3AED" if selected
                            else ("2px solid #334155" if enabled else "1px solid #2d3748"))
                opacity  = "1" if enabled else "0.45"
                no_key   = ("<br><small style='color:#f59e0b'>Add key in Settings</small>"
                            if not enabled else "")
                btn_lbl  = "Selected ✓" if selected else "Select"

                st.markdown(
                    f"<div style='padding:14px 16px;background:#1e293b;border-radius:10px;"
                    f"border:{border};opacity:{opacity};margin-bottom:2px'>"
                    f"<b>{meta['name']}</b><br>"
                    f"<span style='color:#94a3b8;font-size:0.85em'>{meta['desc']}</span><br><br>"
                    f"<span style='font-size:0.83em;color:#94a3b8'>"
                    f"Speed <b style='color:#e2e8f0'>{_dots(meta['speed'])}</b>"
                    f"&nbsp;&nbsp;"
                    f"Quality <b style='color:#e2e8f0'>{_dots(meta['quality'])}</b>"
                    f"</span>{no_key}</div>",
                    unsafe_allow_html=True,
                )
                if st.button(btn_lbl, key=f"sel_{p['name']}",
                             use_container_width=True, disabled=not enabled):
                    st.session_state.upload_parser = p["name"]
                    st.rerun()

            st.markdown("**Free parsers**")
            cols = st.columns(2)
            for i, p in enumerate(free_parsers):
                with cols[i % 2]:
                    _parser_card(p, enabled=True)

            if cloud_parsers:
                st.markdown("**Cloud parsers**")
                cols2 = st.columns(2)
                for i, p in enumerate(cloud_parsers):
                    provider = _PID_MAP.get(p["name"])
                    has_key  = bool(provider and keys_dict.get(provider, {}).get("is_set"))
                    with cols2[i % 2]:
                        _parser_card(p, enabled=has_key)

            if st.session_state.upload_parser:
                st.markdown("---")
                if st.button("Parse PDF", type="primary", use_container_width=True):
                    r2 = api("POST", f"/quick/{doc_id}/run",
                             json={"parser": st.session_state.upload_parser, "api_key": ""})
                    if r2 and r2.status_code == 200:
                        st.session_state.upload_parsing = True
                        st.rerun()
                    else:
                        raw = (r2.json().get("detail", "Failed to start parsing")
                               if r2 else "Network error. Please try again.")
                        st.session_state.upload_error = _human_error(raw)
                        st.rerun()

    # ── DETAILED mode ─────────────────────────────────────────────────────────
    else:
        st.markdown("### Detailed parsing")
        st.markdown(
            "<p style='color:#94a3b8'>Page-by-page layout detection with per-block OCR "
            "review in the viewer.</p>",
            unsafe_allow_html=True,
        )

        if st.session_state.upload_parsing:
            with st.spinner("Detecting layout…"):
                time.sleep(3)
                r = api("GET", f"/processing/{doc_id}/status")
            if r and r.status_code == 200:
                s = r.json()
                if s.get("status") == "layout_done":
                    st.session_state.upload_parsing = False
                    st.session_state["viewer_doc_id"] = doc_id
                    blocks = s.get("total_blocks", 0)
                    st.toast(f"Layout detected — {blocks} blocks found ✓")
                    st.switch_page("pages/1_My_Documents.py")
                elif s.get("status") == "error":
                    st.session_state.upload_parsing = False
                    st.session_state.upload_error   = _human_error(
                        s.get("error", "Layout detection failed."))
                    st.rerun()
                else:
                    st.rerun()
            else:
                st.rerun()

        elif st.session_state.upload_error:
            st.error(st.session_state.upload_error)
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("Try again", use_container_width=True):
                    st.session_state.upload_error   = None
                    st.session_state.upload_parsing = False
                    st.rerun()
            with col_b:
                if st.button("Choose another parser", use_container_width=True):
                    st.session_state.upload_step    = 2
                    st.session_state.upload_error   = None
                    st.session_state.upload_parsing = False
                    st.rerun()

        else:
            if st.button("Parse PDF", type="primary", use_container_width=True):
                r2 = api("POST", f"/processing/{doc_id}/start")
                if r2 and r2.status_code == 400 and "layout_already_done" in r2.text:
                    st.session_state["viewer_doc_id"] = doc_id
                    st.toast("Layout already done — opening editor ✓")
                    st.switch_page("pages/1_My_Documents.py")
                elif r2 and r2.status_code == 200:
                    st.session_state.upload_parsing = True
                    st.rerun()
                else:
                    raw = (r2.json().get("detail", "Failed to start layout detection")
                           if r2 else "Network error. Please try again.")
                    st.session_state.upload_error = _human_error(raw)
                    st.rerun()
