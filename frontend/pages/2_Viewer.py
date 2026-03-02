import streamlit as st
import streamlit.components.v1 as components
import httpx
import os
from PIL import Image, ImageDraw, ImageFont
from streamlit_image_coordinates import streamlit_image_coordinates
import io
import json

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(
    page_title="Viewer — PRMS",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── ЦВЕТА БЛОКОВ ─────────────────────────────────────────────────────────────
BLOCK_COLORS = {
    "text":    (59,  130, 246),   # синий
    "table":   (234, 88,  12),    # оранжевый
    "formula": (22,  163, 74),    # зелёный
    "figure":  (147, 51,  234),   # фиолетовый
}
STATUS_COLORS = {
    "needs_review": (239, 68,  68),   # красный контур
    "accepted":     (34,  197, 94),   # зелёный контур
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def fetch_documents():
    try:
        r = httpx.get(f"{BACKEND_URL}/documents/", timeout=5)
        return r.json().get("documents", [])
    except Exception:
        return []

@st.cache_data(ttl=10)
def fetch_blocks(doc_id: str):
    try:
        r = httpx.get(f"{BACKEND_URL}/processing/{doc_id}/results", timeout=10)
        return r.json().get("blocks", [])
    except Exception:
        return []

@st.cache_data(ttl=60)
def fetch_page_image(doc_id: str, page_num: int, max_width: int = 680):
    """Возвращает (resized_image, orig_w, orig_h) или (None, 0, 0)."""
    try:
        r = httpx.get(
            f"{BACKEND_URL}/documents/{doc_id}/page-image/{page_num}",
            timeout=15,
        )
        if r.status_code == 200:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            orig_w, orig_h = img.size   # ← запоминаем ДО ресайза
            if img.width > max_width:
                ratio = max_width / img.width
                new_h = int(img.height * ratio)
                img = img.resize((max_width, new_h), Image.LANCZOS)
            return img, orig_w, orig_h
    except Exception:
        pass
    return None, 0, 0

def draw_blocks_on_image(
    image: Image.Image,
    blocks: list,
    page_num: int,
    show_types: set,
    selected_block_id: str | None,
    bbox_scale: float = 1.0,
) -> Image.Image:
    """Рисуем цветные bbox поверх изображения страницы."""
    img = image.copy()
    draw = ImageDraw.Draw(img, "RGBA")

    page_blocks = [b for b in blocks if b.get("page_num") == page_num]

    for block in page_blocks:
        btype = block.get("block_type", "text")
        if btype not in show_types:
            continue

        bbox  = block.get("bbox", [])
        if len(bbox) != 4:
            continue
        # Масштабируем bbox под ресайзнутое изображение
        x1 = int(bbox[0] * bbox_scale)
        y1 = int(bbox[1] * bbox_scale)
        x2 = int(bbox[2] * bbox_scale)
        y2 = int(bbox[3] * bbox_scale)

        # Цвет по типу
        rgb = BLOCK_COLORS.get(btype, (128, 128, 128))

        # Если needs_review — красный контур, если selected — жёлтый
        bid = block.get("block_id")
        status = block.get("status", "")

        if bid == selected_block_id:
            outline = (255, 220, 0, 255)   # жёлтый
            width = 4
        elif status == "needs_review":
            outline = (239, 68, 68, 255)   # красный
            width = 3
        elif status == "accepted":
            outline = (34, 197, 94, 255)   # зелёный
            width = 2
        else:
            outline = (*rgb, 200)
            width = 2

        # Полупрозрачная заливка
        draw.rectangle([x1, y1, x2, y2], fill=(*rgb, 30), outline=outline, width=width)

        # Метка типа
        label = f"{btype[0].upper()}"
        draw.rectangle([x1, y1, x1 + 18, y1 + 16], fill=(*rgb, 220))
        draw.text((x1 + 2, y1 + 1), label, fill=(255, 255, 255))

    return img

def image_to_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# ─── SESSION STATE ─────────────────────────────────────────────────────────────
if "viewer_page" not in st.session_state:
    st.session_state.viewer_page = 1
if "viewer_selected_block" not in st.session_state:
    st.session_state.viewer_selected_block = None
if "viewer_edit_mode" not in st.session_state:
    st.session_state.viewer_edit_mode = False

# ─── ЗАГОЛОВОК ────────────────────────────────────────────────────────────────
st.title("🔍 Document Viewer")
st.markdown("""
<style>
    /* Ограничиваем высоту viewer колонки */
    [data-testid="stImage"] img,
    [data-testid="stImageContainer"] img {
        max-height: 75vh !important;
        width: auto !important;
        object-fit: contain;
    }
</style>
""", unsafe_allow_html=True)

# ─── НЕТ ДОКУМЕНТА ────────────────────────────────────────────────────────────
doc_id = st.session_state.get("current_doc_id")
if not doc_id:
    st.info("👆 Выберите документ на странице **Upload**")
    st.stop()

# ─── ПОЛУЧАЕМ ДАННЫЕ ──────────────────────────────────────────────────────────
all_blocks = fetch_blocks(doc_id)
doc_name   = st.session_state.get("current_doc_name", doc_id)

try:
    r = httpx.get(f"{BACKEND_URL}/processing/{doc_id}/status", timeout=5)
    doc_status = r.json()
    total_pages = doc_status.get("page_count", 1)
except Exception:
    total_pages = 1

# ─── ТРИ КОЛОНКИ ──────────────────────────────────────────────────────────────
col_left, col_main, col_right = st.columns([0.7, 3, 1.2])

# ══════════════════════════════════════════════════════════════════════════════
# LEFT SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
with col_left:
    st.markdown("### 📁 Документы")

    docs = fetch_documents()
    for doc in docs:
        did      = doc["doc_id"]
        dname    = doc.get("filename", did)[:28]
        is_cur   = did == doc_id
        if st.button(
            f"{'▶ ' if is_cur else ''}{dname}",
            key=f"doc_{did}",
            use_container_width=True,
            type="primary" if is_cur else "secondary",
        ):
            st.session_state.current_doc_id   = did
            st.session_state.current_doc_name = doc.get("filename", did)
            st.session_state.viewer_page      = 1
            st.session_state.viewer_selected_block = None
            fetch_blocks.clear()
            st.rerun()

    st.markdown("---")
    st.markdown("### 🎨 Фильтры")

    show_text    = st.checkbox("📝 Text",    value=True)
    show_table   = st.checkbox("📊 Table",   value=True)
    show_formula = st.checkbox("➗ Formula", value=True)
    show_figure  = st.checkbox("🖼 Figure",  value=True)

    show_types = set()
    if show_text:    show_types.add("text")
    if show_table:   show_types.add("table")
    if show_formula: show_types.add("formula")
    if show_figure:  show_types.add("figure")

    st.markdown("---")
    st.markdown("### 📊 Документ")
    st.caption(f"📄 {doc_name[:30]}")

    page_blocks = [b for b in all_blocks if b.get("page_num") == st.session_state.viewer_page]
    from collections import Counter
    type_counts = Counter(b.get("block_type") for b in page_blocks)

    st.metric("Страниц", total_pages)
    st.metric("Блоков всего", len(all_blocks))
    st.markdown("**На странице:**")
    for btype, count in sorted(type_counts.items()):
        color_emoji = {"text": "🔵", "table": "🟠", "formula": "🟢", "figure": "🟣"}.get(btype, "⚪")
        st.caption(f"{color_emoji} {btype}: {count}")

    needs_review_count = sum(1 for b in all_blocks if b.get("status") == "needs_review")
    if needs_review_count:
        st.markdown(f"🔴 **needs_review: {needs_review_count}**")

# ══════════════════════════════════════════════════════════════════════════════
# MAIN VIEWER
# ══════════════════════════════════════════════════════════════════════════════
with col_main:
    # Навигация по страницам
    nav1, nav2, nav3, nav4, nav5 = st.columns([1, 1, 2, 1, 1])
    with nav1:
        if st.button("⏮", use_container_width=True):
            st.session_state.viewer_page = 1
            st.session_state.viewer_selected_block = None
    with nav2:
        if st.button("◀", use_container_width=True) and st.session_state.viewer_page > 1:
            st.session_state.viewer_page -= 1
            st.session_state.viewer_selected_block = None
    with nav3:
        new_page = st.number_input(
            "Страница", min_value=1, max_value=total_pages,
            value=st.session_state.viewer_page,
            label_visibility="collapsed",
        )
        if new_page != st.session_state.viewer_page:
            st.session_state.viewer_page = new_page
            st.session_state.viewer_selected_block = None
    with nav4:
        if st.button("▶", use_container_width=True) and st.session_state.viewer_page < total_pages:
            st.session_state.viewer_page += 1
            st.session_state.viewer_selected_block = None
    with nav5:
        if st.button("⏭", use_container_width=True):
            st.session_state.viewer_page = total_pages
            st.session_state.viewer_selected_block = None

    st.caption(f"Страница {st.session_state.viewer_page} / {total_pages}")

    # Загружаем и рендерим изображение
    page_img, orig_w, orig_h = fetch_page_image(doc_id, st.session_state.viewer_page)

    if page_img:
        # Считаем scale bbox оригинал → ресайзнутое изображение
        # orig_w берём реальный (portrait vs landscape — разный!)
        display_w = page_img.width
        bbox_scale = display_w / orig_w if orig_w else 1.0

        annotated = draw_blocks_on_image(
            page_img,
            all_blocks,
            st.session_state.viewer_page,
            show_types,
            st.session_state.viewer_selected_block,
            bbox_scale=bbox_scale,
        )
        img_w, img_h = annotated.size

        # Кликабельное изображение с фиксированной шириной
        coords = streamlit_image_coordinates(
            annotated,
            use_column_width="always",
            key=f"viewer_{doc_id}_{st.session_state.viewer_page}",
        )

        # Обрабатываем клик
        if coords:
            # use_column_width растягивает изображение — пересчитываем координаты
            rendered_w = coords.get("width", img_w)
            render_scale = img_w / rendered_w if rendered_w else 1.0
            cx = int(coords["x"] * render_scale / bbox_scale)
            cy = int(coords["y"] * render_scale / bbox_scale)

            # Ищем блок по bbox
            clicked_block = None
            page_blocks_all = [
                b for b in all_blocks
                if b.get("page_num") == st.session_state.viewer_page
                and b.get("block_type") in show_types
            ]
            for b in page_blocks_all:
                bbox = b.get("bbox", [])
                if len(bbox) == 4:
                    x1, y1, x2, y2 = bbox
                    if x1 <= cx <= x2 and y1 <= cy <= y2:
                        clicked_block = b["block_id"]
                        break

            if clicked_block and clicked_block != st.session_state.viewer_selected_block:
                st.session_state.viewer_selected_block = clicked_block
                st.session_state.viewer_edit_mode = False
                st.rerun()
    else:
        st.warning("Изображение страницы недоступно")

    # Список блоков на странице для выбора
    st.markdown("---")
    st.markdown("**Блоки на странице** (кликни для выбора):")

    page_blocks_filtered = [
        b for b in all_blocks
        if b.get("page_num") == st.session_state.viewer_page
        and b.get("block_type") in show_types
    ]

    if page_blocks_filtered:
        cols_per_row = 4
        rows = [page_blocks_filtered[i:i+cols_per_row]
                for i in range(0, len(page_blocks_filtered), cols_per_row)]
        for row in rows:
            rcols = st.columns(cols_per_row)
            for col, block in zip(rcols, row):
                bid    = block["block_id"]
                btype  = block.get("block_type", "?")
                bstatus = block.get("status", "")
                is_sel  = bid == st.session_state.viewer_selected_block

                status_icon = {
                    "needs_review": "🔴",
                    "accepted":     "✅",
                    "ocr_done":     "🔵",
                    "error":        "❌",
                }.get(bstatus, "⚪")

                type_icon = {"text": "📝", "table": "📊", "formula": "➗", "figure": "🖼"}.get(btype, "?")

                with col:
                    if st.button(
                        f"{'▶' if is_sel else ''}{type_icon}{status_icon}",
                        key=f"sel_{bid}",
                        use_container_width=True,
                        help=f"{bid}\n{btype} · {bstatus}",
                    ):
                        if is_sel:
                            st.session_state.viewer_selected_block = None
                        else:
                            st.session_state.viewer_selected_block = bid
                        st.session_state.viewer_edit_mode = False
                        st.rerun()
    else:
        st.caption("Нет блоков на этой странице")

# ══════════════════════════════════════════════════════════════════════════════
# RIGHT SIDEBAR — детали блока
# ══════════════════════════════════════════════════════════════════════════════
with col_right:
    st.markdown("### 🔧 Инструменты")

    selected_id = st.session_state.viewer_selected_block
    if not selected_id:
        st.caption("Выберите блок кликом")

        # Экспорт документа
        st.markdown("---")
        st.markdown("**📤 Экспорт**")
        for fmt, icon in [("markdown", "📝"), ("json", "🗂"), ("csv", "📊")]:
            if st.button(f"{icon} {fmt.upper()}", key=f"exp_{fmt}", use_container_width=True):
                try:
                    r = httpx.post(
                        f"{BACKEND_URL}/processing/{doc_id}/export?format={fmt}",
                        timeout=30,
                    )
                    if r.status_code == 200:
                        st.success(r.json().get("message", "Готово"))
                    else:
                        st.error("Ошибка экспорта")
                except Exception as e:
                    st.error(str(e))
        st.stop()

    # Находим выбранный блок
    block = next((b for b in all_blocks if b["block_id"] == selected_id), None)
    if not block:
        st.warning("Блок не найден")
        st.stop()

    # Мета-инфо блока
    btype   = block.get("block_type", "—")
    bstatus = block.get("status", "—")
    conf    = block.get("confidence", 0)
    output  = block.get("output") or ""

    type_icon = {"text": "📝", "table": "📊", "formula": "➗", "figure": "🖼"}.get(btype, "?")
    st.markdown(f"**{type_icon} {btype.upper()}**")
    st.caption(f"`{selected_id}`")

    status_color = {
        "needs_review": "🔴", "accepted": "🟢",
        "ocr_done": "🔵", "error": "❌",
    }.get(bstatus, "⚪")
    st.markdown(f"Статус: {status_color} `{bstatus}`")
    st.markdown(f"Conf: `{conf:.2f}`")

    # Изображение блока
    try:
        r = httpx.get(
            f"{BACKEND_URL}/documents/{doc_id}/block-image/{selected_id}",
            timeout=10,
        )
        if r.status_code == 200:
            st.image(r.content, use_container_width=True)
    except Exception:
        pass

    st.markdown("---")

    # OCR Output
    if output:
        st.markdown("**OCR Output:**")
        if btype == "table":
            table_html = f"""
            <html><head><style>
                body {{
                    background: #1e1e1e;
                    color: #e0e0e0;
                    font-family: 'Segoe UI', Arial, sans-serif;
                    font-size: 11px;
                    margin: 4px;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin-top: 4px;
                }}
                th, td {{
                    border: 1px solid #444;
                    padding: 4px 8px;
                    text-align: left;
                    white-space: nowrap;
                }}
                th {{
                    background: #2d4a6e;
                    color: #ffffff;
                    font-weight: 600;
                }}
                tr:nth-child(even) {{
                    background: #2a2a2a;
                }}
                tr:nth-child(odd) {{
                    background: #1e1e1e;
                }}
                tr:hover {{
                    background: #3a3a5c;
                }}
                td[colspan], td[rowspan], th[colspan], th[rowspan] {{
                    background: #2d4a6e;
                    color: #ffffff;
                    text-align: center;
                }}
            </style></head>
            <body>{output}</body></html>
            """
            components.html(table_html, height=350, scrolling=True)
        elif btype == "formula":
            clean = output.strip().strip("$")
            try:
                st.latex(clean)
            except Exception:
                st.code(clean)
        else:
            st.text_area("", value=output, height=150,
                        disabled=not st.session_state.viewer_edit_mode,
                        key="output_display")
    else:
        st.caption("Нет OCR output")

    # Режим редактирования
    if st.session_state.viewer_edit_mode and btype not in ("table", "formula"):
        new_output = st.text_area(
            "✏️ Редактировать output:",
            value=output,
            height=150,
            key="edit_output",
        )
        if st.button("💾 Сохранить", use_container_width=True, type="primary"):
            try:
                r = httpx.patch(
                    f"{BACKEND_URL}/processing/{doc_id}/blocks/{selected_id}",
                    json={"output": new_output, "status": "accepted"},
                    timeout=5,
                )
                if r.status_code == 200:
                    st.success("✅ Сохранено")
                    fetch_blocks.clear()
                    st.session_state.viewer_edit_mode = False
                    st.rerun()
                else:
                    st.error("Ошибка сохранения")
            except Exception as e:
                st.error(str(e))

    st.markdown("---")
    st.markdown("**Действия:**")

    a1, a2 = st.columns(2)
    with a1:
        if st.button("✅ Принять", use_container_width=True):
            httpx.patch(
                f"{BACKEND_URL}/processing/{doc_id}/blocks/{selected_id}",
                json={"status": "accepted"}, timeout=5,
            )
            fetch_blocks.clear()
            st.rerun()

        if st.button("✏️ Править", use_container_width=True):
            st.session_state.viewer_edit_mode = not st.session_state.viewer_edit_mode
            st.rerun()

    with a2:
        if st.button("🔖 В Review", use_container_width=True):
            httpx.patch(
                f"{BACKEND_URL}/processing/{doc_id}/blocks/{selected_id}",
                json={"status": "needs_review"}, timeout=5,
            )
            fetch_blocks.clear()
            st.rerun()

        if st.button("⏭ След. блок", use_container_width=True):
            page_ids = [b["block_id"] for b in page_blocks_filtered]
            if selected_id in page_ids:
                idx = page_ids.index(selected_id)
                if idx + 1 < len(page_ids):
                    st.session_state.viewer_selected_block = page_ids[idx + 1]
                    st.session_state.viewer_edit_mode = False
                    st.rerun()

    st.markdown("---")
    st.markdown("**📤 Экспорт**")
    for fmt, icon in [("markdown", "📝"), ("json", "🗂"), ("csv", "📊")]:
        if st.button(f"{icon} {fmt.upper()}", key=f"exp2_{fmt}", use_container_width=True):
            try:
                r = httpx.post(
                    f"{BACKEND_URL}/processing/{doc_id}/export?format={fmt}",
                    timeout=30,
                )
                if r.status_code == 200:
                    st.success(r.json().get("message", "Готово"))
            except Exception as e:
                st.error(str(e))
