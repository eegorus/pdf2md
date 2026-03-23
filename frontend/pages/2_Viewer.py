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
    "table_simple":  (234, 179, 8),
    "table_complex": (234, 88,  12),
    "formula": (22,  163, 74),
    "figure":  (147, 51,  234),
}
TYPE_HEX = {
    "text": "#3b82f6", "table": "#ea580c", "table_simple": "#eab308", "table_complex": "#ea580c",
    "formula": "#16a34a", "figure": "#9333ea",
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
def fetch_block_image(doc_id: str, block_id: str, _cache_bust: int = 0):
    """Возвращает bytes превью блока или None. _cache_bust инвалидирует кэш."""
    try:
        r = httpx.get(
            f"{BACKEND_URL}/documents/{doc_id}/block-image/{block_id}", timeout=10
        )
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None


@st.cache_data(ttl=60)
def fetch_page_image(doc_id: str, page_num: int, max_w: int = 740, max_h: int = 960):
    """Возвращает (resized_img, orig_w, orig_h, scale) или (None, 0, 0, 1.0)."""
    try:
        r = httpx.get(
            f"{BACKEND_URL}/documents/{doc_id}/page-image/{page_num}", timeout=15
        )
        if r.status_code == 200:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            orig_w, orig_h = img.size
            scale = min(1.0, max_w / orig_w, max_h / orig_h)
            if scale < 1.0:
                img = img.resize((int(orig_w * scale), int(orig_h * scale)), Image.LANCZOS)
            return img, orig_w, orig_h, scale
    except Exception:
        pass
    return None, 0, 0, 1.0


def draw_blocks_on_image(
    image: Image.Image,
    blocks: list,
    page_num: int,
    show_types: set,
    selected_id: str | None,
    scale: float = 1.0,
) -> Image.Image:
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
        x1, y1, x2, y2 = (int(v * scale) for v in bbox)
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
    "viewer_model_dialog_open": False,
    "viewer_model_choices": {},      # {block_type: model_id}
    # Режимы
    "viewer_draw_mode": False,
    "viewer_draw_type": "text",
    "viewer_mode": None,              # None | "edit" — редактирование bbox
    "viewer_canvas_version": 0,       # инкрементируем чтобы сбросить st_canvas
    # Размеры оригинала
    "viewer_orig_w": 0,
    "viewer_orig_h": 0,
    # Undo-стек (max 10)
    "undo_stack": [],                 # list of {action, block_id, snapshot?}
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─── ЗАГОЛОВОК ────────────────────────────────────────────────────────────────
st.title("🔍 Document Viewer")

doc_id = st.session_state.get("viewer_doc_id") or st.session_state.get("viewer_doc_id")
if not doc_id:
    st.info("👆 Выберите документ на странице **Upload**")
    st.stop()

all_blocks = fetch_blocks(doc_id)
doc_name = st.session_state.get("current_doc_name", doc_id)


def _do_undo() -> bool:
    """Отменяет последнее действие из undo_stack. Возвращает True при успехе."""
    _stk = st.session_state.undo_stack
    if not _stk:
        return False
    _entry = _stk[-1]
    _act = _entry["action"]
    try:
        if _act == "delete":
            _snap = _entry["snapshot"]
            _r = httpx.post(
                f"{BACKEND_URL}/processing/{doc_id}/blocks",
                json={
                    "block_type": _snap.get("block_type"),
                    "page_num":   _snap.get("page_num"),
                    "bbox":       _snap.get("bbox"),
                },
                timeout=10,
            )
            if _r.status_code == 200:
                st.session_state.viewer_selected_block = _r.json().get("block_id")
                _stk.pop()
                fetch_blocks.clear()
                return True
        elif _act == "add":
            _r = httpx.delete(
                f"{BACKEND_URL}/processing/{doc_id}/blocks/{_entry['block_id']}",
                timeout=5,
            )
            if _r.status_code in (200, 204, 404):
                if st.session_state.viewer_selected_block == _entry["block_id"]:
                    st.session_state.viewer_selected_block = None
                _stk.pop()
                fetch_blocks.clear()
                return True
        elif _act == "patch":
            _snap = _entry["snapshot"]
            _r = httpx.patch(
                f"{BACKEND_URL}/processing/{doc_id}/blocks/{_entry['block_id']}",
                json={
                    "block_type": _snap.get("block_type"),
                    "bbox":       _snap.get("bbox"),
                    "output":     _snap.get("output"),
                    "status":     _snap.get("status"),
                },
                timeout=5,
            )
            if _r.status_code == 200:
                _stk.pop()
                fetch_blocks.clear()
                return True
    except Exception as _e:
        st.toast(f"Undo: {_e}", icon="❌")
    return False

try:
    r = httpx.get(f"{BACKEND_URL}/processing/{doc_id}/status", timeout=5)
    total_pages = r.json().get("page_count", 1)
except Exception:
    total_pages = 1


# ─── МОДАЛЬНОЕ ОКНО ВЫБОРА МОДЕЛЕЙ ────────────────────────────────────────────
@st.dialog("🎯 Выбор моделей для распознавания", width="large")
def model_selection_dialog(doc_id: str, all_blocks: list):
    """Пользователь назначает модель для каждого типа блоков → запускает OCR."""
    import httpx as _httpx

    # Загружаем доступные модели с backend
    try:
        r = _httpx.get(f"{BACKEND_URL}/settings/available-models", timeout=5)
        avail = r.json()
    except Exception as e:
        st.error(f"Не удалось загрузить список моделей: {e}")
        return

    # Определяем какие типы блоков реально присутствуют в документе
    present_types = sorted(set(
        b.get("block_type") for b in all_blocks
        if b.get("block_type") in avail
    ))

    if not present_types:
        st.warning("В документе нет размеченных блоков")
        return

    BLOCK_LABELS = {
        "text":          "📝 Текст",
        "figure":        "🖼 Картинка",
        "table_simple":  "📊 Таблица простая",
        "table_complex": "📊 Таблица сложная",
        "formula":       "➗ Формула",
        "table":         "📊 Таблица",
    }

    st.markdown("Назначьте модель для каждого типа блоков в документе:")
    st.markdown("---")

    choices = {}
    for btype in present_types:
        info    = avail[btype]
        models  = info["models"]
        default = st.session_state.viewer_model_choices.get(btype, info["default"])

        col_type, col_sel = st.columns([1, 2])
        with col_type:
            cnt = sum(1 for b in all_blocks if b.get("block_type") == btype)
            st.markdown(f"**{BLOCK_LABELS.get(btype, btype)}**")
            st.caption(f"{cnt} блоков")
        with col_sel:
            opts       = [m["id"]    for m in models]
            opt_labels = []
            for m in models:
                avail_icon = "✅" if m["available"] else "○"
                reason     = f" — {m['reason']}" if m.get("reason") else ""
                opt_labels.append(f"{avail_icon} {m['label']}{reason}")

            default_idx = opts.index(default) if default in opts else 0
            chosen = st.selectbox(
                f"Модель для {btype}",
                options=opts,
                index=default_idx,
                format_func=lambda x, _opts=opts, _labels=opt_labels: _labels[_opts.index(x)],
                key=f"modal_model_{btype}",
                label_visibility="collapsed",
            )
            choices[btype] = chosen

            # Предупреждение если выбранная модель недоступна
            chosen_meta = next((m for m in models if m["id"] == chosen), None)
            if chosen_meta and not chosen_meta["available"]:
                st.warning(f"⚠️ {chosen_meta['reason']}", icon=None)

        st.markdown("")

    st.markdown("---")
    run_col, cancel_col = st.columns([2, 1])
    with run_col:
        if st.button("▶ Запустить распознавание", type="primary",
                     use_container_width=True, key="modal_run"):
            st.session_state.viewer_model_choices = choices
            # Запускаем OCR с выбранными моделями
            try:
                resp = _httpx.post(
                    f"{BACKEND_URL}/processing/{doc_id}/ocr",
                    json={"model_choices": choices},
                    timeout=15,
                )
                if resp.status_code == 200:
                    st.success("⏳ OCR запущен в фоне. Следи за статусом на странице.")
                    st.rerun()
                else:
                    st.error(f"Ошибка {resp.status_code}: {resp.text[:100]}")
            except Exception as e:
                st.error(str(e))
    with cancel_col:
        if st.button("Отмена", use_container_width=True, key="modal_cancel"):
            st.rerun()


col_left, col_main, col_right = st.columns([0.65, 3.0, 1.35])

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
            st.session_state.viewer_doc_id         = did
            st.session_state.current_doc_name       = doc.get("filename", did)
            st.session_state.viewer_page            = 1
            st.session_state.viewer_selected_block  = None
            st.session_state.viewer_draw_mode       = False
            st.session_state.viewer_mode            = None
            st.session_state.viewer_canvas_version += 1
            fetch_blocks.clear()
            st.rerun()

    st.markdown("---")
    st.markdown("### 🎨 Фильтры")
    show_types = set()
    if st.checkbox("📝 Text",    value=True): show_types.add("text")
    if st.checkbox("📊 Table",        value=True): show_types.add("table")
    if st.checkbox("📊 Таблица простая", value=True): show_types.add("table_simple")
    if st.checkbox("📊 Таблица сложная", value=True): show_types.add("table_complex")
    if st.checkbox("➗ Formula", value=True): show_types.add("formula")
    if st.checkbox("🖼 Figure",  value=True): show_types.add("figure")

    st.markdown("---")
    st.markdown("### 📊 Страница")
    st.caption(f"📄 {doc_name[:28]}")
    page_blocks_cur = [b for b in all_blocks
                       if b.get("page_num") == st.session_state.viewer_page]
    type_counts = Counter(b.get("block_type") for b in page_blocks_cur)
    st.metric("Страниц", total_pages)
    st.metric("Всего блоков", len(all_blocks))
    for btype, cnt in sorted(type_counts.items()):
        emoji = {"text": "🔵", "table": "🟠", "table_simple": "🟡", "table_complex": "🟠", "formula": "🟢", "figure": "🟣"}.get(btype, "⚪")
        st.caption(f"{emoji} {btype}: {cnt}")
    nr = sum(1 for b in all_blocks if b.get("status") == "needs_review")
    if nr:
        st.markdown(f"🔴 **needs_review: {nr}**")

    # ── Кнопка запуска финального OCR ─────────────────────────────────────
    st.markdown("---")
    total_blocks_count  = len(all_blocks)
    marked_blocks_count = sum(1 for b in all_blocks if b.get("block_type"))
    if total_blocks_count > 0:
        st.markdown("**🎯 Распознавание**")
        st.caption(f"Размечено блоков: {marked_blocks_count}")
        if st.button("✅ Выбрать модели и запустить",
                     use_container_width=True, type="primary",
                     key="btn_open_model_dialog"):
            model_selection_dialog(doc_id, all_blocks)



# ══════════════════════════════════════════════════════════════════════════════
# MAIN — навигация + изображение + список блоков
# ══════════════════════════════════════════════════════════════════════════════
with col_main:
    # ── Навигация ─────────────────────────────────────────────────────────
    n1, n2, n3, n4, n5 = st.columns([1, 1, 2, 1, 1])
    with n1:
        if st.button("⏮", use_container_width=True):
            st.session_state.viewer_page            = 1
            st.session_state.viewer_selected_block  = None
            st.session_state.viewer_canvas_version += 1
    with n2:
        if st.button("◀", use_container_width=True) and st.session_state.viewer_page > 1:
            st.session_state.viewer_page           -= 1
            st.session_state.viewer_selected_block  = None
            st.session_state.viewer_canvas_version += 1
    with n3:
        new_page = st.number_input(
            "Стр.", min_value=1, max_value=total_pages,
            value=st.session_state.viewer_page,
            label_visibility="collapsed",
        )
        if new_page != st.session_state.viewer_page:
            st.session_state.viewer_page            = new_page
            st.session_state.viewer_selected_block  = None
            st.session_state.viewer_canvas_version += 1
    with n4:
        if st.button("▶", use_container_width=True) and st.session_state.viewer_page < total_pages:
            st.session_state.viewer_page           += 1
            st.session_state.viewer_selected_block  = None
            st.session_state.viewer_canvas_version += 1
    with n5:
        if st.button("⏭", use_container_width=True):
            st.session_state.viewer_page            = total_pages
            st.session_state.viewer_selected_block  = None
            st.session_state.viewer_canvas_version += 1

    st.caption(f"Страница {st.session_state.viewer_page} / {total_pages}")

    # ── Тулбар: переключатель режима, выбор типа, undo ────────────────────
    is_draw = st.session_state.viewer_draw_mode
    tc1, tc2, tc3 = st.columns([1, 3, 1])
    with tc1:
        if st.button(
            "👆 Выбор" if is_draw else "✏️ Рисовать",
            use_container_width=True,
            type="secondary" if is_draw else "primary",
            key="toggle_mode",
        ):
            st.session_state.viewer_draw_mode = not is_draw
            st.session_state.viewer_canvas_version += 1

    if st.session_state.viewer_draw_mode:
        with tc2:
            type_opts = ["text", "table_simple", "table_complex", "formula", "figure"]
            icons = {"text": "📝", "table": "📊", "table_simple": "📊", "table_complex": "📊", "formula": "➗", "figure": "🖼"}
            prev_type = st.session_state.viewer_draw_type
            new_type = st.selectbox(
                "тип",
                type_opts,
                index=type_opts.index(prev_type),
                format_func=lambda t: f"{icons[t]} {t}",
                label_visibility="collapsed",
                key="draw_type_sel",
            )
            if new_type != prev_type:
                st.session_state.viewer_draw_type    = new_type
                st.session_state.viewer_canvas_version += 1

    with tc3:
        _stk = st.session_state.undo_stack
        _undo_label = f"↩ ({len(_stk)})" if _stk else "↩"
        if st.button(
            _undo_label,
            use_container_width=True,
            disabled=len(_stk) == 0,
            key="btn_undo_top",
            help=f"Отменить последнее действие ({len(_stk)} в стеке)" if _stk else "Нет действий для отмены",
        ):
            if _do_undo():
                st.rerun()

    # ── Изображение ───────────────────────────────────────────────────────
    page_img, orig_w, orig_h, scale = fetch_page_image(
        doc_id, st.session_state.viewer_page
    )

    if page_img:
        st.session_state.viewer_orig_w = orig_w
        st.session_state.viewer_orig_h = orig_h

        draw_type = st.session_state.viewer_draw_type

        annotated = draw_blocks_on_image(
            page_img, all_blocks,
            st.session_state.viewer_page, show_types,
            st.session_state.viewer_selected_block,
            scale=scale,
        )

        img_w = annotated.width

        if st.session_state.viewer_draw_mode:
            # ── DRAW MODE: st_canvas (drag-and-drop) ──────────────────────
            canvas_key = (
                f"canvas_{doc_id}_p{st.session_state.viewer_page}"
                f"_v{st.session_state.viewer_canvas_version}"
            )
            canvas_result = st_canvas(
                fill_color="rgba(255, 255, 255, 0.0)",
                stroke_width=2,
                stroke_color=TYPE_HEX.get(draw_type, "#FF0000"),
                background_image=annotated,
                update_streamlit=True,
                height=annotated.height,
                width=img_w,
                drawing_mode="rect",
                key=canvas_key,
            )

            # Ищем нарисованный прямоугольник
            _drawn_rect = None
            if canvas_result is not None and canvas_result.json_data is not None:
                _rects = [
                    o for o in canvas_result.json_data.get("objects", [])
                    if o.get("type") == "rect"
                ]
                if _rects:
                    _last = _rects[-1]
                    _left   = _last.get("left", 0)
                    _top    = _last.get("top", 0)
                    _width  = _last.get("width", 0) * _last.get("scaleX", 1)
                    _height = _last.get("height", 0) * _last.get("scaleY", 1)
                    x1 = max(0, int(_left / scale))
                    y1 = max(0, int(_top  / scale))
                    x2 = min(orig_w, int((_left + _width)  / scale))
                    y2 = min(orig_h, int((_top  + _height) / scale))
                    if x2 > x1 + 10 and y2 > y1 + 10:
                        _drawn_rect = [x1, y1, x2, y2]

            if _drawn_rect:
                ox1, oy1, ox2, oy2 = _drawn_rect
                rgb = BLOCK_COLORS.get(draw_type, (128, 128, 128))
                st.markdown(
                    f"<div style='padding:8px 12px;"
                    f"background:rgba({rgb[0]},{rgb[1]},{rgb[2]},0.15);"
                    f"border-left:3px solid {TYPE_HEX[draw_type]};border-radius:4px;"
                    f"font-size:13px;margin:4px 0'>"
                    f"📐 <b>{draw_type}</b> · [{ox1}, {oy1}, {ox2}, {oy2}] "
                    f"· {ox2-ox1}×{oy2-oy1} px</div>",
                    unsafe_allow_html=True,
                )
                ab1, ab2 = st.columns([2, 1])
                with ab1:
                    if st.button("✅ Добавить блок", use_container_width=True,
                                 type="primary", key="btn_canvas_add"):
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
                                new_id = resp.json().get("block_id")
                                _stk = st.session_state.undo_stack
                                _stk.append({"action": "add", "block_id": new_id})
                                if len(_stk) > 10:
                                    _stk.pop(0)
                                fetch_blocks.clear()
                                st.session_state.viewer_canvas_version += 1
                                st.session_state.viewer_draw_mode      = False
                                st.session_state.viewer_selected_block = new_id
                                st.rerun()
                            else:
                                st.error(f"Ошибка {resp.status_code}: {resp.text[:80]}")
                        except Exception as e:
                            st.error(str(e))
                with ab2:
                    if st.button("🔄 Перерисовать", use_container_width=True, key="btn_redraw"):
                        st.session_state.viewer_canvas_version += 1
                        st.rerun()
            else:
                st.info("🖱 Нарисуй прямоугольник для нового блока (drag)", icon=None)

        elif st.session_state.viewer_mode == "edit" and st.session_state.viewer_selected_block:
            # ── EDIT MODE: st_canvas transform (drag bbox выбранного блока) ──
            _sel = next(
                (b for b in all_blocks if b["block_id"] == st.session_state.viewer_selected_block),
                None,
            )
            if _sel:
                _bx = _sel.get("bbox", [0, 0, 100, 100])
                _ex1, _ey1, _ex2, _ey2 = _bx
                _initial = {
                    "version": "5.2.4",
                    "objects": [{
                        "type": "rect",
                        "left":   float(_ex1 * scale),
                        "top":    float(_ey1 * scale),
                        "width":  float((_ex2 - _ex1) * scale),
                        "height": float((_ey2 - _ey1) * scale),
                        "fill":   "rgba(255, 165, 0, 0.15)",
                        "stroke": "#FF8C00",
                        "strokeWidth": 3,
                        "scaleX": 1.0,
                        "scaleY": 1.0,
                        "angle":  0,
                        "selectable": True,
                    }],
                }
                edit_canvas_key = (
                    f"canvas_{doc_id}_p{st.session_state.viewer_page}"
                    f"_edit_{st.session_state.viewer_selected_block}"
                    f"_v{st.session_state.viewer_canvas_version}"
                )
                edit_result = st_canvas(
                    fill_color="rgba(255, 165, 0, 0.15)",
                    stroke_width=3,
                    stroke_color="#FF8C00",
                    background_image=annotated,
                    initial_drawing=_initial,
                    update_streamlit=True,
                    height=annotated.height,
                    width=img_w,
                    drawing_mode="transform",
                    key=edit_canvas_key,
                )
                if edit_result is not None and edit_result.json_data is not None:
                    _objs = [
                        o for o in edit_result.json_data.get("objects", [])
                        if o.get("type") == "rect"
                    ]
                    if _objs:
                        _o   = _objs[0]
                        _nl  = _o.get("left", 0)
                        _nt  = _o.get("top", 0)
                        _nw  = _o.get("width", 0) * _o.get("scaleX", 1)
                        _nh  = _o.get("height", 0) * _o.get("scaleY", 1)
                        nx1  = max(0, int(_nl / scale))
                        ny1  = max(0, int(_nt / scale))
                        nx2  = min(orig_w, int((_nl + _nw) / scale))
                        ny2  = min(orig_h, int((_nt + _nh) / scale))
                        # Сохраняем pending если координаты значимо изменились
                        if max(abs(nx1-_ex1), abs(ny1-_ey1), abs(nx2-_ex2), abs(ny2-_ey2)) > 3:
                            st.session_state["pending_bbox"]     = [nx1, ny1, nx2, ny2]
                            st.session_state["pending_bbox_for"] = st.session_state.viewer_selected_block
            else:
                st.session_state.viewer_mode = None

        else:
            # ── VIEW MODE: streamlit_image_coordinates (клик → выбор блока) ──
            coords = streamlit_image_coordinates(
                annotated,
                width=img_w,
                key=f"viewer_{doc_id}_{st.session_state.viewer_page}",
            )
            if coords is not None:
                ox = int(coords["x"] / scale)
                oy = int(coords["y"] / scale)
                clicked = None
                for b in all_blocks:
                    if b.get("page_num") != st.session_state.viewer_page:
                        continue
                    if b.get("block_type") not in show_types:
                        continue
                    bx = b.get("bbox", [])
                    if len(bx) == 4 and bx[0] <= ox <= bx[2] and bx[1] <= oy <= bx[3]:
                        clicked = b["block_id"]
                        break
                if clicked and clicked != st.session_state.viewer_selected_block:
                    st.session_state.viewer_selected_block = clicked
                    st.session_state.viewer_edit_mode      = False
                    st.session_state.viewer_confirm_delete = False
                    st.session_state.viewer_mode           = None
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
        for row in [page_blocks_filtered[i:i+4]
                    for i in range(0, len(page_blocks_filtered), 4)]:
            rcols = st.columns(4)
            for col, block in zip(rcols, row):
                bid     = block["block_id"]
                btype   = block.get("block_type", "?")
                bstatus = block.get("status", "")
                is_sel  = bid == st.session_state.viewer_selected_block
                s_icon  = {"needs_review": "🔴", "accepted": "✅",
                           "ocr_done": "🔵", "error": "❌"}.get(bstatus, "⚪")
                t_icon  = {"text": "📝", "table": "📊", "table_simple": "📊", "table_complex": "📊",
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
                        st.session_state.viewer_mode           = None
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
                f"<div style='padding:12px;background:rgba({rgb[0]},{rgb[1]},{rgb[2]},0.1);"
                f"border:1px solid {TYPE_HEX[draw_type]};border-radius:8px'>"
                f"<b>✏️ Режим рисования ({draw_type})</b><br><br>"
                f"1. Зажми кнопку мыши и <b>нарисуй прямоугольник</b><br>"
                f"2. Нажми <b>«Добавить блок»</b></div>",
                unsafe_allow_html=True,
            )
        else:
            st.caption("Кликни блок на изображении или выбери из списка")
        st.markdown("---")
        st.markdown("**📤 Экспорт**")
        for fmt, icon in [("markdown", "📝"), ("json", "🗂"), ("csv", "📊")]:
            col_gen, col_dl = st.columns(2)
            with col_gen:
                if st.button(f"{icon} {fmt.upper()}", key=f"exp_{fmt}", use_container_width=True):
                    try:
                        resp = httpx.post(
                            f"{BACKEND_URL}/processing/{doc_id}/export?format={fmt}", timeout=30
                        )
                        if resp.status_code == 200:
                            st.success("✅ Готов")
                        else:
                            st.error("Ошибка")
                    except Exception as e:
                        st.error(str(e))
            with col_dl:
                try:
                    dl = httpx.get(
                        f"{BACKEND_URL}/processing/{doc_id}/export-file/{fmt}", timeout=10
                    )
                    if dl.status_code == 200:
                        ext = {"markdown": "md", "json": "json", "csv": "csv"}[fmt]
                        st.download_button(
                            f"⬇ {fmt.upper()}",
                            data=dl.content,
                            file_name=f"{doc_id[:8]}_{fmt}.{ext}",
                            mime=dl.headers.get("content-type", "text/plain"),
                            key=f"dl_{fmt}",
                            use_container_width=True,
                        )
                    else:
                        st.caption("сначала сгенерируй →")
                except Exception:
                    st.caption("—")
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

    type_icon   = {"text": "📝", "table": "📊", "table_simple": "📊", "table_complex": "📊", "formula": "➗", "figure": "🖼"}.get(btype, "?")
    status_icon = {"needs_review": "🔴", "accepted": "🟢",
                   "ocr_done": "🔵", "error": "❌"}.get(bstatus, "⚪")

    st.markdown(f"**{type_icon} {btype.upper()}**")
    st.caption(f"`{selected_id}`")
    st.markdown(f"Статус: {status_icon} `{bstatus}`")
    st.markdown(f"Conf: `{conf:.2f}`")

    _preview_bust = st.session_state.get(f"preview_bust_{selected_id}", 0)
    _preview_bytes = fetch_block_image(doc_id, selected_id, _cache_bust=_preview_bust)
    if _preview_bytes:
        st.image(_preview_bytes, use_container_width=True)

    # ── Геометрия и тип ───────────────────────────────────────────────────
    with st.expander("✏️ Геометрия и тип", expanded=False):
        # Тип — сохраняется немедленно при изменении
        _type_opts = ["text", "table", "table_simple", "table_complex", "formula", "figure"]
        new_type = st.selectbox(
            "Тип",
            _type_opts,
            index=_type_opts.index(btype) if btype in _type_opts else 0,
            key=f"sel_type_{selected_id}_{btype}",  # key со значением → сброс при undo
        )
        if new_type != btype:
            _stk = st.session_state.undo_stack
            _stk.append({"action": "patch", "block_id": selected_id, "snapshot": dict(block)})
            if len(_stk) > 10:
                _stk.pop(0)
            try:
                _r = httpx.patch(
                    f"{BACKEND_URL}/processing/{doc_id}/blocks/{selected_id}",
                    json={"block_type": new_type},
                    timeout=5,
                )
                if _r.status_code == 200:
                    fetch_blocks.clear()
                    st.rerun()
                else:
                    _stk.pop()
                    st.error(f"Ошибка: {_r.text[:60]}")
            except Exception as _e:
                _stk.pop()
                st.error(str(_e))

        st.divider()

        # Геометрия — редактирование через drag на канвасе
        _edit_active = st.session_state.viewer_mode == "edit"
        if not _edit_active:
            if st.button(
                "📐 Редактировать геометрию",
                type="primary",
                use_container_width=True,
                key="btn_edit_geom",
            ):
                # Сбрасываем старый pending при входе в edit
                st.session_state.pop("pending_bbox", None)
                st.session_state.pop("pending_bbox_for", None)
                st.session_state.viewer_mode           = "edit"
                st.session_state.viewer_canvas_version += 1
                st.rerun()
            st.caption(f"bbox: {bbox[0]}, {bbox[1]} → {bbox[2]}, {bbox[3]}")
            st.caption(f"размер: {bbox[2]-bbox[0]} × {bbox[3]-bbox[1]} px")
        else:
            st.info("Тяни за углы или стороны рамки на изображении.", icon=None)

            _pending     = st.session_state.get("pending_bbox")
            _pending_for = st.session_state.get("pending_bbox_for")
            _has_pending = (
                _pending is not None
                and _pending_for == selected_id
                and _pending != list(bbox)
            )

            _sc1, _sc2 = st.columns(2)
            with _sc1:
                if st.button(
                    "💾 Сохранить",
                    type="primary",
                    disabled=not _has_pending,
                    use_container_width=True,
                    key="btn_save_geom",
                ):
                    _stk = st.session_state.undo_stack
                    _stk.append({"action": "patch", "block_id": selected_id, "snapshot": dict(block)})
                    if len(_stk) > 10:
                        _stk.pop(0)
                    try:
                        _r = httpx.patch(
                            f"{BACKEND_URL}/processing/{doc_id}/blocks/{selected_id}",
                            json={"bbox": _pending},
                            timeout=10,
                        )
                        if _r.status_code == 200:
                            # Инкрементируем bust превью → fetch_block_image вернёт новый кроп
                            _bust = st.session_state.get(f"preview_bust_{selected_id}", 0)
                            st.session_state[f"preview_bust_{selected_id}"] = _bust + 1
                            fetch_blocks.clear()
                            st.session_state.pop("pending_bbox", None)
                            st.session_state.pop("pending_bbox_for", None)
                            st.session_state.viewer_mode           = None
                            st.session_state.viewer_canvas_version += 1
                            st.rerun()
                        else:
                            _stk.pop()
                            st.error(f"Ошибка: {_r.text[:60]}")
                    except Exception as _e:
                        _stk.pop()
                        st.error(str(_e))
            with _sc2:
                if st.button(
                    "✕ Отмена",
                    type="secondary",
                    use_container_width=True,
                    key="btn_cancel_geom",
                ):
                    st.session_state.pop("pending_bbox", None)
                    st.session_state.pop("pending_bbox_for", None)
                    st.session_state.viewer_mode           = None
                    st.session_state.viewer_canvas_version += 1
                    st.rerun()

    # ── Удалить блок ──────────────────────────────────────────────────────
    if not st.session_state.viewer_confirm_delete:
        if st.button("🗑 Удалить блок", use_container_width=True, key="btn_del"):
            st.session_state.viewer_confirm_delete = True
    else:
        st.warning("⚠️ Удалить безвозвратно?")
        dc1, dc2 = st.columns(2)
        with dc1:
            if st.button("✅ Да", use_container_width=True, type="primary", key="btn_del_yes"):
                _stk = st.session_state.undo_stack
                _stk.append({"action": "delete", "block_id": selected_id, "snapshot": dict(block)})
                if len(_stk) > 10:
                    _stk.pop(0)
                try:
                    httpx.delete(
                        f"{BACKEND_URL}/processing/{doc_id}/blocks/{selected_id}", timeout=5
                    )
                except Exception:
                    pass
                st.session_state.viewer_selected_block  = None
                st.session_state.viewer_confirm_delete  = False
                fetch_blocks.clear()
                st.rerun()
        with dc2:
            if st.button("❌ Нет", use_container_width=True, key="btn_del_no"):
                st.session_state.viewer_confirm_delete = False

    st.markdown("---")

    # ── OCR Output ────────────────────────────────────────────────────────
    if output:
        st.markdown("**OCR Output:**")
        if btype in ("table", "table_simple", "table_complex"):
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
            st.text_area(
                "", value=output, height=120,
                disabled=not st.session_state.viewer_edit_mode,
                key=f"output_display_{selected_id}",
            )
    else:
        st.caption("Нет OCR output")

    if st.session_state.viewer_edit_mode and btype not in ("table", "table_simple", "table_complex", "formula"):
        new_output = st.text_area(
            "✏️ Редактировать:", value=output, height=120, key=f"edit_output_{selected_id}"
        )
        if st.button("💾 Сохранить текст", use_container_width=True,
                     type="primary", key="save_text"):
            try:
                resp = httpx.patch(
                    f"{BACKEND_URL}/processing/{doc_id}/blocks/{selected_id}",
                    json={"output": new_output, "status": "accepted"}, timeout=5,
                )
                if resp.status_code == 200:
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
        if st.button("✅ Принять", use_container_width=True, key="btn_accept",
                     help="Отметить блок как проверенный (без правки)"):
            httpx.patch(f"{BACKEND_URL}/processing/{doc_id}/blocks/{selected_id}",
                        json={"status": "accepted"}, timeout=5)
            fetch_blocks.clear()
            st.rerun()
        if st.button("✏️ Править", use_container_width=True, key="btn_edit",
                     help="Открыть редактор OCR output"):
            st.session_state.viewer_edit_mode = not st.session_state.viewer_edit_mode
    with a2:
        if st.button("🔖 В Review", use_container_width=True, key="btn_review",
                     help="Пометить как требующий проверки"):
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

    # ── КНОПКА "В ОБУЧЕНИЕ" ───────────────────────────────────────────────
    # Показываем только когда пользователь что-то правил
    if st.session_state.viewer_edit_mode or block.get("original_output"):
        st.markdown("---")
        orig = block.get("original_output") or output
        cur  = output

        if orig != cur:
            # Уже есть расхождение — предлагаем сохранить как пару
            st.markdown(
                "<div style='padding:8px;background:rgba(22,163,74,0.1);"
                "border-left:3px solid #16a34a;border-radius:4px;font-size:12px'>"
                "📊 Оригинал модели и текущий output отличаются</div>",
                unsafe_allow_html=True,
            )
            if st.button("📚 В обучение", use_container_width=True,
                         type="primary", key="btn_to_train"):
                try:
                    # Если пользователь в режиме правки — берём текст из textarea
                    edit_key = f"edit_output_{selected_id}"
                    edited_text = st.session_state.get(edit_key, cur)
                    train_orig   = orig if orig != cur else output
                    train_target = edited_text if edited_text != train_orig else cur

                    resp = httpx.post(
                        f"{BACKEND_URL}/training/pairs",
                        json={
                            "block_id":           selected_id,
                            "doc_id":             doc_id,
                            "block_type":         btype,
                            "source_page":        block.get("page_num"),
                            "bbox":               block.get("bbox"),
                            "image_path":         block.get("image_path"),
                            "local_model_output": train_orig,
                            "target_output":      train_target,
                        },
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        st.success(
                            f"✅ Пара #{data.get('pair_id','?')[:16]} сохранена. "
                            f"Всего: {data.get('total_pairs', '?')} пар"
                        )
                    else:
                        st.error(f"Ошибка: {resp.json().get('detail', resp.text[:80])}")
                except Exception as e:
                    st.error(str(e))

    st.markdown("---")
    st.markdown("---")
    st.markdown("**🔁 OCR:**")
    oc1, oc2 = st.columns(2)
    with oc1:
        if st.button("▶ Этот блок", use_container_width=True, key="btn_ocr_block",
                     help="Запустить OCR только для выбранного блока"):
            try:
                _btype    = next((b.get("block_type") for b in all_blocks if b.get("id") == selected_id), None)
                _model_id = st.session_state.get("viewer_model_choices", {}).get(_btype)
                resp = httpx.post(
                    f"{BACKEND_URL}/processing/{doc_id}/ocr-block/{selected_id}",
                    json={"model_id": _model_id} if _model_id else {},
                    timeout=60,
                )
                if resp.status_code == 200:
                    fetch_blocks.clear()
                    st.success("✅ OCR готов")
                    st.rerun()
                else:
                    st.error(f"{resp.status_code}: {resp.text[:60]}")
            except Exception as e:
                st.error(str(e))
    with oc2:
        if st.button("▶ Весь doc", use_container_width=True, key="btn_ocr_all",
                     help="Запустить OCR для всего документа в фоне"):
            try:
                _choices = st.session_state.get("viewer_model_choices", {})
                resp = httpx.post(
                    f"{BACKEND_URL}/processing/{doc_id}/ocr",
                    json={"model_choices": _choices},
                    timeout=10,
                )
                if resp.status_code == 200:
                    st.success("⏳ OCR запущен в фоне")
                else:
                    st.error(f"{resp.status_code}")
            except Exception as e:
                st.error(str(e))

    st.markdown("**📤 Экспорт**")
    for fmt, icon in [("markdown", "📝"), ("json", "🗂"), ("csv", "📊")]:
        col_gen, col_dl = st.columns(2)
        with col_gen:
            if st.button(f"{icon} {fmt.upper()}", key=f"exp2_{fmt}", use_container_width=True):
                try:
                    resp = httpx.post(
                        f"{BACKEND_URL}/processing/{doc_id}/export?format={fmt}", timeout=30
                    )
                    if resp.status_code == 200:
                        st.success("✅ Готов")
                    else:
                        st.error("Ошибка")
                except Exception as e:
                    st.error(str(e))
        with col_dl:
            try:
                dl = httpx.get(
                    f"{BACKEND_URL}/processing/{doc_id}/export-file/{fmt}", timeout=10
                )
                if dl.status_code == 200:
                    ext = {"markdown": "md", "json": "json", "csv": "csv"}[fmt]
                    st.download_button(
                        f"⬇ {fmt.upper()}",
                        data=dl.content,
                        file_name=f"{doc_id[:8]}_{fmt}.{ext}",
                        mime=dl.headers.get("content-type", "text/plain"),
                        key=f"dl2_{fmt}",
                        use_container_width=True,
                    )
                else:
                    st.caption("сначала сгенерируй →")
            except Exception:
                st.caption("—")
