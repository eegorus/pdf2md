import streamlit as st
import httpx
import os
import time

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Upload — PRMS", layout="wide")

# ── Helpers ───────────────────────────────────────────────────────────────────
def api(method, path, **kw):
    try:
        headers = kw.pop("headers", {})
        token = st.session_state.get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = httpx.request(method, f"{BACKEND_URL}{path}", timeout=60, headers=headers, **kw)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API {path}: {e}")
        return None

# ── Session state defaults ────────────────────────────────────────────────────
for k, v in {
    "upload_doc_id":   None,
    "upload_doc_name": None,
    "upload_stage":    "upload",   # upload | mode | quick_running | quick_done | detail_ready
    "quick_parser":    "pymupdf",
    "quick_api_key":   "",
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Title ─────────────────────────────────────────────────────────────────────

# ── Сайдбар: история загруженных документов ───────────────────────────────
with st.sidebar:
    # Кнопка "Загрузить новый" всегда сверху
    if st.button("＋ Загрузить новый PDF", type="primary", use_container_width=True):
        st.session_state.upload_doc_id   = None
        st.session_state.upload_doc_name = None
        st.session_state.upload_stage    = "upload"
        st.rerun()

    st.markdown("---")
    st.markdown("**Загруженные документы**")

    docs_resp = api("GET", "/documents/")
    docs = (docs_resp or {}).get("documents", [])

    if not docs:
        st.caption("Нет документов")
    else:
        status_icon = {
            "splitting":   "⏳",
            "split_done":  "✂️",
            "processing":  "🔄",
            "layout_done": "✅",
            "ocr_done":    "🎉",
            "error":       "❌",
        }
        status_to_stage = {
            "splitting":   "upload",
            "split_done":  "mode",
            "processing":  "detail_layout",
            "layout_done": "detail_layout",
            "error":       "mode",
        }

        for d in reversed(docs):
            icon  = status_icon.get(d.get("status", ""), "❓")
            fname = d.get("filename", d["doc_id"])
            # Обрезаем имя файла и убираем расширение для компактности
            short = fname.replace(".pdf", "").replace("_", " ")[:22]
            pages = f"{d['page_count']}стр" if d.get("page_count") else ""

            # Текущий документ — выделяем
            is_active = (d["doc_id"] == st.session_state.get("upload_doc_id"))
            btn_label = f"{icon} {short}"
            if pages:
                btn_label += f" · {pages}"

            col_doc, col_del = st.columns([5, 1])
            with col_doc:
                if st.button(
                    btn_label,
                    key=f"sb_{d['doc_id']}",
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                ):
                    st.session_state.upload_doc_id   = d["doc_id"]
                    st.session_state.upload_doc_name = fname
                    st.session_state.upload_stage    = status_to_stage.get(
                        d.get("status", ""), "mode"
                    )
                    st.rerun()
            with col_del:
                if st.button("🗑", key=f"del_{d['doc_id']}", use_container_width=True,
                             help=f"Удалить {fname}"):
                    api("DELETE", f"/documents/{d['doc_id']}")
                    if d["doc_id"] == st.session_state.get("upload_doc_id"):
                        st.session_state.upload_doc_id   = None
                        st.session_state.upload_doc_name = None
                        st.session_state.upload_stage    = "upload"
                    st.rerun()

    if docs:
        st.markdown("---")
        if st.button("🗑 Удалить все", use_container_width=True):
            for d in docs:
                api("DELETE", f"/documents/{d['doc_id']}")
            st.session_state.upload_doc_id   = None
            st.session_state.upload_doc_name = None
            st.session_state.upload_stage    = "upload"
            st.rerun()

st.title("📤 Загрузка PDF")

# ══════════════════════════════════════════════════════════════════════════════
# ЭТАП 0 — загрузка файла
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.upload_stage == "upload":
    st.caption("Выберите PDF файл")
    uploaded = st.file_uploader("", type=["pdf"], label_visibility="collapsed")

    if uploaded:
        if st.button("📎 Загрузить и обработать", type="primary", use_container_width=True):
            with st.spinner("Загружаем файл..."):
                res = api("POST", "/documents/upload",
                          files={"file": (uploaded.name, uploaded.getvalue(), "application/pdf")})
            if not res:
                st.stop()

            doc_id = res["doc_id"]
            st.session_state.upload_doc_id   = doc_id
            st.session_state.upload_doc_name = uploaded.name
            st.success(f"✅ Загружен: `{doc_id}`")

            # Ждём split_done
            with st.spinner("Разбиваем PDF на страницы..."):
                for _ in range(120):
                    time.sleep(1)
                    r = api("GET", f"/processing/{doc_id}/status")
                    if r and r.get("status") == "split_done":
                        break
                else:
                    st.error("❌ PDF не разбился за 120 сек — проверь логи backend")
                    st.stop()

            st.session_state.upload_stage = "mode"
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# ЭТАП 1 — выбор режима
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.upload_stage == "mode":
    doc_id   = st.session_state.upload_doc_id
    doc_name = st.session_state.upload_doc_name

    st.success(f"✅ Загружен: `{doc_id}` — **{doc_name}**")
    st.markdown("---")
    st.markdown("### Выберите режим обработки")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            "<div style='padding:20px;background:#1e293b;border-radius:12px;"
            "border:2px solid #3b82f6;text-align:center'>"
            "<div style='font-size:2em'>⚡</div>"
            "<b style='font-size:1.1em'>Быстрый режим</b><br/><br/>"
            "Весь PDF отправляется парсеру целиком.<br/>"
            "Результат — Markdown через 1-5 минут.<br/><br/>"
            "<i style='color:#94a3b8'>Хорошо для: стандартных отчётов,<br/>"
            "когда структура не важна</i>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown("")
        if st.button("⚡ Быстрый режим", use_container_width=True, type="primary"):
            st.session_state.upload_stage = "quick_setup"
            st.rerun()

    with col2:
        st.markdown(
            "<div style='padding:20px;background:#1e293b;border-radius:12px;"
            "border:2px solid #22c55e;text-align:center'>"
            "<div style='font-size:2em'>🔬</div>"
            "<b style='font-size:1.1em'>Детальный режим</b><br/><br/>"
            "Постраничная разметка блоков с ручной<br/>"
            "проверкой и выбором OCR для каждого типа.<br/><br/>"
            "<i style='color:#94a3b8'>Хорошо для: PRMS-отчётов,<br/>"
            "сложных таблиц и формул</i>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown("")
        if st.button("🔬 Детальный режим", use_container_width=True):
            with st.spinner("Запускаем layout detection..."):
                try:
                    resp = httpx.post(
                        f"{BACKEND_URL}/processing/{doc_id}/start", timeout=10
                    )
                    if resp.status_code == 400 and "layout_already_done" in resp.text:
                        st.session_state["viewer_doc_id"] = doc_id
                        st.switch_page("pages/2_Viewer.py")
                    elif resp.status_code == 200:
                        st.session_state.upload_stage = "detail_layout"
                        st.rerun()
                    else:
                        st.error(f"Ошибка: {resp.text}")
                except Exception as _e:
                    st.error(str(_e))

# ══════════════════════════════════════════════════════════════════════════════
# ЭТАП 2а — быстрый режим: настройка парсера
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.upload_stage == "quick_setup":
    doc_id = st.session_state.upload_doc_id
    st.success(f"✅ `{doc_id}` — {st.session_state.upload_doc_name}")
    st.markdown("---")
    st.markdown("### ⚡ Быстрый режим — выбор парсера")

    parsers_data = api("GET", "/quick/parsers") or []

    # Разбиваем на локальные и облачные
    local_parsers = [p for p in parsers_data if not p["needs_api_key"]]
    cloud_parsers = [p for p in parsers_data if p["needs_api_key"]]

    st.markdown("#### 🖥 Локальные парсеры")
    for p in local_parsers:
        avail  = p["available"]
        badge  = "✅" if avail else "❌ не установлен"
        color  = "#22c55e" if avail else "#475569"
        border = "2px solid #22c55e" if (avail and st.session_state.quick_parser == p["name"]) else f"1px solid {color}"

        col_a, col_b = st.columns([3, 1])
        with col_a:
            st.markdown(
                f"<div style='padding:12px;background:#1e293b;border-radius:8px;border:{border}'>"
                f"<b>{p['label']}</b> {badge}<br/>"
                f"<small style='color:#94a3b8'>{p['description']}</small>"
                f"</div>",
                unsafe_allow_html=True,
            )
        with col_b:
            if avail:
                if st.button("Выбрать", key=f"sel_{p['name']}", use_container_width=True):
                    st.session_state.quick_parser = p["name"]
                    st.session_state.quick_api_key = ""
                    st.rerun()

    # Загружаем статус API-ключей пользователя
    keys_list = api("GET", "/users/me/api-keys") or []
    settings_keys = {k["provider"]: k for k in keys_list}
    pid_map = {
        "gpt4o":      "openai",
        "claude":     "anthropic",
        "llamaparse": "llamaparse",
        "openrouter": "openrouter",
    }

    st.markdown("#### ☁️ Облачные парсеры")
    for p in cloud_parsers:
        avail = p["available"]
        if not avail:
            st.caption(f"❌ {p['label']} — пакет не установлен")
            continue

        provider_id  = pid_map.get(p["name"])
        key_in_settings = bool(
            provider_id and settings_keys.get(provider_id, {}).get("is_set")
        )

        with st.expander(f"{p['label']} — {p['description']}"):
            if key_in_settings:
                st.success("✅ API ключ задан в Settings")
                key_input = ""   # backend возьмёт из settings.json
            else:
                st.warning("⚠️ Ключ не задан — введите или добавьте в Settings")
                key_input = st.text_input(
                    "API Key", type="password",
                    key=f"key_{p['name']}",
                    placeholder="sk-..." if p["name"] == "gpt4o" else "...",
                )

            if st.button("Выбрать", key=f"sel_{p['name']}", use_container_width=True):
                if not key_in_settings and not key_input:
                    st.error("Введите API key или задайте его в Settings")
                else:
                    st.session_state.quick_parser  = p["name"]
                    st.session_state.quick_api_key = key_input  # пусто = backend читает из settings
                    st.rerun()

    st.markdown("---")
    chosen = st.session_state.quick_parser
    st.info(f"Выбран парсер: **{chosen}**")

    col_back, col_run = st.columns(2)
    with col_back:
        if st.button("← Назад", use_container_width=True):
            st.session_state.upload_stage = "mode"
            st.rerun()
    with col_run:
        if st.button("🚀 Запустить", type="primary", use_container_width=True):
            res = api("POST", f"/quick/{doc_id}/run",
                      json={"parser": chosen,
                            "api_key": st.session_state.quick_api_key})
            if res:
                st.session_state.upload_stage = "quick_running"
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# ЭТАП 2б — быстрый режим: ожидание результата
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.upload_stage == "quick_running":
    doc_id = st.session_state.upload_doc_id
    st.info(f"⏳ Парсер **{st.session_state.quick_parser}** обрабатывает документ...")

    status = api("GET", f"/quick/{doc_id}/status") or {}
    state  = status.get("status")

    if state == "done":
        st.session_state.upload_stage = "quick_done"
        st.rerun()
    elif state == "error":
        st.error(f"❌ Ошибка: {status.get('error')}")
        if st.button("← Назад к выбору парсера"):
            st.session_state.upload_stage = "quick_setup"
            st.rerun()
    else:
        st.caption("Страница обновляется автоматически каждые 5 сек...")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Обновить статус", use_container_width=True):
                st.rerun()
        with col2:
            if st.checkbox("Авто-обновление", value=True):
                time.sleep(5)
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# ЭТАП 3а — быстрый режим: результат готов
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.upload_stage == "quick_done":
    doc_id = st.session_state.upload_doc_id
    status = api("GET", f"/quick/{doc_id}/status") or {}
    md     = status.get("markdown", "")

    st.success(f"✅ Готово! Парсер: **{st.session_state.quick_parser}**")
    st.markdown("---")

    col_md, col_new = st.columns(2)
    with col_md:
        if st.button("📄 Открыть в Markdown Viewer", type="primary", use_container_width=True):
            st.session_state["md_viewer_doc_id"] = doc_id
            st.switch_page("pages/4_MarkdownViewer.py")
    with col_new:
        if st.button("📄 Новый документ", use_container_width=True):
            for k in ["upload_doc_id", "upload_doc_name"]:
                st.session_state[k] = None
            st.session_state.upload_stage = "upload"
            st.rerun()

    st.markdown("---")
    col_dl, col_copy = st.columns(2)
    with col_dl:
        st.download_button(
            "⬇️ Скачать Markdown",
            data=md.encode("utf-8"),
            file_name=f"{st.session_state.upload_doc_name or doc_id}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    with col_copy:
        st.button("📋 Скопировать", use_container_width=True,
                  on_click=lambda: st.write(
                      f"<script>navigator.clipboard.writeText(`{md[:100]}`)</script>",
                      unsafe_allow_html=True))

    st.markdown("### Результат")
    tab_preview, tab_raw = st.tabs(["👁 Preview", "📝 Raw Markdown"])
    with tab_preview:
        st.markdown(md)
    with tab_raw:
        st.code(md, language="markdown")

# ══════════════════════════════════════════════════════════════════════════════
# ЭТАП 3б — детальный режим: layout detection запущен
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.upload_stage == "detail_layout":
    doc_id = st.session_state.upload_doc_id
    st.info("🔬 Детальный режим — запущен Layout Detection")

    progress_bar = st.progress(0, text="Определяем блоки...")
    for _ in range(120):
        time.sleep(5)
        s = api("GET", f"/processing/{doc_id}/status") or {}
        status = s.get("status")
        if status == "layout_done":
            progress_bar.progress(1.0, text=f"✅ Найдено блоков: {s.get('total_blocks', 0)}")
            st.success("✅ Разметка блоков готова! Переходи в **Viewer** для проверки.")
            if st.button("→ Открыть Viewer", type="primary", use_container_width=True):
                st.session_state["viewer_doc_id"] = doc_id
                st.switch_page("pages/2_Viewer.py")
            break
        elif status == "error":
            st.error(f"❌ Ошибка layout detection: {s.get('error', '')}")
            break
        else:
            pct = min(0.9, (_ + 1) / 120)
            progress_bar.progress(pct, text=f"Статус: {status}...")
    else:
        st.warning("⚠️ Layout detection занимает больше времени — проверь Viewer позже")
