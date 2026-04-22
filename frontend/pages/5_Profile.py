import streamlit as st
import httpx
import os

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Профиль — PRMS", page_icon="👤", layout="wide")

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


# Refresh current_user from backend on page load
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
    if st.button("🚪 Выйти", use_container_width=True):
        try:
            _api_post_raw("/auth/logout", {"refresh_token": st.session_state.get("refresh_token", "")})
        except Exception:
            pass
        st.session_state.clear()
        st.switch_page("pages/0Auth.py")

st.divider()

tab_account, tab_storage = st.tabs(["👤 Аккаунт", "💾 Хранилище"])

# === TAB 1: ACCOUNT ===
with tab_account:
    st.subheader("Данные профиля")
    with st.form("profile_form"):
        new_username = st.text_input("Имя пользователя", value=current_user.get("username", ""))
        new_email = st.text_input("Email", value=current_user.get("email", ""))
        save_profile = st.form_submit_button("Сохранить", type="primary")

    if save_profile:
        try:
            resp = _api_patch("/users/me", {"username": new_username, "email": new_email})
            if resp.status_code == 200:
                st.session_state["current_user"] = resp.json()
                st.success("Профиль обновлён")
                st.rerun()
            elif resp.status_code == 409:
                st.error("Email или username уже занят")
            else:
                st.error(f"Ошибка: {resp.status_code}")
        except Exception as e:
            st.error(f"Ошибка соединения: {e}")

    st.divider()
    st.subheader("Смена пароля")

    with st.form("password_form"):
        current_password = st.text_input("Текущий пароль", type="password")
        new_password = st.text_input("Новый пароль", type="password")
        confirm_password = st.text_input("Подтвердить новый пароль", type="password")
        change_pw = st.form_submit_button("Сменить пароль", type="primary")

    if change_pw:
        if new_password != confirm_password:
            st.error("Новый пароль и подтверждение не совпадают")
        elif len(new_password) < 8:
            st.error("Новый пароль должен содержать минимум 8 символов")
        else:
            try:
                resp = _api_post_raw(
                    "/users/me/change-password",
                    {"current_password": current_password, "new_password": new_password},
                )
                if resp.status_code == 200:
                    st.success("Пароль изменён")
                elif resp.status_code == 400:
                    st.error("Неверный текущий пароль")
                else:
                    st.error(f"Ошибка: {resp.status_code}")
            except Exception as e:
                st.error(f"Ошибка соединения: {e}")

# === TAB 2: STORAGE ===
with tab_storage:
    try:
        resp = _api_get_raw("/users/me/stats")
        if resp.status_code == 200:
            stats = resp.json()
            c1, c2, c3 = st.columns(3)
            c1.metric("📄 Документов", stats["document_count"])
            c2.metric("💾 Использовано", f"{stats['used_mb']} MB")
            c3.metric("📦 Квота", f"{stats['quota_mb']} MB")
            st.progress(min(stats["usage_percent"] / 100, 1.0))
            st.caption(f"{stats['used_mb']} MB из {stats['quota_mb']} MB ({stats['usage_percent']}%)")
            if stats["usage_percent"] > 90:
                st.warning("Хранилище почти заполнено")
        else:
            st.error(f"Ошибка загрузки статистики: {resp.status_code}")
    except Exception as e:
        st.error(f"Ошибка соединения: {e}")

    if st.button("📂 Перейти к документам"):
        st.switch_page("pages/1_Upload.py")
