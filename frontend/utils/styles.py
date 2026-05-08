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


def inject_global_styles() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    is_admin = st.session_state.get("current_user", {}).get("is_admin", False)
    if not is_admin:
        st.markdown(_CSS_HIDE_APP_NAV, unsafe_allow_html=True)
