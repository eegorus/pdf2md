import streamlit as st
import httpx
import os

st.set_page_config(
    page_title="PRMS — Login",
    page_icon="🔑",
    layout="centered"
)

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

if st.session_state.get("access_token"):
    st.switch_page("pages/1_Upload.py")

st.title("🔑 PRMS — Sign In")
st.caption("PDF Recognition & Markdown System")
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
                        st.session_state["access_token"] = data["access_token"]
                        st.session_state["refresh_token"] = data["refresh_token"]

                        profile_resp = httpx.get(
                            f"{BACKEND_URL}/users/me",
                            headers={"Authorization": f"Bearer {data['access_token']}"},
                            timeout=10,
                        )
                        if profile_resp.status_code == 200:
                            st.session_state["current_user"] = profile_resp.json()

                        st.success("Welcome!")
                        st.switch_page("pages/1_Upload.py")
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
        reg_username = st.text_input("Username", key="reg_username", placeholder="letters, digits, _ - only")
        reg_password = st.text_input("Password", type="password", key="reg_pass",
                                     help="Min. 8 characters, at least 1 letter and 1 digit")
        reg_password2 = st.text_input("Confirm password", type="password", key="reg_pass2")
        reg_submitted = st.form_submit_button("Register", type="primary", use_container_width=True)

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
                        st.success("✅ Account created! Sign in on the Login tab")
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
