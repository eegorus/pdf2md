import streamlit as st
import streamlit.components.v1 as components
import httpx
import os
from PIL import Image, ImageDraw
from streamlit_image_coordinates import streamlit_image_coordinates
from streamlit_drawable_canvas import st_canvas
import io
from collections import Counter

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(
    page_title="Viewer — PRMS",
    layout="wide",
    initial_sidebar_state="expanded",
)

BLOCK_COLORS = {
    "text":    (59,  130, 246),
    "table":   (234, 88,  12),
    "formula": (22,  163, 74),
    "figure":  (147, 51,  234),
}
TYPE_COLORS_HEX = {
    "text":    "#3b82f6",
    "table":   "#ea580c",
    "formula": "#16a34a",
    "figure":  "#9333ea",
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
def fetch_page_image(doc_id: str, page_num: int, max_width: int = 520):
    """Возвращает (resized_img, orig_w, orig_h) или (None, 0, 0)."""
    try:
        r = httpx.get(
            f"{BACKEND_URL}/documents/{doc_id}/page-image/{page_num}",
            timeout=15,
        )
        if r.status_code == 200:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            orig_w, orig_h = img.size
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
            return img, orig_w, orig_h
    except Exception:
        pass
    return None, 0, 0

def draw_blocks_on_image(image, blocks, page_num, show_types, selected_id, bbox_scale=1.0):
    img = image.copy()
    draw = ImageDraw.Draw(img, "RGBA")
    for block in blocks:
        if block.get("page_num") != page_num:
            continue
        btype = block.get("block_type", "text")
        if btype not in show_types:
            continue
        bbox = block.get("bbox", [])
        if len(bbox) != 4:
            continue
        x1 = int(bbox[0] * bbox_scale)
        y1 = int(bbox[1] * bbox_scale)
        x2 = int(bbox[2] * bbox_scale)
        y2 = int(bbox[3] * bbox_scale)
        rgb = BLOCK_COLORS.get(btype, (128, 128, 128))
        bid = block.get("block_id")
        status = block.get("status", "")
        if bid == selected_id:
            outline, lw = (255, 220, 0, 255), 4
        elif status == "needs_review":
            outline, lw = (239, 68, 68, 255), 3
        elif status == "accepted":
            outline, lw = (34, 197, 94, 255), 2
        else:
            outline, lw = (*rgb, 200), 2
        draw.rectangle([x1, y1, x2, y2], fill=(*rgb, 30), outline=outline, width=lw)
        draw.rectangle([x1, y1, x1 + 18, y1 + 16], fill=(*rgb, 220))
        draw.text((x1 + 2, y1 + 1), btype[0].upper(), fill=(255, 255, 255))
    return img

# ─── SESSION STATE ─────────────────────────────────────────────────────────────
_defaults = {
    "viewer_page": 1,
    "viewer_selected_block": None,
    "viewer_edit_mode": False,
    "viewer_confirm_delete": False,
    "viewer_draw_mode": False,
    "viewer_draw_type": "text",
    "viewer_canvas_key": 0,     # инкрементируем для сброса canvas
    "viewer_orig_w": 0,
    "viewer_orig_h": 0,
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─── ЗАГОЛОВОК ────────────────────────────────────────────────────────────────
st.title("🔍 Document Viewer")

doc_id = st.session_state.get("current_doc_id")
if not doc_id:
    st.info("👆 Выберите документ на странице **Upload**")
    st.stop()

all_blocks = fetch_blocks(doc_id)
doc_name   = st.session_state.get("current_doc_name", doc_id)

try:
    r = httpx.get(f"{BACKEND_URL}/processing/{doc_id}/status", timeout=5)
    total_pages = r.json().get("page_count", 1)
except Exception:
    total_pages = 1

col_left, col_main, col_right = st.columns([0.75, 2.5, 1.5])

# ══════════════════════════════════════════════════════════════════════════════
# LEFT — документы, фильтры, статистика
# ══════════════════════════════════════════════════════════════════════════════
with col_left:
    st.markdown("### 📁 Документы")
    docs = fetch_documents()
    for doc in docs:
        did    = doc["doc_id"]
        dname  = doc.get("filename", did)[:26]
        is_cur = did == doc_id
        if st.button(
            f"{'▶ ' if is_cur else ''}{dname}",
            key=f"doc_{did}",
            use_container_width=True,
            type="primary" if is_cur else "secondary",
        ):
            st.session_state.current_doc_id           = did
            st.session_state.current_doc_name         = doc.get("filename", did)
            st.session_state.viewer_page              = 1
            st.session_state.viewer_selected_block    = None
            st.session_state.viewer_confirm_delete    = False
            st.session_state.viewer_draw_mode         = False
            st.session_state.viewer_canvas_key        += 1
            fetch_blocks.clear()
            st.rerun()

    st.markdown("---")
    st.markdown("### 🎨 Фильтры")
    show_types = set()
    if st.checkbox("📝 Text",    value=True): show_types.add("text")
    if st.checkbox("📊 Table",   value=True): show_types.add("table")
    if st.checkbox("➗ Formula", value=True): show_types.add("formula")
    if st.checkbox("🖼 Figure",  value=True): show_types.add("figure")

    st.markdown("---")
    st.markdown("### 📊 Страница")
    st.caption(f"📄 {doc_name[:28]}")
    page_blocks_cur = [b for b in all_blocks if b.get("page_num") == st.session_state.viewer_page]
    type_counts = Counter(b.get("block_type") for b in page_blocks_cur)
    st.metric("Страниц", total_pages)
    st.metric("Всего блоков", len(all_blocks))
    for btype, cnt in sorted(type_counts.items()):
        emoji = {"text": "🔵", "table": "🟠", "formula": "🟢", "figure": "🟣"}.get(btype, "⚪")
        st.caption(f"{emoji} {btype}: {cnt}")
    nr = sum(1 for b in all_blocks if b.get("status") == "needs_review")
    if nr:
        st.markdown(f"🔴 **needs_review: {nr}**")

# ══════════════════════════════════════════════════════════════════════════════
# MAIN — навигация + изображение + список блоков
# ══════════════════════════════════════════════════════════════════════════════
with col_main:
    # Навигация
    n1, n2, n3, n4, n5 = st.columns([1, 1, 2, 1, 1])
    with n1:
        if st.button("⏮", use_container_width=True):
            st.session_state.viewer_page = 1
            st.session_state.viewer_selected_block = None
    with n2:
        if st.button("◀", use_container_width=True) and st.session_state.viewer_page > 1:
            st.session_state.viewer_page -= 1
            st.session_state.viewer_selected_block = None
    with n3:
        new_page = st.number_input(
            "Стр.", min_value=1, max_value=total_pages,
            value=st.session_state.viewer_page,
            label_visibility="collapsed",
        )
        if new_page != st.session_state.viewer_page:
            st.session_state.viewer_page = new_page
            st.session_state.viewer_selected_block = None
    with n4:
        if st.button("▶", use_container_width=True) and st.session_state.viewer_page < total_pages:
            st.session_state.viewer_page += 1
            st.session_state.viewer_selected_block = None
    with n5:
        if st.button("⏭", use_container_width=True):
            st.session_state.viewer_page = total_pages
            st.session_state.viewer_selected_block = None

    st.caption(f"Страница {st.session_state.viewer_page} / {total_pages}")

    # ── Переключатель режима ───────────────────────────────────────────────
    tc1, tc2, tc3 = st.columns([1, 1.5, 2.5])
    with tc1:
        is_draw = st.session_state.viewer_draw_mode
        if st.button(
            "👆 Выбор" if is_draw else "✏️ Рисовать",
            use_container_width=True,
            type="secondary" if is_draw else "primary",
            key="toggle_draw_mode",
        ):
            st.session_state.viewer_draw_mode = not is_draw
            st.session_state.viewer_canvas_key += 1
            st.rerun()

    if st.session_state.viewer_draw_mode:
        with tc2:
            type_opts = ["text", "table", "formula", "figure"]
            icons = {"text": "📝", "table": "📊", "formula": "➗", "figure": "🖼"}
            st.session_state.viewer_draw_type = st.selectbox(
                "тип",
                type_opts,
                index=type_opts.index(st.session_state.viewer_draw_type),
                format_func=lambda t: f"{icons[t]} {t}",
                label_visibility="collapsed",
                key="draw_type_sel",
            )
        with tc3:
            draw_type = st.session_state.viewer_draw_type
            rgb = BLOCK_COLORS[draw_type]
            st.markdown(
                f"<div style='padding:6px 10px;background:rgba({rgb[0]},{rgb[1]},{rgb[2]},0.2);"
                f"border:1px solid {TYPE_COLORS_HEX[draw_type]};border-radius:6px;"
                f"font-size:13px;color:{TYPE_COLORS_HEX[draw_type]}'>"
                f"Нарисуй прямоугольник на изображении</div>",
                unsafe_allow_html=True,
            )

    # ── Изображение ───────────────────────────────────────────────────────
    page_img, orig_w, orig_h = fetch_page_image(doc_id, st.session_state.viewer_page)

    if page_img:
        st.session_state.viewer_orig_w = orig_w
        st.session_state.viewer_orig_h = orig_h
        bbox_scale = page_img.width / orig_w if orig_w else 1.0

        annotated = draw_blocks_on_image(
            page_img, all_blocks,
            st.session_state.viewer_page, show_types,
            st.session_state.viewer_selected_block,
            bbox_scale=bbox_scale,
        )

        if st.session_state.viewer_draw_mode:
            # ── DRAWING MODE ──────────────────────────────────────────────
            draw_type   = st.session_state.viewer_draw_type
            stroke_hex  = TYPE_COLORS_HEX[draw_type]
            rgb         = BLOCK_COLORS[draw_type]
            fill_rgba   = f"rgba({rgb[0]},{rgb[1]},{rgb[2]},0.25)"

            canvas_result = st_canvas(
                fill_color=fill_rgba,
                stroke_width=2,
                stroke_color=stroke_hex,
                background_image=annotated,   # PIL Image — уже аннотированный
                update_streamlit=True,
                height=annotated.height,
                width=annotated.width,
                drawing_mode="rect",
                display_toolbar=False,        # убираем лишние кнопки
                key=f"canvas_{doc_id}_{st.session_state.viewer_page}_{st.session_state.viewer_canvas_key}",
            )

            # Обрабатываем нарисованные прямоугольники
            objects = []
            if canvas_result.json_data is not None:
                objects = canvas_result.json_data.get("objects", [])

            if objects:
                last = objects[-1]
                sx = last.get("scaleX", 1.0)
                sy = last.get("scaleY", 1.0)
                # Canvas координаты (display-пиксели)
                cx1 = int(last["left"])
                cy1 = int(last["top"])
                cx2 = int(last["left"] + last["width"]  * sx)
                cy2 = int(last["top"]  + last["height"] * sy)
                # Переводим в оригинальные координаты документа
                ox1 = max(0, int(cx1 / bbox_scale))
                oy1 = max(0, int(cy1 / bbox_scale))
                ox2 = min(orig_w, int(cx2 / bbox_scale))
                oy2 = min(orig_h, int(cy2 / bbox_scale))

                st.markdown(
                    f"<div style='margin:6px 0;padding:6px 10px;"
                    f"background:rgba({rgb[0]},{rgb[1]},{rgb[2]},0.15);"
                    f"border-left:3px solid {stroke_hex};border-radius:4px;font-size:12px'>"
                    f"📐 <b>{draw_type}</b> · bbox: [{ox1}, {oy1}, {ox2}, {oy2}] "
                    f"· размер: {ox2-ox1}×{oy2-oy1} px</div>",
                    unsafe_allow_html=True,
                )

                ab1, ab2 = st.columns([2, 1])
                with ab1:
                    if st.button("✅ Добавить блок", use_container_width=True,
                                 type="primary", key="btn_canvas_add"):
                        if ox2 > ox1 and oy2 > oy1:
                            try:
                                resp = httpx.post(
                                    f"{BACKEND_URL}/processing/{doc_id}/blocks",
                                    json={
                                        "block_type": draw_type,
                                        "page_num":   st.session_state.viewer_page,
                                        "bbox":       [ox1, oy1, ox2, oy2],
                                    },
                                    timeout=10,
                                )
                                if resp.status_code == 200:
                                    fetch_blocks.clear()
                                    st.session_state.viewer_canvas_key += 1
                                    st.session_state.viewer_selected_block = resp.json().get("block_id")
                                    st.session_state.viewer_draw_mode = False
                                    st.success(f"✅ Создан: {resp.json().get('block_id','')[:24]}")
                                    st.rerun()
                                else:
                                    st.error(f"Ошибка {resp.status_code}: {resp.text[:80]}")
                            except Exception as e:
                                st.error(str(e))
                        else:
                            st.warning("Нарисуй прямоугольник на изображении")
                with ab2:
                    if st.button("🗑 Сбросить", use_container_width=True, key="btn_canvas_clear"):
                        st.session_state.viewer_canvas_key += 1
                        st.rerun()
        else:
            # ── SELECT MODE ───────────────────────────────────────────────
            coords = streamlit_image_coordinates(
                annotated,
                use_column_width=False,
                key=f"viewer_{doc_id}_{st.session_state.viewer_page}",
            )
            if coords:
                cx = int(coords["x"] / bbox_scale)
                cy = int(coords["y"] / bbox_scale)
                clicked = None
                for b in all_blocks:
                    if b.get("page_num") != st.session_state.viewer_page:
                        continue
                    if b.get("block_type") not in show_types:
                        continue
                    bx = b.get("bbox", [])
                    if len(bx) == 4 and bx[0] <= cx <= bx[2] and bx[1] <= cy <= bx[3]:
                        clicked = b["block_id"]
                        break
                if clicked and clicked != st.session_state.viewer_selected_block:
                    st.session_state.viewer_selected_block = clicked
                    st.session_state.viewer_edit_mode      = False
                    st.session_state.viewer_confirm_delete = False
                    st.rerun()
    else:
        st.warning("Изображение страницы недоступно")

    # ── Список блоков ──────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**Блоки на странице:**")
    page_blocks_filtered = [
        b for b in all_blocks
        if b.get("page_num") == st.session_state.viewer_page
        and b.get("block_type") in show_types
    ]
    if page_blocks_filtered:
        cols_per_row = 4
        for row in [page_blocks_filtered[i:i+cols_per_row]
                    for i in range(0, len(page_blocks_filtered), cols_per_row)]:
            rcols = st.columns(cols_per_row)
            for col, block in zip(rcols, row):
                bid     = block["block_id"]
                btype   = block.get("block_type", "?")
                bstatus = block.get("status", "")
                is_sel  = bid == st.session_state.viewer_selected_block
                s_icon  = {"needs_review": "🔴", "accepted": "✅",
                           "ocr_done": "🔵", "error": "❌"}.get(bstatus, "⚪")
                t_icon  = {"text": "📝", "table": "📊",
                           "formula": "➗", "figure": "🖼"}.get(btype, "?")
                with col:
                    if st.button(
                        f"{'▶' if is_sel else ''}{t_icon}{s_icon}",
                        key=f"sel_{bid}",
                        use_container_width=True,
                        help=f"{bid}\n{btype} · {bstatus}",
                    ):
                        st.session_state.viewer_selected_block = None if is_sel else bid
                        st.session_state.viewer_edit_mode      = False
                        st.session_state.viewer_confirm_delete = False
                        st.rerun()
    else:
        st.caption("Нет блоков на этой странице")

# ══════════════════════════════════════════════════════════════════════════════
# RIGHT — детали блока
# ══════════════════════════════════════════════════════════════════════════════
with col_right:
    st.markdown("### 🔧 Инструменты")
    selected_id = st.session_state.viewer_selected_block

    if not selected_id:
        if st.session_state.viewer_draw_mode:
            draw_type = st.session_state.viewer_draw_type
            rgb = BLOCK_COLORS[draw_type]
            st.markdown(
                f"<div style='padding:10px;background:rgba({rgb[0]},{rgb[1]},{rgb[2]},0.1);"
                f"border:1px solid {TYPE_COLORS_HEX[draw_type]};border-radius:8px'>"
                f"<b>✏️ Режим рисования</b><br><br>"
                f"1. Выбери тип блока слева<br>"
                f"2. Нарисуй прямоугольник на изображении<br>"
                f"3. Нажми <b>«Добавить блок»</b></div>",
                unsafe_allow_html=True,
            )
        else:
            st.caption("Кликни на блок или выбери из списка ниже")
        st.markdown("---")
        st.markdown("**📤 Экспорт**")
        for fmt, icon in [("markdown", "📝"), ("json", "🗂"), ("csv", "📊")]:
            if st.button(f"{icon} {fmt.upper()}", key=f"exp_{fmt}", use_container_width=True):
                try:
                    r = httpx.post(
                        f"{BACKEND_URL}/processing/{doc_id}/export?format={fmt}", timeout=30
                    )
                    if r.status_code == 200:
                        st.success(r.json().get("message", "Готово"))
                    else:
                        st.error("Ошибка экспорта")
                except Exception as e:
                    st.error(str(e))
        st.stop()

    block = next((b for b in all_blocks if b["block_id"] == selected_id), None)
    if not block:
        st.warning("Блок не найден")
        st.stop()

    btype   = block.get("block_type", "—")
    bstatus = block.get("status", "—")
    conf    = block.get("confidence", 0)
    output  = block.get("output") or ""
    bbox    = block.get("bbox", [0, 0, 0, 0])

    type_icon   = {"text": "📝", "table": "📊", "formula": "➗", "figure": "🖼"}.get(btype, "?")
    status_icon = {"needs_review": "🔴", "accepted": "🟢",
                   "ocr_done": "🔵", "error": "❌"}.get(bstatus, "⚪")

    st.markdown(f"**{type_icon} {btype.upper()}**")
    st.caption(f"`{selected_id}`")
    st.markdown(f"Статус: {status_icon} `{bstatus}`")
    st.markdown(f"Conf: `{conf:.2f}`")

    try:
        r = httpx.get(f"{BACKEND_URL}/documents/{doc_id}/block-image/{selected_id}", timeout=10)
        if r.status_code == 200:
            st.image(r.content, use_container_width=True)
    except Exception:
        pass

    # ── ГЕОМЕТРИЯ И ТИП ───────────────────────────────────────────────────
    with st.expander("✏️ Геометрия и тип", expanded=False):
        type_list = ["text", "table", "formula", "figure"]
        edit_btype = st.selectbox(
            "Тип блока", type_list,
            index=type_list.index(btype) if btype in type_list else 0,
            key=f"edit_btype_{selected_id}",
        )
        bx = bbox if len(bbox) == 4 else [0, 0, 100, 100]
        lw = st.session_state.viewer_orig_w or 9999
        lh = st.session_state.viewer_orig_h or 9999
        c1, c2 = st.columns(2)
        with c1:
            ex1 = st.number_input("x1", 0, lw, int(bx[0]), key=f"ex1_{selected_id}")
            ex2 = st.number_input("x2", 0, lw, int(bx[2]), key=f"ex2_{selected_id}")
        with c2:
            ey1 = st.number_input("y1", 0, lh, int(bx[1]), key=f"ey1_{selected_id}")
            ey2 = st.number_input("y2", 0, lh, int(bx[3]), key=f"ey2_{selected_id}")
        if st.button("💾 Сохранить геометрию", use_container_width=True,
                     type="primary", key=f"sgeo_{selected_id}"):
            if ex2 > ex1 and ey2 > ey1:
                try:
                    resp = httpx.patch(
                        f"{BACKEND_URL}/processing/{doc_id}/blocks/{selected_id}",
                        json={"block_type": edit_btype, "bbox": [ex1, ey1, ex2, ey2]},
                        timeout=5,
                    )
                    if resp.status_code == 200:
                        fetch_blocks.clear()
                        st.success("✅ Сохранено")
                        st.rerun()
                    else:
                        st.error(f"Ошибка: {resp.text[:80]}")
                except Exception as e:
                    st.error(str(e))
            else:
                st.warning("x2 > x1 и y2 > y1!")

    # ── УДАЛИТЬ БЛОК ─────────────────────────────────────────────────────
    if not st.session_state.viewer_confirm_delete:
        if st.button("🗑 Удалить блок", use_container_width=True, key="btn_del"):
            st.session_state.viewer_confirm_delete = True
            st.rerun()
    else:
        st.warning("⚠️ Удалить безвозвратно?")
        dc1, dc2 = st.columns(2)
        with dc1:
            if st.button("✅ Да", use_container_width=True, type="primary", key="btn_del_yes"):
                try:
                    httpx.delete(
                        f"{BACKEND_URL}/processing/{doc_id}/blocks/{selected_id}", timeout=5
                    )
                except Exception:
                    pass
                st.session_state.viewer_selected_block = None
                st.session_state.viewer_confirm_delete = False
                fetch_blocks.clear()
                st.rerun()
        with dc2:
            if st.button("❌ Нет", use_container_width=True, key="btn_del_no"):
                st.session_state.viewer_confirm_delete = False
                st.rerun()

    st.markdown("---")

    if output:
        st.markdown("**OCR Output:**")
        if btype == "table":
            components.html(
                f"<html><head><style>"
                f"body{{background:#1e1e1e;color:#e0e0e0;font-family:'Segoe UI',sans-serif;"
                f"font-size:11px;margin:4px}}"
                f"table{{border-collapse:collapse;width:100%;margin-top:4px}}"
                f"th,td{{border:1px solid #444;padding:4px 8px;text-align:left;white-space:nowrap}}"
                f"th{{background:#2d4a6e;color:#fff;font-weight:600}}"
                f"tr:nth-child(even){{background:#2a2a2a}}tr:nth-child(odd){{background:#1e1e1e}}"
                f"tr:hover{{background:#3a3a5c}}"
                f"td[colspan],td[rowspan],th[colspan],th[rowspan]"
                f"{{background:#2d4a6e;color:#fff;text-align:center}}"
                f"</style></head><body>{output}</body></html>",
                height=280, scrolling=True,
            )
        elif btype == "formula":
            clean = output.strip().strip("$")
            try:    st.latex(clean)
            except: st.code(clean)
        else:
            st.text_area("", value=output, height=120,
                         disabled=not st.session_state.viewer_edit_mode,
                         key="output_display")
    else:
        st.caption("Нет OCR output")

    if st.session_state.viewer_edit_mode and btype not in ("table", "formula"):
        new_output = st.text_area("✏️ Редактировать:", value=output, height=120, key="edit_output")
        if st.button("💾 Сохранить текст", use_container_width=True,
                     type="primary", key="save_text"):
            try:
                r = httpx.patch(
                    f"{BACKEND_URL}/processing/{doc_id}/blocks/{selected_id}",
                    json={"output": new_output, "status": "accepted"}, timeout=5,
                )
                if r.status_code == 200:
                    fetch_blocks.clear()
                    st.session_state.viewer_edit_mode = False
                    st.success("✅ Сохранено")
                    st.rerun()
                else:
                    st.error("Ошибка")
            except Exception as e:
                st.error(str(e))

    st.markdown("---")
    st.markdown("**Действия:**")
    a1, a2 = st.columns(2)
    with a1:
        if st.button("✅ Принять", use_container_width=True, key="btn_accept"):
            httpx.patch(f"{BACKEND_URL}/processing/{doc_id}/blocks/{selected_id}",
                        json={"status": "accepted"}, timeout=5)
            fetch_blocks.clear()
            st.rerun()
        if st.button("✏️ Править", use_container_width=True, key="btn_edit"):
            st.session_state.viewer_edit_mode = not st.session_state.viewer_edit_mode
            st.rerun()
    with a2:
        if st.button("🔖 В Review", use_container_width=True, key="btn_review"):
            httpx.patch(f"{BACKEND_URL}/processing/{doc_id}/blocks/{selected_id}",
                        json={"status": "needs_review"}, timeout=5)
            fetch_blocks.clear()
            st.rerun()
        if st.button("⏭ След. блок", use_container_width=True, key="btn_next"):
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
                r = httpx.post(f"{BACKEND_URL}/processing/{doc_id}/export?format={fmt}", timeout=30)
                if r.status_code == 200:
                    st.success(r.json().get("message", "Готово"))
            except Exception as e:
                st.error(str(e))
