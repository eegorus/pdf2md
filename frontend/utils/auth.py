import base64
import json
import os
import time

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

_INACTIVITY_TIMEOUT = 4 * 3600  # 4 hours
_REFRESH_THRESHOLD = 5 * 60     # refresh when < 5 min remaining


def _jwt_exp(token: str) -> float:
    """Extract exp claim from JWT without signature verification."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload_b64)).get("exp", 0))
    except Exception:
        return 0.0


def ensure_authenticated() -> bool:
    """
    Call at the top of every protected page (after set_page_config).
    Handles: missing token, inactivity logout, and silent token refresh.
    Returns True if session is valid; switches to login page otherwise.
    """
    if not st.session_state.get("access_token"):
        st.switch_page("pages/0Auth.py")
        return False

    now = time.time()

    last = st.session_state.get("last_activity_ts", now)
    if now - last > _INACTIVITY_TIMEOUT:
        for k in ("access_token", "refresh_token", "current_user", "access_token_exp", "last_activity_ts"):
            st.session_state.pop(k, None)
        st.session_state["auth_message"] = "You were signed out due to inactivity."
        st.switch_page("pages/0Auth.py")
        return False

    exp = st.session_state.get("access_token_exp") or _jwt_exp(st.session_state["access_token"])
    if exp and now > exp - _REFRESH_THRESHOLD:
        refresh = st.session_state.get("refresh_token")
        if refresh:
            try:
                resp = httpx.post(
                    f"{BACKEND_URL}/auth/refresh",
                    json={"refresh_token": refresh},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    st.session_state["access_token"] = data["access_token"]
                    st.session_state["refresh_token"] = data["refresh_token"]
                    st.session_state["access_token_exp"] = now + data.get("expires_in", 1800)
                else:
                    for k in ("access_token", "refresh_token", "current_user", "access_token_exp", "last_activity_ts"):
                        st.session_state.pop(k, None)
                    st.session_state["auth_message"] = "Your session has expired. Please sign in again."
                    st.switch_page("pages/0Auth.py")
                    return False
            except Exception:
                pass  # network error — keep session, will retry on next request

    st.session_state["last_activity_ts"] = now
    return True
