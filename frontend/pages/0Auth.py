import streamlit as st
import httpx
import os

st.set_page_config(
    page_title="PRMS — Вход",
    page_icon="🔑",
    layout="centered"
)

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

# Если уже авторизован — на главную
if st.session_state.get("access_token"):
    st.switch_page("pages/1_Upload.py")

st.title("🔑 PRMS — Вход в систему")
st.caption("PDF Recognition & Markdown System")
st.divider()

tab_login, tab_register = st.tabs(["Войти", "Регистрация"])

# --- TAB: Login ---
with tab_login:
    with st.form("login_form"):
        email = st.text_input("Email", placeholder="user@example.com")
        password = st.text_input("Пароль", type="password")
        submitted = st.form_submit_button("Войти", type="primary", use_container_width=True)

    if submitted:
        if not email or not password:
            st.error("Заполните все поля")
        else:
            with st.spinner("Проверяем..."):
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

                        st.success("Добро пожаловать!")
                        st.switch_page("pages/1_Upload.py")
                    elif resp.status_code == 401:
                        st.error("Неверный email или пароль")
                    elif resp.status_code == 429:
                        st.error("Слишком много попыток. Подождите немного.")
                    else:
                        st.error(f"Ошибка входа: {resp.status_code}")
                except httpx.ConnectError:
                    st.error("Не удалось подключиться к серверу")
                except Exception as e:
                    st.error(f"Ошибка: {e}")

# --- TAB: Register ---
with tab_register:
    with st.form("register_form"):
        reg_email = st.text_input("Email", key="reg_email", placeholder="user@example.com")
        reg_username = st.text_input("Username", key="reg_username", placeholder="username (только буквы, цифры, _ -)")
        reg_password = st.text_input("Пароль", type="password", key="reg_pass",
                                     help="Минимум 8 символов, хотя бы 1 буква и 1 цифра")
        reg_password2 = st.text_input("Повторите пароль", type="password", key="reg_pass2")
        reg_submitted = st.form_submit_button("Зарегистрироваться", type="primary", use_container_width=True)

    if reg_submitted:
        if not reg_email or not reg_username or not reg_password:
            st.error("Заполните все поля")
        elif reg_password != reg_password2:
            st.error("Пароли не совпадают")
        else:
            with st.spinner("Создаём аккаунт..."):
                try:
                    resp = httpx.post(
                        f"{BACKEND_URL}/auth/register",
                        json={"email": reg_email, "username": reg_username, "password": reg_password},
                        timeout=10,
                    )
                    if resp.status_code == 201:
                        st.success("✅ Аккаунт создан! Войдите на вкладке 'Войти'")
                    elif resp.status_code == 409:
                        st.error(resp.json().get("detail", "Пользователь уже существует"))
                    elif resp.status_code == 400:
                        st.error(resp.json().get("detail", "Ошибка валидации"))
                    elif resp.status_code == 429:
                        st.error("Слишком много попыток регистрации")
                    else:
                        st.error(f"Ошибка: {resp.status_code} — {resp.text[:200]}")
                except httpx.ConnectError:
                    st.error("Не удалось подключиться к серверу")
                except Exception as e:
                    st.error(f"Ошибка: {e}")
