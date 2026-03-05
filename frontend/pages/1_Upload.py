import streamlit as st
import httpx
import time
import os

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Upload — PRMS", layout="wide")
st.title("📤 Загрузка PDF")

# ─── ЗАГРУЗКА ФАЙЛА ───────────────────────────────────────────────────────────
uploaded = st.file_uploader(
    "Выберите PDF файл",
    type=["pdf"],
    help="Максимальный размер: 100 MB"
)

if uploaded:
    st.info(f"📄 {uploaded.name} · {uploaded.size / 1024 / 1024:.1f} MB")

    if st.button("🚀 Загрузить и обработать", type="primary", use_container_width=True):
        # 1. Upload
        with st.spinner("Загружаем PDF..."):
            try:
                r = httpx.post(
                    f"{BACKEND_URL}/documents/upload",
                    files={"file": (uploaded.name, uploaded.getvalue(), "application/pdf")},
                    timeout=60,
                )
                r.raise_for_status()
                data = r.json()
                doc_id = data["doc_id"]
                st.session_state.current_doc_id = doc_id
                st.session_state.current_doc_name = uploaded.name
                st.success(f"✅ Загружен: `{doc_id}`")
            except Exception as e:
                st.error(f"❌ Ошибка загрузки: {e}")
                st.stop()

        # 2. Ждём split_done — сплит идёт в фоне после upload
        with st.spinner("Разбиваем PDF на страницы..."):
            for _ in range(120):  # max 120 сек
                import time as _time
                _time.sleep(1)
                try:
                    r = httpx.get(f"{BACKEND_URL}/processing/{doc_id}/status", timeout=5)
                    if r.json().get("status") == "split_done":
                        break
                except Exception:
                    pass
            else:
                st.error("❌ PDF не разбился на страницы за 120 сек — проверь логи backend")
                st.stop()

        # 3. Layout detection
        with st.spinner("Запускаем layout detection..."):
            try:
                r = httpx.post(
                    f"{BACKEND_URL}/processing/{doc_id}/start",
                    timeout=10,
                )
                r.raise_for_status()
            except Exception as e:
                st.error(f"❌ Ошибка запуска pipeline: {e}")
                st.stop()

        # 4. Polling layout detection
        st.markdown("**Layout Detection**")
        progress_bar = st.progress(0, text="Определяем блоки...")
        for _ in range(60):  # max 5 минут
            time.sleep(5)
            try:
                r = httpx.get(f"{BACKEND_URL}/processing/{doc_id}/status", timeout=5)
                s = r.json()
                status = s.get("status")
                if status == "layout_done":
                    total = s.get("total_blocks", 0)
                    progress_bar.progress(1.0, text=f"✅ Найдено блоков: {total}")
                    break
                elif status == "error":
                    st.error("❌ Layout detection завершился с ошибкой")
                    st.stop()
                else:
                    progress_bar.progress(0.3, text=f"Статус: {status}...")
            except Exception:
                pass
        else:
            st.warning("⚠️ Layout detection занимает больше времени, проверь статус позже")
            st.stop()

        # 4. OCR
        with st.spinner("Запускаем OCR..."):
            try:
                r = httpx.post(
                    f"{BACKEND_URL}/processing/{doc_id}/ocr",
                    timeout=10,
                )
                r.raise_for_status()
            except Exception as e:
                st.error(f"❌ Ошибка запуска OCR: {e}")
                st.stop()

        # 5. Polling OCR с прогрессом
        st.markdown("**OCR Pipeline**")
        ocr_bar = st.progress(0, text="Запускаем OCR...")
        status_placeholder = st.empty()

        for _ in range(120):  # max 10 минут
            time.sleep(5)
            try:
                r = httpx.get(f"{BACKEND_URL}/processing/{doc_id}/status", timeout=5)
                s = r.json()
                status = s.get("status")
                total = s.get("total_blocks", 1)
                processed = s.get("processed_blocks", 0)
                pct = s.get("progress_pct", 0) / 100

                if status == "ocr_done":
                    ocr_bar.progress(1.0, text=f"✅ OCR завершён: {total}/{total} блоков")
                    break
                elif status == "error":
                    st.error("❌ OCR завершился с ошибкой")
                    st.stop()
                else:
                    ocr_bar.progress(
                        max(pct, 0.01),
                        text=f"Обработано: {processed}/{total} блоков"
                    )
                    status_placeholder.caption(f"Статус: `{status}`")
            except Exception:
                pass
        else:
            st.warning("⚠️ OCR занимает больше времени, проверь статус позже")

        # 6. Итог
        st.success("🎉 Документ полностью обработан!")
        r = httpx.get(f"{BACKEND_URL}/processing/{doc_id}/status", timeout=5)
        s = r.json()

        col1, col2, col3 = st.columns(3)
        col1.metric("Страниц", s.get("page_count"))
        col2.metric("Всего блоков", s.get("total_blocks"))
        col3.metric("Обработано", s.get("processed_blocks"))

        st.markdown("### Экспорт результатов")
        c1, c2, c3 = st.columns(3)
        for fmt, col, icon in [("markdown", c1, "📝"), ("json", c2, "🗂"), ("csv", c3, "📊")]:
            with col:
                if st.button(f"{icon} {fmt.upper()}", use_container_width=True):
                    r = httpx.post(
                        f"{BACKEND_URL}/processing/{doc_id}/export?format={fmt}",
                        timeout=30,
                    )
                    if r.status_code == 200:
                        st.success(f"✅ {r.json().get('message')}")
                    else:
                        st.error("❌ Ошибка экспорта")

# ─── СПИСОК ДОКУМЕНТОВ ───────────────────────────────────────────────────────
st.markdown("---")
st.markdown("### 📁 Загруженные документы")

try:
    r = httpx.get(f"{BACKEND_URL}/documents/", timeout=5)
    docs = r.json().get("documents", [])
    if not docs:
        st.caption("Нет загруженных документов")
    else:
        current = st.session_state.get("current_doc_id")
        if current:
            st.success(f"✅ Выбран: `{st.session_state.get('current_doc_name', current)}`")

        for doc in docs:
            doc_id  = doc["doc_id"]
            filename = doc.get("filename", doc_id)
            status   = doc.get("status", "—")
            is_current = (doc_id == current)

            status_color = {
                "ocr_done":    "🟢",
                "layout_done": "🟡",
                "uploaded":    "🔵",
                "error":       "🔴",
            }.get(status, "⚪")

            col1, col2, col3 = st.columns([4, 1, 1])
            with col1:
                prefix = "▶ " if is_current else "   "
                st.markdown(f"{prefix}📄 **{filename}**")
                st.caption(f"`{doc_id}`")
            with col2:
                st.markdown(f"{status_color} `{status}`")
            with col3:
                if st.button(
                    "✅ Выбран" if is_current else "Открыть",
                    key=f"open_{doc_id}",
                    use_container_width=True,
                    disabled=is_current,
                ):
                    st.session_state.current_doc_id  = doc_id
                    st.session_state.current_doc_name = filename
                    st.rerun()
except Exception as e:
    st.warning(f"Не удалось загрузить список: {e}")
