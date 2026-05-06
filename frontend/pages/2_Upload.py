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

# ── Helpers ───────────────────────────────────────────────────────────────────
def api(method, path, **kw):
    try:
        headers = kw.pop("headers", {})
        token = st.session_state.get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = httpx.request(method, f"{BACKEND_URL}{path}", timeout=60, headers=headers, **kw)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API {path}: {e}")
        return None

# ── Session state defaults ────────────────────────────────────────────────────
for k, v in {
    "upload_doc_id":   None,
    "upload_doc_name": None,
    "upload_stage":    "upload",   # upload | mode | quick_running | quick_done | detail_ready
    "quick_parser":    "pymupdf",
    "quick_api_key":   "",
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Sidebar: document history ──────────────────────────────────────────────
with st.sidebar:
    if st.button("＋ Upload new PDF", type="primary", use_container_width=True):
        st.session_state.upload_doc_id   = None
        st.session_state.upload_doc_name = None
        st.session_state.upload_stage    = "upload"
        st.rerun()

    st.markdown("---")
    st.markdown("**Documents**")

    docs_resp = api("GET", "/documents/")
    docs = (docs_resp or {}).get("documents", [])

    if not docs:
        st.caption("No documents")
    else:
        status_icon = {
            "splitting":   "⏳",
            "split_done":  "✂️",
            "processing":  "🔄",
            "layout_done": "✅",
            "ocr_done":    "🎉",
            "error":       "❌",
        }
        status_to_stage = {
            "splitting":   "upload",
            "split_done":  "mode",
            "processing":  "detail_layout",
            "layout_done": "detail_layout",
            "error":       "mode",
        }

        for d in reversed(docs):
            icon  = status_icon.get(d.get("status", ""), "❓")
            fname = d.get("filename", d["doc_id"])
            short = fname.replace(".pdf", "").replace("_", " ")[:22]
            pages = f"{d['page_count']}pp" if d.get("page_count") else ""

            is_active = (d["doc_id"] == st.session_state.get("upload_doc_id"))
            btn_label = f"{icon} {short}"
            if pages:
                btn_label += f" · {pages}"

            col_doc, col_del = st.columns([5, 1])
            with col_doc:
                if st.button(
                    btn_label,
                    key=f"sb_{d['doc_id']}",
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                ):
                    st.session_state.upload_doc_id   = d["doc_id"]
                    st.session_state.upload_doc_name = fname
                    st.session_state.upload_stage    = status_to_stage.get(
                        d.get("status", ""), "mode"
                    )
                    st.rerun()
            with col_del:
                if st.button("🗑", key=f"del_{d['doc_id']}", use_container_width=True,
                             help=f"Delete {fname}"):
                    api("DELETE", f"/documents/{d['doc_id']}")
                    if d["doc_id"] == st.session_state.get("upload_doc_id"):
                        st.session_state.upload_doc_id   = None
                        st.session_state.upload_doc_name = None
                        st.session_state.upload_stage    = "upload"
                    st.rerun()

    if docs:
        st.markdown("---")
        if st.button("🗑 Delete all", use_container_width=True):
            for d in docs:
                api("DELETE", f"/documents/{d['doc_id']}")
            st.session_state.upload_doc_id   = None
            st.session_state.upload_doc_name = None
            st.session_state.upload_stage    = "upload"
            st.rerun()

st.title("📤 Upload PDF")

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 0 — file upload
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.upload_stage == "upload":
    st.caption("Select a PDF file")
    uploaded = st.file_uploader("", type=["pdf"], label_visibility="collapsed")

    if uploaded:
        if st.button("📎 Upload & Process", type="primary", use_container_width=True):
            with st.spinner("Uploading file..."):
                res = api("POST", "/documents/upload",
                          files={"file": (uploaded.name, uploaded.getvalue(), "application/pdf")})
            if not res:
                st.stop()

            doc_id = res["doc_id"]
            st.session_state.upload_doc_id   = doc_id
            st.session_state.upload_doc_name = uploaded.name
            st.success(f"✅ Uploaded: `{doc_id}`")

            with st.spinner("Splitting PDF into pages..."):
                for _ in range(120):
                    time.sleep(1)
                    r = api("GET", f"/processing/{doc_id}/status")
                    if r and r.get("status") == "split_done":
                        break
                else:
                    st.error("❌ PDF splitting timed out (120s) — check backend logs")
                    st.stop()

            st.session_state.upload_stage = "mode"
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — mode selection
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.upload_stage == "mode":
    doc_id   = st.session_state.upload_doc_id
    doc_name = st.session_state.upload_doc_name

    st.success(f"✅ Uploaded: `{doc_id}` — **{doc_name}**")
    st.markdown("---")
    st.markdown("### Select processing mode")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            "<div style='padding:20px;background:#1e293b;border-radius:12px;"
            "border:2px solid #3b82f6;text-align:center'>"
            "<div style='font-size:2em'>⚡</div>"
            "<b style='font-size:1.1em'>Quick mode</b><br/><br/>"
            "The entire PDF is sent to the parser at once.<br/>"
            "Result — Markdown in 1–5 minutes.<br/><br/>"
            "<i style='color:#94a3b8'>Good for: standard reports<br/>"
            "where layout doesn't matter</i>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown("")
        if st.button("⚡ Quick mode", use_container_width=True, type="primary"):
            st.session_state.upload_stage = "quick_setup"
            st.rerun()

    with col2:
        st.markdown(
            "<div style='padding:20px;background:#1e293b;border-radius:12px;"
            "border:2px solid #22c55e;text-align:center'>"
            "<div style='font-size:2em'>🔬</div>"
            "<b style='font-size:1.1em'>Detailed mode</b><br/><br/>"
            "Page-by-page block layout with manual<br/>"
            "review and OCR selection per block type.<br/><br/>"
            "<i style='color:#94a3b8'>Good for: pdf2md reports,<br/>"
            "complex tables and formulas</i>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown("")
        if st.button("🔬 Detailed mode", use_container_width=True):
            with st.spinner("Starting layout detection..."):
                try:
                    _token = st.session_state.get("access_token")
                    resp = httpx.post(
                        f"{BACKEND_URL}/processing/{doc_id}/start",
                        headers={"Authorization": f"Bearer {_token}"} if _token else {},
                        timeout=10,
                    )
                    if resp.status_code == 400 and "layout_already_done" in resp.text:
                        st.session_state["viewer_doc_id"] = doc_id
                        st.switch_page("pages/1_My_Documents.py")
                    elif resp.status_code == 200:
                        st.session_state.upload_stage = "detail_layout"
                        st.rerun()
                    else:
                        st.error(f"Error: {resp.text}")
                except Exception as _e:
                    st.error(str(_e))

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2a — quick mode: parser setup
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.upload_stage == "quick_setup":
    doc_id = st.session_state.upload_doc_id
    st.success(f"✅ `{doc_id}` — {st.session_state.upload_doc_name}")
    st.markdown("---")
    st.markdown("### ⚡ Quick mode — select parser")

    parsers_data = api("GET", "/quick/parsers") or []

    local_parsers = [p for p in parsers_data if not p["needs_api_key"]]
    cloud_parsers = [p for p in parsers_data if p["needs_api_key"]]

    st.markdown("#### 🖥 Local parsers")
    for p in local_parsers:
        avail  = p["available"]
        badge  = "✅" if avail else "❌ not installed"
        color  = "#22c55e" if avail else "#475569"
        border = "2px solid #22c55e" if (avail and st.session_state.quick_parser == p["name"]) else f"1px solid {color}"

        col_a, col_b = st.columns([3, 1])
        with col_a:
            st.markdown(
                f"<div style='padding:12px;background:#1e293b;border-radius:8px;border:{border}'>"
                f"<b>{p['label']}</b> {badge}<br/>"
                f"<small style='color:#94a3b8'>{p['description']}</small>"
                f"</div>",
                unsafe_allow_html=True,
            )
        with col_b:
            if avail:
                if st.button("Select", key=f"sel_{p['name']}", use_container_width=True):
                    st.session_state.quick_parser = p["name"]
                    st.session_state.quick_api_key = ""
                    st.rerun()

    keys_list = api("GET", "/users/me/api-keys") or []
    settings_keys = {k["provider"]: k for k in keys_list}
    pid_map = {
        "gpt4o":      "openai",
        "claude":     "anthropic",
        "llamaparse": "llamaparse",
        "openrouter": "openrouter",
    }

    st.markdown("#### ☁️ Cloud parsers")
    for p in cloud_parsers:
        avail = p["available"]
        if not avail:
            st.caption(f"❌ {p['label']} — package not installed")
            continue

        provider_id  = pid_map.get(p["name"])
        key_in_settings = bool(
            provider_id and settings_keys.get(provider_id, {}).get("is_set")
        )

        with st.expander(f"{p['label']} — {p['description']}"):
            if key_in_settings:
                st.success("✅ API key configured in Settings")
                key_input = ""
            else:
                st.warning("⚠️ Key not set — enter here or add in Settings")
                key_input = st.text_input(
                    "API Key", type="password",
                    key=f"key_{p['name']}",
                    placeholder="sk-..." if p["name"] == "gpt4o" else "...",
                )

            if st.button("Select", key=f"sel_{p['name']}", use_container_width=True):
                if not key_in_settings and not key_input:
                    st.error("Enter API key or add it in Settings")
                else:
                    st.session_state.quick_parser  = p["name"]
                    st.session_state.quick_api_key = key_input
                    st.rerun()

    st.markdown("---")
    chosen = st.session_state.quick_parser
    st.info(f"Selected parser: **{chosen}**")

    col_back, col_run = st.columns(2)
    with col_back:
        if st.button("← Back", use_container_width=True):
            st.session_state.upload_stage = "mode"
            st.rerun()
    with col_run:
        if st.button("🚀 Run", type="primary", use_container_width=True):
            res = api("POST", f"/quick/{doc_id}/run",
                      json={"parser": chosen,
                            "api_key": st.session_state.quick_api_key})
            if res:
                st.session_state.upload_stage = "quick_running"
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2b — quick mode: waiting for result
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.upload_stage == "quick_running":
    doc_id = st.session_state.upload_doc_id
    st.info(f"⏳ Parser **{st.session_state.quick_parser}** is processing the document...")

    status = api("GET", f"/quick/{doc_id}/status") or {}
    state  = status.get("status")

    if state == "done":
        st.session_state.upload_stage = "quick_done"
        st.rerun()
    elif state == "error":
        st.error(f"❌ Error: {status.get('error')}")
        if st.button("← Back to parser selection"):
            st.session_state.upload_stage = "quick_setup"
            st.rerun()
    else:
        st.caption("Page refreshes automatically every 5 sec...")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Refresh status", use_container_width=True):
                st.rerun()
        with col2:
            if st.checkbox("Auto-refresh", value=True):
                time.sleep(5)
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 3a — quick mode: result ready
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.upload_stage == "quick_done":
    doc_id = st.session_state.upload_doc_id
    status = api("GET", f"/quick/{doc_id}/status") or {}
    md     = status.get("markdown", "")

    st.success(f"✅ Done! Parser: **{st.session_state.quick_parser}**")
    st.markdown("---")

    col_md, col_new = st.columns(2)
    with col_md:
        if st.button("📄 Open in Markdown Viewer", type="primary", use_container_width=True):
            st.session_state["md_viewer_doc_id"] = doc_id
            st.switch_page("pages/5_Viewer.py")
    with col_new:
        if st.button("📄 New document", use_container_width=True):
            for k in ["upload_doc_id", "upload_doc_name"]:
                st.session_state[k] = None
            st.session_state.upload_stage = "upload"
            st.rerun()

    st.markdown("---")
    col_dl, col_copy = st.columns(2)
    with col_dl:
        st.download_button(
            "⬇️ Download Markdown",
            data=md.encode("utf-8"),
            file_name=f"{st.session_state.upload_doc_name or doc_id}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    with col_copy:
        st.button("📋 Copy", use_container_width=True,
                  on_click=lambda: st.write(
                      f"<script>navigator.clipboard.writeText(`{md[:100]}`)</script>",
                      unsafe_allow_html=True))

    st.markdown("### Result")
    tab_preview, tab_raw = st.tabs(["👁 Preview", "📝 Raw Markdown"])
    with tab_preview:
        st.markdown(md)
    with tab_raw:
        st.code(md, language="markdown")

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 3b — detailed mode: layout detection running
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.upload_stage == "detail_layout":
    doc_id = st.session_state.upload_doc_id
    st.info("🔬 Detailed mode — Layout Detection started")

    progress_bar = st.progress(0, text="Detecting blocks...")
    for _ in range(120):
        time.sleep(5)
        s = api("GET", f"/processing/{doc_id}/status") or {}
        status = s.get("status")
        if status == "layout_done":
            progress_bar.progress(1.0, text=f"✅ Blocks found: {s.get('total_blocks', 0)}")
            st.success("✅ Block layout ready! Open Viewer to review.")
            if st.button("→ Open Viewer", type="primary", use_container_width=True):
                st.session_state["viewer_doc_id"] = doc_id
                st.switch_page("pages/1_My_Documents.py")
            break
        elif status == "error":
            st.error(f"❌ Layout detection error: {s.get('error', '')}")
            break
        else:
            pct = min(0.9, (_ + 1) / 120)
            progress_bar.progress(pct, text=f"Status: {status}...")
    else:
        st.warning("⚠️ Layout detection is taking longer than expected — check Viewer later")
