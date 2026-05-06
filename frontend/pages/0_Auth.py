import streamlit as st
import httpx
import os
import time

st.set_page_config(
    page_title="pdf2md",
    page_icon="📄",
    layout="centered"
)

from utils.styles import inject_global_styles
inject_global_styles()

st.markdown("""
<style>
[data-testid="stSidebar"] { display: none !important; }
[data-testid="stSidebarCollapsedControl"] { display: none !important; }
</style>
""", unsafe_allow_html=True)

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


def _store_session(data: dict, token: str) -> None:
    st.session_state["access_token"] = data["access_token"]
    st.session_state["refresh_token"] = data["refresh_token"]
    st.session_state["access_token_exp"] = time.time() + data.get("expires_in", 1800)
    st.session_state["last_activity_ts"] = time.time()
    profile = httpx.get(
        f"{BACKEND_URL}/users/me",
        headers={"Authorization": f"Bearer {data['access_token']}"},
        timeout=10,
    )
    if profile.status_code == 200:
        user = profile.json()
        st.session_state["current_user"] = user
        st.session_state["user_display_name"] = user.get("username", "")


def _redirect_after_login(token: str) -> None:
    try:
        docs = httpx.get(
            f"{BACKEND_URL}/documents/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if docs.status_code == 200 and docs.json():
            st.switch_page("pages/1_My_Documents.py")
    except Exception:
        pass
    st.switch_page("pages/2_Upload.py")


if st.session_state.get("access_token"):
    _redirect_after_login(st.session_state["access_token"])

if msg := st.session_state.pop("auth_message", None):
    st.info(msg)

st.title("pdf2md — Sign in")
st.caption("Your personal Markdown library")
st.divider()

tab_login, tab_register = st.tabs(["Login", "Register"])

# --- TAB: Login ---
with tab_login:
    with st.form("login_form"):
        email = st.text_input("Email", placeholder="user@example.com")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign In", type="primary", use_container_width=True)

    if submitted:
        if not email or not password:
            st.error("Please fill in all fields")
        else:
            with st.spinner("Checking..."):
                try:
                    resp = httpx.post(
                        f"{BACKEND_URL}/auth/login",
                        json={"email": email, "password": password},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        _store_session(data, data["access_token"])
                        _redirect_after_login(data["access_token"])
                    elif resp.status_code == 401:
                        st.error("Invalid email or password")
                    elif resp.status_code == 429:
                        st.error("Too many attempts. Please wait.")
                    else:
                        st.error(f"Login error: {resp.status_code}")
                except httpx.ConnectError:
                    st.error("Could not connect to server")
                except Exception as e:
                    st.error(f"Error: {e}")

# --- TAB: Register ---
with tab_register:
    with st.form("register_form"):
        reg_email = st.text_input("Email", key="reg_email", placeholder="user@example.com")
        reg_username = st.text_input(
            "How should we call you?",
            key="reg_username",
            placeholder="e.g. johndoe",
            help="Letters, digits, _ and - only (min. 3 characters)",
        )
        reg_password = st.text_input("Password", type="password", key="reg_pass",
                                     help="Min. 8 characters, at least 1 letter and 1 digit")
        reg_password2 = st.text_input("Confirm password", type="password", key="reg_pass2")
        reg_submitted = st.form_submit_button("Create account", type="primary", use_container_width=True)

    if reg_submitted:
        if not reg_email or not reg_username or not reg_password:
            st.error("Please fill in all fields")
        elif reg_password != reg_password2:
            st.error("Passwords do not match")
        else:
            with st.spinner("Creating account..."):
                try:
                    resp = httpx.post(
                        f"{BACKEND_URL}/auth/register",
                        json={"email": reg_email, "username": reg_username, "password": reg_password},
                        timeout=10,
                    )
                    if resp.status_code == 201:
                        login_resp = httpx.post(
                            f"{BACKEND_URL}/auth/login",
                            json={"email": reg_email, "password": reg_password},
                            timeout=10,
                        )
                        if login_resp.status_code == 200:
                            data = login_resp.json()
                            _store_session(data, data["access_token"])
                            _redirect_after_login(data["access_token"])
                        else:
                            st.success("✅ Account created! Please sign in.")
                    elif resp.status_code == 409:
                        st.error(resp.json().get("detail", "User already exists"))
                    elif resp.status_code == 400:
                        st.error(resp.json().get("detail", "Validation error"))
                    elif resp.status_code == 429:
                        st.error("Too many registration attempts")
                    else:
                        st.error(f"Error: {resp.status_code} — {resp.text[:200]}")
                except httpx.ConnectError:
                    st.error("Could not connect to server")
                except Exception as e:
                    st.error(f"Error: {e}")
