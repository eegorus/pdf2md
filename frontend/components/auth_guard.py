import streamlit as st
import httpx
import os

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


def require_auth() -> dict:
    if not st.session_state.get("access_token"):
        st.warning("⚠️ Authentication required. Please sign in.")
        st.page_link("pages/0Auth.py", label="👉 Go to login page", icon="🔑")
        st.stop()
    return st.session_state.get("current_user", {})


def api_get(path: str, params: dict = None) -> dict | None:
    token = st.session_state.get("access_token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = httpx.get(f"{BACKEND_URL}{path}", headers=headers, params=params, timeout=15)
        if resp.status_code == 401:
            _handle_unauthorized()
            return None
        return resp.json()
    except Exception as e:
        st.error(f"Backend connection error: {e}")
        return None


def api_post(path: str, json_data: dict = None, files=None, timeout: int = 60) -> dict | None:
    token = st.session_state.get("access_token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        if files:
            resp = httpx.post(f"{BACKEND_URL}{path}", headers=headers, files=files, timeout=timeout)
        else:
            resp = httpx.post(f"{BACKEND_URL}{path}", headers=headers, json=json_data, timeout=timeout)
        if resp.status_code == 401:
            _handle_unauthorized()
            return None
        return resp.json()
    except Exception as e:
        st.error(f"Backend connection error: {e}")
        return None


def api_delete(path: str) -> dict | None:
    token = st.session_state.get("access_token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = httpx.delete(f"{BACKEND_URL}{path}", headers=headers, timeout=15)
        if resp.status_code == 401:
            _handle_unauthorized()
            return None
        return resp.json()
    except Exception as e:
        st.error(f"Backend connection error: {e}")
        return None


def logout():
    refresh_token = st.session_state.get("refresh_token")
    if refresh_token:
        try:
            httpx.post(f"{BACKEND_URL}/auth/logout", json={"refresh_token": refresh_token}, timeout=5)
        except Exception:
            pass
    for key in ["access_token", "refresh_token", "current_user"]:
        st.session_state.pop(key, None)
    st.rerun()


def _handle_unauthorized():
    for key in ["access_token", "refresh_token", "current_user"]:
        st.session_state.pop(key, None)
    st.warning("Session expired. Please sign in again.")
    st.page_link("pages/0Auth.py", label="👉 Sign in", icon="🔑")
    st.stop()


def render_sidebar_user():
    user = st.session_state.get("current_user", {})
    if user:
        with st.sidebar:
            st.markdown("---")
            st.markdown(f"👤 **{user.get('username', 'User')}**")
            st.caption(user.get('email', ''))
            if st.button("🚪 Sign out", key="sidebar_logout", use_container_width=True):
                logout()
