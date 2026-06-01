import streamlit as st

_CSS = """
<style>
:root {
    --brand-primary:   #7C3AED;
    --brand-secondary: #6D28D9;
    --brand-danger:    #EF4444;
    --brand-success:   #10B981;
}

/* Primary buttons */
button[data-testid="baseButton-primary"],
button[kind="primary"] {
    background-color: var(--brand-primary) !important;
    border-color:     var(--brand-primary) !important;
    color: white !important;
}
button[data-testid="baseButton-primary"]:hover,
button[kind="primary"]:hover {
    background-color: var(--brand-secondary) !important;
    border-color:     var(--brand-secondary) !important;
}

/* Hide auth page from sidebar navigation */
[data-testid="stSidebarNav"] a[href$="/Auth"],
[data-testid="stSidebarNav"] li:has(a[href$="/Auth"]) {
    display: none !important;
}


/* Explicit danger class — use sparingly: st.markdown('<span class="danger">…</span>', unsafe_allow_html=True) */
.brand-danger  { color: var(--brand-danger);  }
.brand-success { color: var(--brand-success); }
</style>
"""


_CSS_HIDE_APP_NAV = """
<style>
[data-testid="stSidebarNav"] a[href$="/"],
[data-testid="stSidebarNav"] li:has(a[href$="/"]) {
    display: none !important;
}
</style>
"""


_CSS_EDITOR_LAYOUT = """
<style>
/* ── Editor layout (detail mode) ─────────────────────────────────────────── */

/* Hide Streamlit top header */
header[data-testid="stHeader"] { display: none !important; }

/* Remove block-container padding; NO overflow:hidden here — would kill sticky */
.main .block-container {
    padding-top: 0 !important;
    padding-bottom: 0 !important;
    max-width: 100% !important;
}

/* Toolbar = first stHorizontalBlock in the page.
   Use sticky + bg so it pins to top when user scrolls. */
section.main > div > div[data-testid="stVerticalBlock"]
    > div[data-testid="stHorizontalBlock"]:first-of-type {
    position: sticky !important;
    top: 0 !important;
    z-index: 999 !important;
    background: #0e1117 !important;
    border-bottom: 1px solid rgba(255,255,255,0.12) !important;
    padding: 4px 8px !important;
}

/* Second stHorizontalBlock = three main columns */
section.main > div > div[data-testid="stVerticalBlock"]
    > div[data-testid="stHorizontalBlock"]:nth-of-type(2) {
    height: calc(100vh - 56px) !important;
    align-items: stretch !important;
    gap: 0 !important;
    overflow: hidden !important;
}

/* All three inner columns scroll independently */
section.main > div > div[data-testid="stVerticalBlock"]
    > div[data-testid="stHorizontalBlock"]:nth-of-type(2)
    > div[data-testid="stColumn"] {
    overflow-y: auto !important;
    overflow-x: hidden !important;
}

/* Canvas column */
section.main > div > div[data-testid="stVerticalBlock"]
    > div[data-testid="stHorizontalBlock"]:nth-of-type(2)
    > div[data-testid="stColumn"]:nth-child(2) {
    overflow-x: auto !important;
    background: #111827 !important;
    padding: 16px !important;
}

/* Panel borders */
section.main > div > div[data-testid="stVerticalBlock"]
    > div[data-testid="stHorizontalBlock"]:nth-of-type(2)
    > div[data-testid="stColumn"]:nth-child(1) {
    border-right: 1px solid rgba(255,255,255,0.08) !important;
    padding: 12px 10px !important;
}
section.main > div > div[data-testid="stVerticalBlock"]
    > div[data-testid="stHorizontalBlock"]:nth-of-type(2)
    > div[data-testid="stColumn"]:nth-child(3) {
    border-left: 1px solid rgba(255,255,255,0.08) !important;
    padding: 12px 10px !important;
}

/* Prevent full-page body scroll — only inner columns scroll */
html, body { overflow: hidden !important; height: 100% !important; }
</style>
"""


def inject_global_styles() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    is_admin = st.session_state.get("current_user", {}).get("is_admin", False)
    if not is_admin:
        st.markdown(_CSS_HIDE_APP_NAV, unsafe_allow_html=True)


def inject_editor_layout() -> None:
    """Inject CSS for fixed toolbar + scrollable column layout."""
    st.markdown(_CSS_EDITOR_LAYOUT, unsafe_allow_html=True)
