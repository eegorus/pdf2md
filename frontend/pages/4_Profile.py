import streamlit as st
import httpx
import os

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="pdf2md", page_icon="📄", layout="wide")

from utils.auth import ensure_authenticated
if not ensure_authenticated():
    st.stop()

from utils.styles import inject_global_styles
inject_global_styles()

from components.auth_guard import require_auth, render_sidebar_user

require_auth()
render_sidebar_user()


def _api_patch(path: str, json_data: dict):
    token = st.session_state.get("access_token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.patch(f"{BACKEND_URL}{path}", headers=headers, json=json_data, timeout=15)


def _api_post_raw(path: str, json_data: dict):
    token = st.session_state.get("access_token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.post(f"{BACKEND_URL}{path}", headers=headers, json=json_data, timeout=15)


def _api_get_raw(path: str):
    token = st.session_state.get("access_token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.get(f"{BACKEND_URL}{path}", headers=headers, timeout=15)


try:
    resp = _api_get_raw("/users/me")
    if resp.status_code == 200:
        st.session_state["current_user"] = resp.json()
except Exception:
    pass

current_user = st.session_state.get("current_user", {})

# === Header ===
col_info, col_logout = st.columns([6, 1])
with col_info:
    st.markdown(f"## {current_user.get('username', '')}")
    st.caption(current_user.get("email", ""))
    if current_user.get("is_admin"):
        st.markdown("👑 **Admin**")
with col_logout:
    st.write("")
    if st.button("🚪 Sign out", use_container_width=True):
        try:
            _api_post_raw("/auth/logout", {"refresh_token": st.session_state.get("refresh_token", "")})
        except Exception:
            pass
        st.session_state.clear()
        st.switch_page("pages/0_Auth.py")

st.divider()

tab_account, tab_storage = st.tabs(["👤 Account", "💾 Storage"])

# === TAB 1: ACCOUNT ===
with tab_account:
    st.subheader("Profile")
    with st.form("profile_form"):
        new_username = st.text_input("Username", value=current_user.get("username", ""))
        new_email = st.text_input("Email", value=current_user.get("email", ""))
        save_profile = st.form_submit_button("Save", type="primary")

    if save_profile:
        try:
            resp = _api_patch("/users/me", {"username": new_username, "email": new_email})
            if resp.status_code == 200:
                st.session_state["current_user"] = resp.json()
                st.success("Profile updated")
                st.rerun()
            elif resp.status_code == 409:
                st.error("Email or username already taken")
            else:
                st.error(f"Error: {resp.status_code}")
        except Exception as e:
            st.error(f"Connection error: {e}")

    st.divider()
    st.subheader("Change Password")

    with st.form("password_form"):
        current_password = st.text_input("Current password", type="password")
        new_password = st.text_input("New password", type="password")
        confirm_password = st.text_input("Confirm new password", type="password")
        change_pw = st.form_submit_button("Change password", type="primary")

    if change_pw:
        if new_password != confirm_password:
            st.error("New password and confirmation do not match")
        elif len(new_password) < 8:
            st.error("New password must be at least 8 characters")
        else:
            try:
                resp = _api_post_raw(
                    "/users/me/change-password",
                    {"current_password": current_password, "new_password": new_password},
                )
                if resp.status_code == 200:
                    st.success("Password changed")
                elif resp.status_code == 400:
                    st.error("Incorrect current password")
                else:
                    st.error(f"Error: {resp.status_code}")
            except Exception as e:
                st.error(f"Connection error: {e}")

# === TAB 2: STORAGE ===
with tab_storage:
    try:
        resp = _api_get_raw("/users/me/stats")
        if resp.status_code == 200:
            stats = resp.json()
            c1, c2, c3 = st.columns(3)
            c1.metric("📄 Documents", stats["document_count"])
            c2.metric("💾 Used", f"{stats['used_mb']} MB")
            c3.metric("📦 Quota", f"{stats['quota_mb']} MB")
            st.progress(min(stats["usage_percent"] / 100, 1.0))
            st.caption(f"{stats['used_mb']} MB of {stats['quota_mb']} MB ({stats['usage_percent']}%)")
            if stats["usage_percent"] > 90:
                st.warning("Storage is almost full")
        else:
            st.error(f"Error loading stats: {resp.status_code}")
    except Exception as e:
        st.error(f"Connection error: {e}")

    if st.button("📂 Go to documents"):
        st.switch_page("pages/2_Upload.py")
