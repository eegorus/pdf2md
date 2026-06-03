import streamlit as st
import streamlit.components.v1 as components
import httpx
import os
import time
from datetime import datetime, timezone
from PIL import Image, ImageDraw
from streamlit_image_coordinates import streamlit_image_coordinates
from streamlit_drawable_canvas import st_canvas
from streamlit_sortables import sort_items
import io

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


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
    initial_sidebar_state="expanded",
)

from utils.auth import ensure_authenticated
if not ensure_authenticated():
    st.stop()

from utils.styles import inject_global_styles, inject_editor_layout
inject_global_styles()
st.markdown("""
<style>
/* Canvas column — hide horizontal overflow so zoomed image doesn't shift layout */
.main .block-container { overflow-x: hidden; }
/* Center the canvas iframe within its column */
[data-testid="stIframe"] { display: block; margin: 0 auto; }
</style>
""", unsafe_allow_html=True)

from components.auth_guard import require_auth, render_sidebar_user
current_user = require_auth()
render_sidebar_user()

BLOCK_COLORS = {
    "text":    (59,  130, 246),
    "table":   (234, 88,  12),
    "table_simple":  (234, 179, 8),
    "table_complex": (234, 88,  12),
    "formula": (22,  163, 74),
    "figure":  (147, 51,  234),
}
TYPE_HEX = {
    "text": "#3b82f6", "table": "#ea580c", "table_simple": "#eab308", "table_complex": "#ea580c",
    "formula": "#16a34a", "figure": "#9333ea",
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def fetch_documents(token: str = ""):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = httpx.get(f"{BACKEND_URL}/documents/", headers=headers, timeout=5)
        return r.json().get("documents", [])
    except Exception:
        return []

@st.cache_data(ttl=10)
def fetch_blocks(doc_id: str, token: str = ""):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = httpx.get(f"{BACKEND_URL}/processing/{doc_id}/results", headers=headers, timeout=10)
        return r.json().get("blocks", [])
    except Exception:
        return []

@st.cache_data(ttl=5)
def fetch_block_preview(doc_id: str, block_id: str, token: str = "", _bust: int = 0) -> bytes | None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = httpx.get(
            f"{BACKEND_URL}/documents/{doc_id}/block-image/{block_id}",
            headers=headers,
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


@st.cache_data(ttl=60)
def fetch_available_models(token: str = "") -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = httpx.get(f"{BACKEND_URL}/settings/available-models", headers=headers, timeout=5)
        return r.json()
    except Exception:
        return {}


@st.cache_data(ttl=60)
def fetch_page_image(doc_id: str, page_num: int, token: str = "", max_w: int = 740, max_h: int = 750):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = httpx.get(
            f"{BACKEND_URL}/documents/{doc_id}/page-image/{page_num}", headers=headers, timeout=15
        )
        if r.status_code == 200:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            orig_w, orig_h = img.size
            scale = min(1.0, max_w / orig_w, max_h / orig_h)
            if scale < 1.0:
                img = img.resize((int(orig_w * scale), int(orig_h * scale)), Image.LANCZOS)
            return img, orig_w, orig_h, scale
    except Exception:
        pass
    return None, 0, 0, 1.0



def _relative_time(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        s = (datetime.now(timezone.utc) - dt).total_seconds()
        if s < 60:       return "just now"
        if s < 3600:     return f"{int(s/60)}m ago"
        if s < 86400:    return f"{int(s/3600)}h ago"
        if s < 172800:   return "Yesterday"
        if s < 2592000:  return f"{int(s/86400)}d ago"
        return dt.strftime("%b %d, %Y")
    except Exception:
        return "—"


def _status_badge(status: str | None) -> str:
    return {
        "done":       "✅ Done",
        "processing": "⏳ Processing",
        "splitting":  "⏳ Splitting",
        "error":      "❌ Error",
        "failed":     "❌ Failed",
        "pending":    "🕐 Pending",
        "uploaded":   "🕐 Uploaded",
        "layout_done": "🗂 Layout done",
        "ocr_done":   "✅ OCR done",
    }.get(status or "", f"⚪ {status or 'unknown'}")


def draw_blocks_on_image(
    image: Image.Image,
    blocks: list,
    page_num: int,
    selected_id: str | None,
    scale: float = 1.0,
    show_order: bool = True,
) -> Image.Image:
    import math

    img = image.copy()
    draw = ImageDraw.Draw(img, "RGBA")

    page_blocks = [b for b in blocks if b.get("page_num") == page_num]

    ordered = sorted(
        [b for b in page_blocks if not b.get("ignore", False)],
        key=lambda b: b.get("sort_order", b.get("blockidx", 0)),
    )
    ignored_blocks = [b for b in page_blocks if b.get("ignore", False)]

    # Reading order arrows (drawn under block rectangles)
    if show_order and len(ordered) > 1:
        for i in range(len(ordered) - 1):
            b1, b2 = ordered[i], ordered[i + 1]
            bbox1 = b1.get("bbox", [0, 0, 0, 0])
            bbox2 = b2.get("bbox", [0, 0, 0, 0])
            cx1 = int((bbox1[0] + bbox1[2]) / 2 * scale)
            cy1 = int((bbox1[1] + bbox1[3]) / 2 * scale)
            cx2 = int((bbox2[0] + bbox2[2]) / 2 * scale)
            cy2 = int((bbox2[1] + bbox2[3]) / 2 * scale)
            draw.line([(cx1, cy1), (cx2, cy2)], fill=(30, 120, 255, 100), width=2)
            angle = math.atan2(cy2 - cy1, cx2 - cx1)
            alen, aang = 14, 0.4
            ax1 = cx2 - alen * math.cos(angle - aang)
            ay1 = cy2 - alen * math.sin(angle - aang)
            ax2 = cx2 - alen * math.cos(angle + aang)
            ay2 = cy2 - alen * math.sin(angle + aang)
            draw.polygon(
                [(cx2, cy2), (int(ax1), int(ay1)), (int(ax2), int(ay2))],
                fill=(30, 120, 255, 160),
            )

    # Ignored blocks — grey semi-transparent
    for b in ignored_blocks:
        bbox = b.get("bbox", [])
        if len(bbox) != 4:
            continue
        x1, y1, x2, y2 = (int(v * scale) for v in bbox)
        bid = b.get("block_id")
        outline = (255, 220, 0, 255) if bid == selected_id else (128, 128, 128, 120)
        lw = 4 if bid == selected_id else 1
        draw.rectangle([x1, y1, x2, y2], fill=(128, 128, 128, 30), outline=outline, width=lw)
        draw.rectangle([x1, y1, x1 + 24, y1 + 16], fill=(128, 128, 128, 180))
        draw.text((x1 + 2, y1 + 1), "IGN", fill=(255, 255, 255))

    # Active blocks — coloured with sort_order badge
    for idx, b in enumerate(ordered):
        bbox = b.get("bbox", [])
        if len(bbox) != 4:
            continue
        btype = b.get("block_type", "text")
        x1, y1, x2, y2 = (int(v * scale) for v in bbox)
        rgb = BLOCK_COLORS.get(btype, (128, 128, 128))
        bid = b.get("block_id")
        status = b.get("status", "")
        if bid == selected_id:
            outline, lw = (255, 220, 0, 255), 4
        elif status == "needs_review":
            outline, lw = (239, 68, 68, 255), 3
        elif status == "accepted":
            outline, lw = (34, 197, 94, 255), 2
        else:
            outline, lw = (*rgb, 200), 2
        draw.rectangle([x1, y1, x2, y2], fill=(*rgb, 30), outline=outline, width=lw)
        label = str(b.get("sort_order", idx))
        badge_w = max(18, len(label) * 7 + 4)
        draw.rectangle([x1, y1, x1 + badge_w, y1 + 16], fill=(*rgb, 220))
        draw.text((x1 + 2, y1 + 1), label, fill=(255, 255, 255))

    return img


# ─── SESSION STATE ─────────────────────────────────────────────────────────────
_defaults = {
    "viewer_page": 1,
    "viewer_selected_block": None,
    "viewer_edit_mode": False,
    "viewer_confirm_delete": False,
    "viewer_model_dialog_open": False,
    "viewer_model_choices": {},
    "viewer_draw_mode": False,
    "viewer_draw_type": "text",
    "viewer_mode": None,
    "viewer_canvas_version": 0,
    "viewer_orig_w": 0,
    "viewer_orig_h": 0,
    "undo_stack": [],
    "canvas_last_coord": None,
    "canvas_zoom": 1.0,
    "viewer_show_order": True,
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─── HEADER ───────────────────────────────────────────────────────────────────
st.title("🔍 My Documents")

doc_id = st.session_state.get("viewer_doc_id") or st.session_state.get("viewer_doc_id")
if not doc_id:
    # ── Search + sort ──────────────────────────────────────────────────────────
    _cs, _cs2, _cu = st.columns([4, 2, 1])
    _search = _cs.text_input(
        "search", label_visibility="collapsed",
        placeholder="🔍 Search by filename…", key="doc_search",
    )
    _sort = _cs2.selectbox(
        "sort",
        ["Date ↓ (newest first)", "Date ↑ (oldest first)", "Name A→Z"],
        label_visibility="collapsed", key="doc_sort",
    )
    if _cu.button("⬆ Upload", type="primary", use_container_width=True, key="picker_upload"):
        st.switch_page("pages/2_Upload.py")

    _token = st.session_state.get("access_token", "")
    _all_docs = fetch_documents(_token)

    _docs = _all_docs
    if _search:
        _q = _search.lower()
        _docs = [d for d in _docs if _q in (d.get("filename") or "").lower()]
    if _sort == "Date ↑ (oldest first)":
        _docs = sorted(_docs, key=lambda d: d.get("created_at") or "")
    elif _sort == "Name A→Z":
        _docs = sorted(_docs, key=lambda d: (d.get("filename") or "").lower())

    # ── Empty state ────────────────────────────────────────────────────────────
    if not _docs:
        st.info("No documents yet. Upload your first PDF →")
        if st.button("Go to Upload", key="picker_go_upload"):
            st.switch_page("pages/2_Upload.py")
        st.stop()

    # ── Pending download ───────────────────────────────────────────────────────
    _dl_req = st.session_state.pop("_picker_dl_req", None)
    if _dl_req:
        with st.spinner("Preparing download…"):
            api("POST", f"/processing/{_dl_req}/export?format=markdown", timeout=60)
            _dr = api("GET", f"/processing/{_dl_req}/export-file/markdown")
        if _dr and _dr.status_code == 200:
            _dl_doc = next((d for d in _all_docs if d.get("doc_id") == _dl_req), {})
            _md_name = _dl_doc.get("filename", _dl_req).rsplit(".", 1)[0] + ".md"
            st.session_state["_picker_dl_ready"] = (_dr.content, _md_name)
        else:
            st.warning("Markdown not available yet — run OCR first.")

    if "_picker_dl_ready" in st.session_state:
        _db, _dn = st.session_state["_picker_dl_ready"]
        _dc1, _dc2 = st.columns([8, 1])
        _dc1.download_button(f"⬇ {_dn}", data=_db, file_name=_dn, mime="text/markdown", key="picker_dl_save")
        if _dc2.button("✕", key="picker_dl_dismiss"):
            del st.session_state["_picker_dl_ready"]
            st.rerun()

    # ── Delete confirm ─────────────────────────────────────────────────────────
    _confirm_id = st.session_state.get("_picker_confirm_delete")
    if _confirm_id:
        _cdoc = next((d for d in _all_docs if d.get("doc_id") == _confirm_id), None)
        _clabel = _cdoc.get("filename", _confirm_id) if _cdoc else _confirm_id
        st.warning(f"Delete **{_clabel}**? This cannot be undone.")
        _yc, _nc, _ = st.columns([1, 1, 6])
        if _yc.button("Yes, delete", type="primary", key="picker_del_yes"):
            _r = api("DELETE", f"/documents/{_confirm_id}")
            if _r and _r.status_code in (200, 204):
                st.session_state.pop("_picker_confirm_delete", None)
                fetch_documents.clear()
                st.rerun()
            else:
                st.error("Delete failed")
        if _nc.button("Cancel", key="picker_del_no"):
            st.session_state.pop("_picker_confirm_delete", None)
            st.rerun()

    # ── List ───────────────────────────────────────────────────────────────────
    st.caption(f"{len(_docs)} document{'s' if len(_docs) != 1 else ''}")
    st.divider()

    _hdr = st.columns([4, 2, 2, 2, 1, 1])
    for _h, _l in zip(_hdr, ["Filename", "Uploaded", "Parser", "Status", "", ""]):
        _h.caption(_l)

    for doc in _docs:
        did     = doc.get("doc_id", "")
        name    = doc.get("filename", "unknown.pdf")
        status  = doc.get("status", "")
        parser  = doc.get("parser") or "OCR pipeline"
        created = doc.get("created_at")
        can_open = status in ("done", "layout_done", "ocr_done")

        _cn, _ct, _cp, _css, _co, _cm = st.columns([4, 2, 2, 2, 1, 1])
        _cn.markdown(f"📄 **{name}**")
        _ct.write(_relative_time(created))
        _cp.write(parser)
        _css.write(_status_badge(status))

        if _co.button(
            "Open ▶", key=f"open_{did}", use_container_width=True,
            type="primary" if can_open else "secondary", disabled=not can_open,
        ):
            st.session_state["viewer_doc_id"] = did
            st.switch_page("pages/5_Viewer.py")

        with _cm.popover("···", use_container_width=True):
            _can_editor = status in ("layout_done", "ocr_done", "done")
            if st.button(
                "📋 Open Editor",
                key=f"open_editor_{did}",
                disabled=not _can_editor,
                help="Open block editor" if _can_editor else "Available after layout detection",
            ):
                st.session_state["viewer_doc_id"]           = did
                st.session_state["viewer_page"]             = 1
                st.session_state["viewer_selected_block"]   = None
                st.rerun()
            if st.button("⬇ Download .md", key=f"dl_{did}"):
                st.session_state["_picker_dl_req"] = did
                st.rerun()
            if st.button("🗑 Delete", key=f"del_{did}"):
                st.session_state["_picker_confirm_delete"] = did
                st.rerun()

        st.divider()

    st.stop()

all_blocks = fetch_blocks(doc_id, st.session_state.get("access_token", ""))
doc_name = st.session_state.get("current_doc_name", doc_id)


def _do_undo() -> bool:
    _stk = st.session_state.undo_stack
    if not _stk:
        return False
    _entry = _stk[-1]
    _act = _entry["action"]
    try:
        if _act == "delete":
            _snap = _entry["snapshot"]
            _r = api("POST", f"/processing/{doc_id}/blocks",
                     json={
                         "block_type": _snap.get("block_type"),
                         "page_num":   _snap.get("page_num"),
                         "bbox":       _snap.get("bbox"),
                     }, timeout=10)
            if _r and _r.status_code == 200:
                st.session_state.viewer_selected_block = _r.json().get("block_id")
                _stk.pop()
                fetch_blocks.clear()
                return True
        elif _act == "add":
            _r = api("DELETE", f"/processing/{doc_id}/blocks/{_entry['block_id']}", timeout=5)
            if _r and _r.status_code in (200, 204, 404):
                if st.session_state.viewer_selected_block == _entry["block_id"]:
                    st.session_state.viewer_selected_block = None
                _stk.pop()
                fetch_blocks.clear()
                return True
        elif _act == "patch":
            _snap = _entry["snapshot"]
            _r = api("PATCH", f"/processing/{doc_id}/blocks/{_entry['block_id']}",
                     json={
                         "block_type": _snap.get("block_type"),
                         "bbox":       _snap.get("bbox"),
                         "output":     _snap.get("output"),
                         "status":     _snap.get("status"),
                     }, timeout=5)
            if _r and _r.status_code == 200:
                _stk.pop()
                fetch_blocks.clear()
                return True
    except Exception as _e:
        st.toast(f"Undo: {_e}", icon="❌")
    return False

_status_r = api("GET", f"/processing/{doc_id}/status", timeout=5)
total_pages = _status_r.json().get("page_count", 1) if _status_r and _status_r.status_code == 200 else 1


# ─── MODEL SELECTION DIALOG ───────────────────────────────────────────────────
@st.dialog("🎯 Select models for recognition", width="large")
def model_selection_dialog(doc_id: str, all_blocks: list):
    r = api("GET", "/settings/available-models", timeout=5)
    if r is None or r.status_code != 200:
        st.error("Failed to load model list")
        return
    avail = r.json()

    present_types = sorted(set(
        b.get("block_type") for b in all_blocks
        if b.get("block_type") in avail
    ))

    if not present_types:
        st.warning("No annotated blocks in this document")
        return

    BLOCK_LABELS = {
        "text":          "📝 Text",
        "figure":        "🖼 Image",
        "table_simple":  "📊 Simple table",
        "table_complex": "📊 Complex table",
        "formula":       "➗ Formula",
        "table":         "📊 Table",
    }

    st.markdown("Assign a model for each block type in the document:")
    st.markdown("---")

    choices = {}
    for btype in present_types:
        info    = avail[btype]
        models  = info["models"]
        default = st.session_state.viewer_model_choices.get(btype, info["default"])

        col_type, col_sel = st.columns([1, 2])
        with col_type:
            cnt = sum(1 for b in all_blocks if b.get("block_type") == btype)
            st.markdown(f"**{BLOCK_LABELS.get(btype, btype)}**")
            st.caption(f"{cnt} blocks")
        with col_sel:
            opts       = [m["id"]    for m in models]
            opt_labels = []
            for m in models:
                avail_icon = "✅" if m["available"] else "○"
                reason     = f" — {m['reason']}" if m.get("reason") else ""
                opt_labels.append(f"{avail_icon} {m['label']}{reason}")

            default_idx = opts.index(default) if default in opts else 0
            chosen = st.selectbox(
                f"Model for {btype}",
                options=opts,
                index=default_idx,
                format_func=lambda x, _opts=opts, _labels=opt_labels: _labels[_opts.index(x)],
                key=f"modal_model_{btype}",
                label_visibility="collapsed",
            )
            choices[btype] = chosen

            chosen_meta = next((m for m in models if m["id"] == chosen), None)
            if chosen_meta and not chosen_meta["available"]:
                st.warning(f"⚠️ {chosen_meta['reason']}", icon=None)

        st.markdown("")

    st.markdown("---")
    run_col, cancel_col = st.columns([2, 1])
    with run_col:
        if st.button("▶ Run recognition", type="primary",
                     use_container_width=True, key="modal_run"):
            st.session_state.viewer_model_choices = choices
            try:
                resp = api("POST", f"/processing/{doc_id}/ocr",
                           json={"model_choices": choices}, timeout=15)
                if resp and resp.status_code == 200:
                    result = resp.json()
                    if result.get("status") in ("started", "already_running"):
                        st.session_state["viewer_model_dialog_open"] = False
                        st.session_state["ocr_polling"] = True
                        st.session_state["ocr_doc_id"]  = doc_id
                        st.rerun()
                elif resp:
                    st.error(f"Launch error: {resp.status_code} — {resp.text[:200]}")
            except Exception as e:
                st.error(str(e))
    with cancel_col:
        if st.button("Cancel", use_container_width=True, key="modal_cancel"):
            st.rerun()


# ── OCR polling ──────────────────────────────────────────────────────────────
if st.session_state.get("ocr_polling") and st.session_state.get("ocr_doc_id") == doc_id:
    try:
        _ocr_resp = api("GET", f"/processing/{doc_id}/ocr-status", timeout=5)
        if _ocr_resp and _ocr_resp.status_code == 200:
            _ocr_st   = _ocr_resp.json()
            _processed = _ocr_st.get("processed", 0)
            _total     = _ocr_st.get("total", 1) or 1
            _status    = _ocr_st.get("status", "running")

            if _status == "running":
                _prog_pct = _processed / (_total or 1)
                st.progress(_prog_pct, text=f"Blocks processed: {_processed} / {_total}")
                if st.button(
                    "⏹ Stop OCR",
                    key="btn_cancel_ocr",
                    type="secondary",
                    help="Stops processing after the current block. Already processed blocks are saved.",
                ):
                    try:
                        _cancel_resp = api("POST", f"/processing/{doc_id}/ocr/cancel", timeout=5)
                        if _cancel_resp and _cancel_resp.status_code == 200:
                            st.warning("⏸ Cancellation sent, waiting for current block to finish...")
                        elif _cancel_resp:
                            st.error(f"Cancel error: {_cancel_resp.status_code}")
                    except Exception as _ce:
                        st.error(str(_ce))
                time.sleep(3)
                st.rerun()
            elif _status == "cancelled":
                st.warning(f"⏹ OCR cancelled. Processed: {_processed}/{_total} blocks.")
                st.session_state.pop("ocr_polling", None)
                fetch_blocks.clear()
                st.rerun()
            elif _status == "done":
                st.success(
                    f"✅ OCR complete! Processed: {_processed}, "
                    f"errors: {_ocr_st.get('errors', 0)}"
                )
                st.session_state.pop("ocr_polling", None)
                fetch_blocks.clear()
            elif _status == "error":
                st.error(f"❌ OCR error: {_ocr_st.get('error_msg', 'unknown')}")
                st.session_state.pop("ocr_polling", None)
    except Exception as _e:
        st.warning(f"Failed to get OCR status: {_e}")
        st.session_state.pop("ocr_polling", None)

# ── Editor-style layout activation ───────────────────────────────────────────
inject_editor_layout()

# ══════════════════════════════════════════════════════════════════════════════
# TOOLBAR — flat st.columns (no nesting) so buttons register on first click
# ══════════════════════════════════════════════════════════════════════════════

_is_draw = st.session_state.viewer_draw_mode
_stk_tb  = st.session_state.undo_stack
_zoom_tb = st.session_state.canvas_zoom

(
    _tn_first, _tn_prev, _tn_inp, _tn_next, _tn_last,
    _t_draw, _t_type,
    _t_pg_lbl,
    _t_zm, _t_zoom_lbl, _t_zp,
    _t_undo,
    _t_back,
) = st.columns([1, 1, 2, 1, 1, 2, 3, 2, 1, 2, 1, 2, 2])

with _tn_first:
    if st.button("⏮", use_container_width=True, key="nav_first"):
        st.session_state.viewer_page            = 1
        st.session_state.viewer_selected_block  = None
        st.session_state.viewer_canvas_version += 1
        st.rerun()

with _tn_prev:
    if st.button("◀", use_container_width=True, key="nav_prev"):
        if st.session_state.viewer_page > 1:
            st.session_state.viewer_page           -= 1
            st.session_state.viewer_selected_block  = None
            st.session_state.viewer_canvas_version += 1
            st.rerun()

with _tn_inp:
    # Keep tb_page_input in sync so it doesn't overwrite viewer_page after nav buttons fire
    st.session_state["tb_page_input"] = st.session_state.viewer_page
    _new_page = st.number_input(
        "Page", min_value=1, max_value=total_pages,
        label_visibility="collapsed", key="tb_page_input",
    )
    if _new_page != st.session_state.viewer_page:
        st.session_state.viewer_page            = _new_page
        st.session_state.viewer_selected_block  = None
        st.session_state.viewer_canvas_version += 1
        st.rerun()

with _tn_next:
    if st.button("▶", use_container_width=True, key="nav_next"):
        if st.session_state.viewer_page < total_pages:
            st.session_state.viewer_page           += 1
            st.session_state.viewer_selected_block  = None
            st.session_state.viewer_canvas_version += 1
            st.rerun()

with _tn_last:
    if st.button("⏭", use_container_width=True, key="nav_last"):
        st.session_state.viewer_page            = total_pages
        st.session_state.viewer_selected_block  = None
        st.session_state.viewer_canvas_version += 1
        st.rerun()

with _t_draw:
    if st.button(
        "👆 Select" if _is_draw else "✏️ Draw",
        use_container_width=True,
        type="secondary" if _is_draw else "primary",
        key="toggle_mode",
    ):
        st.session_state.viewer_draw_mode = not _is_draw
        st.session_state.viewer_canvas_version += 1

with _t_type:
    if st.session_state.viewer_draw_mode:
        _type_opts = ["text", "table_simple", "table_complex", "formula", "figure"]
        _icons = {"text": "📝", "table_simple": "📊", "table_complex": "📊", "formula": "➗", "figure": "🖼"}
        _prev_type = st.session_state.viewer_draw_type
        _new_type = st.selectbox(
            "type", _type_opts,
            index=_type_opts.index(_prev_type),
            format_func=lambda t: f"{_icons.get(t, '?')} {t}",
            label_visibility="collapsed",
            key="draw_type_sel",
        )
        if _new_type != _prev_type:
            st.session_state.viewer_draw_type    = _new_type
            st.session_state.viewer_canvas_version += 1

with _t_pg_lbl:
    st.markdown(
        f"<div style='padding-top:8px;text-align:center;font-size:13px;"
        f"color:rgba(255,255,255,0.5)'>p.{st.session_state.viewer_page}/{total_pages}</div>",
        unsafe_allow_html=True,
    )

with _t_zm:
    if st.button("➖", use_container_width=True, key="zoom_out", help="Zoom out"):
        st.session_state.canvas_zoom = round(max(0.3, _zoom_tb - 0.15), 2)
        st.session_state.viewer_canvas_version += 1
        st.rerun()

with _t_zoom_lbl:
    st.markdown(
        f"<div style='padding-top:8px;text-align:center;font-size:13px'>"
        f"{_zoom_tb:.0%}</div>",
        unsafe_allow_html=True,
    )

with _t_zp:
    if st.button("➕", use_container_width=True, key="zoom_in", help="Zoom in"):
        st.session_state.canvas_zoom = round(min(3.0, _zoom_tb + 0.15), 2)
        st.session_state.viewer_canvas_version += 1
        st.rerun()

with _t_undo:
    _undo_label = f"↩ ({len(_stk_tb)})" if _stk_tb else "↩"
    if st.button(
        _undo_label,
        use_container_width=True,
        disabled=len(_stk_tb) == 0,
        key="btn_undo_top",
        help=f"Undo last action ({len(_stk_tb)} in stack)" if _stk_tb else "No actions to undo",
    ):
        if _do_undo():
            st.rerun()

with _t_back:
    if st.button("⬅ Back", use_container_width=True, key="btn_change_doc"):
        st.session_state.pop("viewer_doc_id", None)
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# THREE-COLUMN LAYOUT
# ══════════════════════════════════════════════════════════════════════════════
col_left, col_main, col_right = st.columns([1.0, 3.5, 1.5])

# ══════════════════════════════════════════════════════════════════════════════
# LEFT — recognition info only
# ══════════════════════════════════════════════════════════════════════════════
with col_left:
    _cur_doc_name = st.session_state.get("current_doc_name") or doc_id
    st.markdown(f"**📄 {_cur_doc_name[:30]}**")
    st.divider()
    total_blocks_count  = len(all_blocks)
    marked_blocks_count = sum(1 for b in all_blocks if b.get("block_type"))
    if total_blocks_count > 0:
        st.markdown("**🎯 Recognition**")
        st.caption(f"Annotated blocks: {marked_blocks_count}")
        if st.button("✅ Select models & run",
                     use_container_width=True, type="primary",
                     key="btn_open_model_dialog"):
            model_selection_dialog(doc_id, all_blocks)

# ══════════════════════════════════════════════════════════════════════════════
# MAIN — canvas only (toolbar moved above)
# ══════════════════════════════════════════════════════════════════════════════
with col_main:
    is_draw = st.session_state.viewer_draw_mode

    # ── Image ─────────────────────────────────────────────────────────────
    page_img, orig_w, orig_h, scale = fetch_page_image(
        doc_id, st.session_state.viewer_page, st.session_state.get("access_token", "")
    )

    if page_img:
        st.session_state.viewer_orig_w = orig_w
        st.session_state.viewer_orig_h = orig_h

        draw_type = st.session_state.viewer_draw_type

        annotated = draw_blocks_on_image(
            page_img, all_blocks,
            st.session_state.viewer_page,
            st.session_state.viewer_selected_block,
            scale=scale,
            show_order=st.session_state.get("viewer_show_order", True),
        )

        zoom = st.session_state.canvas_zoom
        if zoom != 1.0:
            annotated = annotated.resize(
                (int(annotated.width * zoom), int(annotated.height * zoom)),
                Image.LANCZOS,
            )
        effective_scale = scale * zoom

        img_w = annotated.width

        if st.session_state.viewer_draw_mode:
            # ── DRAW MODE: st_canvas (drag-and-drop) ──────────────────────
            canvas_key = (
                f"canvas_{doc_id}_p{st.session_state.viewer_page}"
                f"_v{st.session_state.viewer_canvas_version}"
            )
            canvas_result = st_canvas(
                fill_color="rgba(255, 255, 255, 0.0)",
                stroke_width=2,
                stroke_color=TYPE_HEX.get(draw_type, "#FF0000"),
                background_image=annotated,
                update_streamlit=True,
                height=annotated.height,
                width=img_w,
                drawing_mode="rect",
                key=canvas_key,
            )

            _drawn_rect = None
            if canvas_result is not None and canvas_result.json_data is not None:
                _rects = [
                    o for o in canvas_result.json_data.get("objects", [])
                    if o.get("type") == "rect"
                ]
                if _rects:
                    _last = _rects[-1]
                    _left   = _last.get("left", 0)
                    _top    = _last.get("top", 0)
                    _width  = _last.get("width", 0) * _last.get("scaleX", 1)
                    _height = _last.get("height", 0) * _last.get("scaleY", 1)
                    x1 = max(0, int(_left / effective_scale))
                    y1 = max(0, int(_top  / effective_scale))
                    x2 = min(orig_w, int((_left + _width)  / effective_scale))
                    y2 = min(orig_h, int((_top  + _height) / effective_scale))
                    if x2 > x1 + 10 and y2 > y1 + 10:
                        _drawn_rect = [x1, y1, x2, y2]

            if _drawn_rect:
                ox1, oy1, ox2, oy2 = _drawn_rect
                rgb = BLOCK_COLORS.get(draw_type, (128, 128, 128))
                st.markdown(
                    f"<div style='padding:8px 12px;"
                    f"background:rgba({rgb[0]},{rgb[1]},{rgb[2]},0.15);"
                    f"border-left:3px solid {TYPE_HEX[draw_type]};border-radius:4px;"
                    f"font-size:13px;margin:4px 0'>"
                    f"📐 <b>{draw_type}</b> · [{ox1}, {oy1}, {ox2}, {oy2}] "
                    f"· {ox2-ox1}×{oy2-oy1} px</div>",
                    unsafe_allow_html=True,
                )
                st.session_state["_drawn_rect"] = _drawn_rect
            else:
                st.session_state.pop("_drawn_rect", None)
                st.info("🖱 Draw a rectangle for a new block (drag)", icon=None)

        elif st.session_state.viewer_mode == "edit" and st.session_state.viewer_selected_block:
            # ── EDIT MODE: st_canvas transform ────────────────────────────
            _sel = next(
                (b for b in all_blocks if b["block_id"] == st.session_state.viewer_selected_block),
                None,
            )
            if _sel:
                _bx = _sel.get("bbox", [0, 0, 100, 100])
                _ex1, _ey1, _ex2, _ey2 = _bx
                _initial = {
                    "version": "5.2.4",
                    "objects": [{
                        "type": "rect",
                        "left":   float(_ex1 * effective_scale),
                        "top":    float(_ey1 * effective_scale),
                        "width":  float((_ex2 - _ex1) * effective_scale),
                        "height": float((_ey2 - _ey1) * effective_scale),
                        "fill":   "rgba(255, 165, 0, 0.15)",
                        "stroke": "#FF8C00",
                        "strokeWidth": 3,
                        "scaleX": 1.0,
                        "scaleY": 1.0,
                        "angle":  0,
                        "selectable": True,
                    }],
                }
                edit_canvas_key = (
                    f"canvas_{doc_id}_p{st.session_state.viewer_page}"
                    f"_edit_{st.session_state.viewer_selected_block}"
                    f"_v{st.session_state.viewer_canvas_version}"
                )
                edit_result = st_canvas(
                    fill_color="rgba(255, 165, 0, 0.15)",
                    stroke_width=3,
                    stroke_color="#FF8C00",
                    background_image=annotated,
                    initial_drawing=_initial,
                    update_streamlit=True,
                    height=annotated.height,
                    width=img_w,
                    drawing_mode="transform",
                    key=edit_canvas_key,
                )
                if edit_result is not None and edit_result.json_data is not None:
                    _objs = [
                        o for o in edit_result.json_data.get("objects", [])
                        if o.get("type") == "rect"
                    ]
                    if _objs:
                        _o   = _objs[0]
                        _nl  = _o.get("left", 0)
                        _nt  = _o.get("top", 0)
                        _nw  = _o.get("width", 0) * _o.get("scaleX", 1)
                        _nh  = _o.get("height", 0) * _o.get("scaleY", 1)
                        nx1  = max(0, int(_nl / effective_scale))
                        ny1  = max(0, int(_nt / effective_scale))
                        nx2  = min(orig_w, int((_nl + _nw) / effective_scale))
                        ny2  = min(orig_h, int((_nt + _nh) / effective_scale))
                        if max(abs(nx1-_ex1), abs(ny1-_ey1), abs(nx2-_ex2), abs(ny2-_ey2)) > 3:
                            st.session_state["pending_bbox"]     = [nx1, ny1, nx2, ny2]
                            st.session_state["pending_bbox_for"] = st.session_state.viewer_selected_block
            else:
                st.session_state.viewer_mode = None

        else:
            # ── VIEW MODE: streamlit_image_coordinates (click to select block) ──
            coords = streamlit_image_coordinates(
                annotated,
                width=img_w,
                key=f"viewer_{doc_id}_{st.session_state.viewer_page}",
            )
            if coords is not None:
                _cv = st.session_state.viewer_canvas_version
                _coord_key = (_cv, coords["x"], coords["y"])
                if _coord_key == st.session_state.get("canvas_last_coord"):
                    coords = None
                else:
                    st.session_state["canvas_last_coord"] = _coord_key

            if coords is not None:
                ox = int(coords["x"] / effective_scale)
                oy = int(coords["y"] / effective_scale)
                clicked = None
                for b in all_blocks:
                    if b.get("page_num") != st.session_state.viewer_page:
                        continue
                    bx = b.get("bbox", [])
                    if len(bx) == 4 and bx[0] <= ox <= bx[2] and bx[1] <= oy <= bx[3]:
                        clicked = b["block_id"]
                        break
                if clicked and clicked != st.session_state.viewer_selected_block:
                    st.session_state.viewer_selected_block = clicked
                    st.session_state.viewer_edit_mode      = False
                    st.session_state.viewer_confirm_delete = False
                    st.session_state.viewer_mode           = None
                    st.rerun()
    else:
        st.warning("Page image unavailable")


# ══════════════════════════════════════════════════════════════════════════════
# RIGHT — draw actions + block list + block details
# ══════════════════════════════════════════════════════════════════════════════
with col_right:
    st.markdown("### 🔧 Tools")

    st.checkbox(
        "Show reading order",
        value=st.session_state.get("viewer_show_order", True),
        key="viewer_show_order",
    )

    # ── Draw mode: Add block / Redraw (правка 1 — кнопки выше канваса) ────
    if st.session_state.viewer_draw_mode:
        _drawn_rect = st.session_state.get("_drawn_rect")
        draw_type   = st.session_state.viewer_draw_type
        rgb         = BLOCK_COLORS.get(draw_type, (128, 128, 128))
        if _drawn_rect:
            ox1, oy1, ox2, oy2 = _drawn_rect
            ab1, ab2 = st.columns([2, 1])
            with ab1:
                if st.button("✅ Add block", use_container_width=True,
                             type="primary", key="btn_canvas_add"):
                    resp = api("POST", f"/processing/{doc_id}/blocks",
                               json={
                                   "block_type": draw_type,
                                   "page_num":   st.session_state.viewer_page,
                                   "bbox":       [ox1, oy1, ox2, oy2],
                               }, timeout=10)
                    if resp and resp.status_code == 200:
                        new_id = resp.json().get("block_id")
                        _stk = st.session_state.undo_stack
                        _stk.append({"action": "add", "block_id": new_id})
                        if len(_stk) > 10:
                            _stk.pop(0)
                        fetch_blocks.clear()
                        st.session_state.viewer_canvas_version += 1
                        st.session_state.viewer_draw_mode      = False
                        st.session_state.viewer_selected_block = new_id
                        st.session_state.pop("_drawn_rect", None)
                        st.rerun()
                    elif resp:
                        st.error(f"Error {resp.status_code}: {resp.text[:80]}")
            with ab2:
                if st.button("🔄 Redraw", use_container_width=True, key="btn_redraw"):
                    st.session_state.viewer_canvas_version += 1
                    st.session_state.pop("_drawn_rect", None)
                    st.rerun()
        else:
            st.markdown(
                f"<div style='padding:10px;background:rgba({rgb[0]},{rgb[1]},{rgb[2]},0.1);"
                f"border:1px solid {TYPE_HEX[draw_type]};border-radius:8px;font-size:13px'>"
                f"<b>✏️ Draw mode ({draw_type})</b><br><br>"
                f"Draw a rectangle on the canvas, then click <b>Add block</b></div>",
                unsafe_allow_html=True,
            )
        st.markdown("---")

    # ── Block list — sortable, в правой панели ────────────────────────────
    _page_blocks = [
        b for b in all_blocks
        if b.get("page_num") == st.session_state.viewer_page
    ]
    _order_key = f"block_order_{doc_id}_p{st.session_state.viewer_page}"

    _t_icon = {"text": "📝", "table": "📊", "table_simple": "📊",
               "table_complex": "📊", "formula": "➗", "figure": "🖼"}
    _s_icon = {"needs_review": "🔴", "accepted": "✅", "ocr_done": "🔵", "error": "❌"}

    if _page_blocks:
        # Базовый порядок — по sort_order из backend
        _page_blocks_sorted = sorted(
            _page_blocks,
            key=lambda b: b.get("sort_order", b.get("blockidx", 0)),
        )

        # Метки для sort_items: ignored помечаем 🚫 (blockid после последнего " | ")
        _labels = [
            f"{'🚫' if b.get('ignore') else _t_icon.get(b.get('block_type',''),'?')} "
            f"{b.get('block_type','?').upper()} | {b['block_id']}"
            for b in _page_blocks_sorted
        ]

        st.caption(f"Blocks on page: {len(_page_blocks)}")
        _sorted_labels = sort_items(
            _labels,
            direction="vertical",
            key=f"sort_{doc_id}_p{st.session_state.viewer_page}",
        )
        if _sorted_labels != _labels:
            st.session_state[_order_key] = _sorted_labels
            _new_order = [lbl.split(" | ")[-1].strip() for lbl in _sorted_labels if " | " in lbl]
            try:
                api("POST", f"/processing/{doc_id}/blocks/reorder",
                    json={"order": _new_order}, timeout=5)
            except Exception:
                pass
            # Обновить sort_order прямо в all_blocks — канвас нарисуется правильно до следующего rerun
            _order_map = {bid: i for i, bid in enumerate(_new_order)}
            for _b in all_blocks:
                if _b.get("block_id") in _order_map:
                    _b["sort_order"] = _order_map[_b["block_id"]]
            fetch_blocks.clear()
            st.rerun()

        # Selectbox для выбора блока (sort_items не поддерживает клик)
        _id_to_block = {b["block_id"]: b for b in _page_blocks}
        _sorted_ids = [lbl.split(" | ")[-1].strip() for lbl in _sorted_labels if " | " in lbl]
        _cur_sel = st.session_state.viewer_selected_block
        _sel_opts = ["— select block —"] + [
            f"{'🚫' if _id_to_block[bid].get('ignore') else _t_icon.get(_id_to_block[bid].get('block_type',''),'?')} "
            f"{bid} {_s_icon.get(_id_to_block[bid].get('status',''),'⚪')}"
            for bid in _sorted_ids if bid in _id_to_block
        ]
        _sel_idx = 0
        if _cur_sel and _cur_sel in _sorted_ids:
            _sel_idx = _sorted_ids.index(_cur_sel) + 1
        _chosen = st.selectbox(
            "Select block",
            options=_sel_opts,
            index=_sel_idx,
            key=f"blksel_{doc_id}_p{st.session_state.viewer_page}",
            label_visibility="collapsed",
        )
        if _chosen != "— select block —":
            _chosen_idx = _sel_opts.index(_chosen) - 1
            if 0 <= _chosen_idx < len(_sorted_ids):
                _chosen_bid = _sorted_ids[_chosen_idx]
                if _chosen_bid != _cur_sel:
                    st.session_state.viewer_selected_block = _chosen_bid
                    st.session_state.viewer_edit_mode      = False
                    st.session_state.viewer_confirm_delete = False
                    st.session_state.viewer_mode           = None
                    st.rerun()
        elif _cur_sel:
            st.session_state.viewer_selected_block = None
            st.rerun()

        st.markdown("---")
    else:
        st.caption("No blocks on this page")
        st.markdown("---")

    selected_id = st.session_state.viewer_selected_block

    if not selected_id:
        if not st.session_state.viewer_draw_mode:
            st.caption("Click a block on the image or select from list")
        st.stop()

    block = next((b for b in all_blocks if b["block_id"] == selected_id), None)
    if not block:
        st.warning("Block not found")
        st.stop()

    btype   = block.get("block_type", "—")
    bstatus = block.get("status", "—")
    conf    = block.get("confidence", 0)
    bbox    = block.get("bbox", [0, 0, 0, 0])

    type_icon   = {"text": "📝", "table": "📊", "table_simple": "📊", "table_complex": "📊", "formula": "➗", "figure": "🖼"}.get(btype, "?")
    status_icon = {"needs_review": "🔴", "accepted": "🟢",
                   "ocr_done": "🔵", "error": "❌"}.get(bstatus, "⚪")

    st.markdown(f"**{type_icon} {btype.upper()}**")
    st.caption(f"`{selected_id}`")
    st.markdown(f"Status: {status_icon} `{bstatus}`")
    st.markdown(f"Conf: `{conf:.2f}`")

    _preview_bust = st.session_state.get(f"preview_bust_{selected_id}", 0)
    _preview_bytes = fetch_block_preview(doc_id, selected_id, st.session_state.get("access_token", ""), _bust=_preview_bust)
    if _preview_bytes:
        st.image(_preview_bytes, use_container_width=True)
    else:
        st.caption("No preview")

    # ── Ignore toggle ─────────────────────────────────────────────────────
    _is_ignored = block.get("ignore", False)
    _ignore_label = "✓ Ignored" if _is_ignored else "Ignore"
    if st.button(_ignore_label, key=f"btn_ignore_{selected_id}", use_container_width=True):
        _r = api("PATCH", f"/processing/{doc_id}/blocks/{selected_id}",
                 json={"ignore": not _is_ignored}, timeout=5)
        if _r and _r.status_code == 200:
            fetch_blocks.clear()
            st.rerun()
        elif _r:
            st.error(f"Error: {_r.text[:60]}")

    # ── Geometry & type ───────────────────────────────────────────────────
    with st.expander("✏️ Geometry & type", expanded=False):
        _type_opts = ["text", "table", "table_simple", "table_complex", "formula", "figure"]
        new_type = st.selectbox(
            "Type",
            _type_opts,
            index=_type_opts.index(btype) if btype in _type_opts else 0,
            key=f"sel_type_{selected_id}",
        )
        if new_type != btype:
            if st.button("💾 Save type", use_container_width=True, key=f"btn_save_type_{selected_id}"):
                _stk = st.session_state.undo_stack
                _stk.append({"action": "patch", "block_id": selected_id, "snapshot": dict(block)})
                if len(_stk) > 10:
                    _stk.pop(0)
                _r = api("PATCH", f"/processing/{doc_id}/blocks/{selected_id}",
                         json={"block_type": new_type}, timeout=5)
                if _r and _r.status_code == 200:
                    fetch_blocks.clear()
                    st.rerun()
                else:
                    _stk.pop()
                    if _r:
                        st.error(f"Error: {_r.text[:60]}")

        st.divider()

        _edit_active = st.session_state.viewer_mode == "edit"
        if not _edit_active:
            if st.button(
                "📐 Edit geometry",
                type="primary",
                use_container_width=True,
                key="btn_edit_geom",
            ):
                st.session_state.pop("pending_bbox", None)
                st.session_state.pop("pending_bbox_for", None)
                st.session_state.viewer_mode           = "edit"
                st.session_state.viewer_canvas_version += 1
                st.rerun()
            st.caption(f"bbox: {bbox[0]}, {bbox[1]} → {bbox[2]}, {bbox[3]}")
            st.caption(f"size: {bbox[2]-bbox[0]} × {bbox[3]-bbox[1]} px")
        else:
            st.info("Drag corners or sides of the bounding box on the image.", icon=None)

            _pending     = st.session_state.get("pending_bbox")
            _pending_for = st.session_state.get("pending_bbox_for")
            _has_pending = (
                _pending is not None
                and _pending_for == selected_id
                and _pending != list(bbox)
            )

            _sc1, _sc2 = st.columns(2)
            with _sc1:
                if st.button(
                    "💾 Save",
                    type="primary",
                    disabled=not _has_pending,
                    use_container_width=True,
                    key="btn_save_geom",
                ):
                    _stk = st.session_state.undo_stack
                    _stk.append({"action": "patch", "block_id": selected_id, "snapshot": dict(block)})
                    if len(_stk) > 10:
                        _stk.pop(0)
                    _r = api("PATCH", f"/processing/{doc_id}/blocks/{selected_id}",
                             json={"bbox": _pending}, timeout=10)
                    if _r and _r.status_code == 200:
                        st.session_state[f"preview_bust_{selected_id}"] = int(time.time())
                        fetch_block_preview.clear()
                        fetch_blocks.clear()
                        st.session_state.pop("pending_bbox", None)
                        st.session_state.pop("pending_bbox_for", None)
                        st.session_state.viewer_mode           = None
                        st.session_state.viewer_canvas_version += 1
                        st.rerun()
                    else:
                        _stk.pop()
                        if _r:
                            st.error(f"Error: {_r.text[:60]}")
            with _sc2:
                if st.button(
                    "✕ Cancel",
                    type="secondary",
                    use_container_width=True,
                    key="btn_cancel_geom",
                ):
                    st.session_state.pop("pending_bbox", None)
                    st.session_state.pop("pending_bbox_for", None)
                    st.session_state.viewer_mode           = None
                    st.session_state.viewer_canvas_version += 1
                    st.rerun()

    # ── Delete block ──────────────────────────────────────────────────────
    if not st.session_state.viewer_confirm_delete:
        if st.button("🗑 Delete block", use_container_width=True, key=f"btn_del_{selected_id}"):
            st.session_state.viewer_confirm_delete = True
    else:
        st.warning("⚠️ Delete permanently?")
        dc1, dc2 = st.columns(2)
        with dc1:
            if st.button("✅ Yes", use_container_width=True, type="primary", key=f"btn_del_yes_{selected_id}"):
                _stk = st.session_state.undo_stack
                _stk.append({"action": "delete", "block_id": selected_id, "snapshot": dict(block)})
                if len(_stk) > 10:
                    _stk.pop(0)
                api("DELETE", f"/processing/{doc_id}/blocks/{selected_id}", timeout=5)
                st.session_state.viewer_selected_block  = None
                st.session_state.viewer_confirm_delete  = False
                fetch_blocks.clear()
                st.rerun()
        with dc2:
            if st.button("❌ No", use_container_width=True, key="btn_del_no"):
                st.session_state.viewer_confirm_delete = False


