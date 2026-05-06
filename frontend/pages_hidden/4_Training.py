import streamlit as st
import httpx
import os
import time

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(
    page_title="pdf2md",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🎓 Training")
st.caption("Управление fine-tuning моделей на накопленных парах")

# ── Helpers ───────────────────────────────────────────────────────────────────
def api(method, path, **kw):
    try:
        r = httpx.request(method, f"{BACKEND_URL}{path}", timeout=30, **kw)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API {path}: {e}")
        return None


@st.cache_data(ttl=5)
def fetch_stats():
    return api("GET", "/training/stats") or {}


@st.cache_data(ttl=5)
def fetch_ft_status():
    return api("GET", "/training/status") or {}


@st.cache_data(ttl=30)
def fetch_pairs(block_type=None, limit=20):
    url = f"/training/pairs?limit={limit}"
    if block_type:
        url += f"&block_type={block_type}"
    return api("GET", url) or {"pairs": [], "total": 0}


# ── Layout ────────────────────────────────────────────────────────────────────
col_left, col_right = st.columns([1.2, 2])

# ══════════════════════════════════════════════════════════════════════════════
# LEFT — статистика датасета
# ══════════════════════════════════════════════════════════════════════════════
with col_left:
    st.markdown("### 📊 Датасет")

    stats = fetch_stats()
    total   = stats.get("total_pairs", 0)
    min_req = stats.get("min_pairs_for_finetune", 50)
    ready   = stats.get("ready_for_finetune", False)
    by_type = stats.get("by_type", {})

    # Прогресс-бар накопления пар
    progress = min(1.0, total / min_req) if min_req else 0
    st.metric("Накоплено пар", total, help=f"Минимум для запуска: {min_req}")
    st.progress(progress, text=f"{total} / {min_req} пар")

    if ready:
        st.success("✅ Готово к обучению!")
    else:
        st.info(f"📋 Нужно ещё {min_req - total} пар")

    st.markdown("**По типам блоков:**")
    type_icons = {"text": "📝", "table": "📊", "formula": "➗", "figure": "🖼"}
    for btype, cnt in sorted(by_type.items()):
        icon = type_icons.get(btype, "⚪")
        bar  = "█" * min(cnt, 20) + "░" * max(0, 20 - min(cnt, 20))
        st.caption(f"{icon} {btype:8s} {cnt:4d}  `{bar}`")

    st.markdown("---")
    st.markdown("### 🗂 Версии моделей")

    ft_status = fetch_ft_status()
    versions  = ft_status.get("versions", [])
    active    = ft_status.get("active_version")

    if versions:
        for v in reversed(versions):
            is_active = v == active
            badge = " 🟢 **active**" if is_active else ""
            col_v1, col_v2 = st.columns([2, 1])
            with col_v1:
                st.markdown(f"`{v}`{badge}")
            with col_v2:
                if not is_active:
                    if st.button("Активировать", key=f"sw_{v}", use_container_width=True):
                        res = api("POST", "/training/switch-model", json={"version": v})
                        if res:
                            st.success(f"Активна: {v}")
                            fetch_ft_status.clear()
                            st.rerun()
    else:
        st.caption("Нет обученных версий")

    if active:
        st.markdown(f"**Активная модель:** `{active}`")

# ══════════════════════════════════════════════════════════════════════════════
# RIGHT — запуск обучения + статус + лог
# ══════════════════════════════════════════════════════════════════════════════
with col_right:
    ft = fetch_ft_status()
    ft_state = ft.get("status", "idle")

    # ── Панель запуска ────────────────────────────────────────────────────
    st.markdown("### 🚀 Запуск обучения")

    state_colors = {
        "idle":     ("⚪", "Готов к запуску"),
        "starting": ("🟡", "Инициализация..."),
        "running":  ("🟠", "Обучение идёт..."),
        "done":     ("🟢", "Завершено успешно"),
        "error":    ("🔴", "Ошибка"),
    }
    icon, label = state_colors.get(ft_state, ("⚪", ft_state))
    st.markdown(
        f"<div style='padding:10px 14px;background:#1e293b;border-radius:8px;"
        f"border-left:4px solid {'#22c55e' if ft_state=='done' else '#f59e0b' if ft_state=='running' else '#64748b'}'>"
        f"<b>Статус:</b> {icon} {label}"
        + (f"  |  <b>Версия:</b> <code>{ft.get('version','?')}</code>" if ft.get('version') else "")
        + "</div>",
        unsafe_allow_html=True,
    )

    st.markdown("")

    if ft_state not in ("running", "starting"):
        with st.expander("⚙️ Параметры", expanded=True):
            import datetime
            default_ver = f"v{datetime.datetime.now().strftime('%Y%m%d_%H%M')}"
            new_version = st.text_input("Версия", value=default_ver, key="ft_version")
            epochs      = st.slider("Эпох обучения", 1, 10, 3, key="ft_epochs")
            st.caption(
                "**RTX 4090:** ~30 мин на 100 пар / 3 эпохи  \n"
                "Во время обучения backend останавливается — API будет недоступен"
            )

        col_btn1, col_btn2 = st.columns([2, 1])
        with col_btn1:
            disabled = not ready
            if st.button(
                "🎓 Запустить Fine-Tuning",
                use_container_width=True,
                type="primary",
                disabled=disabled,
                help=f"Нужно минимум {min_req} пар" if not ready else "Запустить обучение",
            ):
                res = api("POST", "/training/start",
                          json={"version": new_version, "epochs": epochs})
                if res:
                    st.success(f"✅ Запущено! Версия: {res.get('version')}, "
                               f"пар: {res.get('pairs_count')}, эпох: {res.get('epochs')}")
                    fetch_ft_status.clear()
                    st.rerun()
        with col_btn2:
            if st.button("🔄 Обновить", use_container_width=True):
                fetch_ft_status.clear()
                fetch_stats.clear()
                st.rerun()
    else:
        # Обучение идёт — автообновление каждые 5 сек
        st.info("⏳ Обучение в процессе... страница обновляется автоматически")
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            if st.button("🔄 Обновить статус", use_container_width=True):
                fetch_ft_status.clear()
                st.rerun()
        with col_b2:
            auto = st.checkbox("Авто-обновление (5 сек)", value=True)
        if auto:
            time.sleep(5)
            fetch_ft_status.clear()
            st.rerun()

    # ── Лог обучения ──────────────────────────────────────────────────────
    log = ft.get("log", "")
    if log:
        st.markdown("### 📋 Лог")
        st.code(log, language=None)

    # ── Последние пары ────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🗃 Последние training pairs")

    filter_type = st.selectbox(
        "Тип блока",
        ["все", "text", "table", "formula", "figure"],
        key="pairs_filter",
        label_visibility="collapsed",
    )
    pairs_data = fetch_pairs(
        block_type=None if filter_type == "все" else filter_type,
        limit=10,
    )
    pairs = pairs_data.get("pairs", [])
    total_pairs = pairs_data.get("total", 0)
    st.caption(f"Показано: {len(pairs)} из {total_pairs}")

    for p in reversed(pairs):  # последние сверху
        btype  = p.get("block_type", "?")
        pid    = p.get("pair_id", "?")[:20]
        page   = p.get("source_page", "?")
        icon   = type_icons.get(btype, "⚪")
        orig   = (p.get("local_model_output") or "")[:120]
        target = (p.get("target_output") or "")[:120]
        with st.expander(f"{icon} {btype} · стр.{page} · `{pid}`", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                st.caption("🤖 Модель выдала:")
                st.text(orig + ("..." if len(p.get("local_model_output","")) > 120 else ""))
            with c2:
                st.caption("✅ Правильный ответ:")
                st.text(target + ("..." if len(p.get("target_output","")) > 120 else ""))
