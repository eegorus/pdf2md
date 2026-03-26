# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack

- **Backend**: FastAPI (Python), `backend/`
- **Frontend**: Streamlit, `frontend/pages/`
- **Shared**: schemas and utilities, `shared/`
- **Infra**: Docker Compose, GPU (RTX 4090), Ollama

## Common Commands

```bash
# Start all services (build + up)
make up

# After first start — pull Qwen2.5-VL model (~6-8 GB, ~5 min)
make pull-model

# Restart specific containers after code changes
docker compose restart backend      # backend has bind-mount, reload picks up changes
docker compose restart frontend     # frontend has bind-mount, reload picks up changes

# View logs
make logs
make logs-backend

# Health check
make health

# Fine-tuning (GPU-intensive, stops backend/ollama first)
make finetune-start
make finetune-stop
```

No test suite exists. Manual testing via UI at `http://localhost:8501`.

## Architecture

### Data flow

```
PDF upload → split to PNG pages (300 DPI)
         → layout detection (DocLayout-YOLO) → blocks.json
         → per-block OCR routing → output (HTML/text/LaTeX)
         → export (markdown / JSON / CSV)
```

User corrections are collected as training pairs → QLoRA fine-tune of Qwen2.5-VL.

### Services

| Service   | Port  | Notes |
|-----------|-------|-------|
| ollama    | 11434 | Qwen2.5vl:7b (fallback) + 3b (figure), GPU, 10m keep-alive |
| backend   | 8000  | FastAPI, GPU, ~2 min startup (model loading) |
| frontend  | 8501  | Streamlit, no GPU |
| finetune  | —     | Manual launch via `--profile finetune` |

### OCR model routing

```
text         → EasyOCR (ru+en)
table_simple / table_complex → dots.ocr (Qwen2-VL local)
formula      → TexTeller CLI → LaTeX
figure       → Ollama or cloud API
fallback     → Ollama (qwen2.5vl:7b) → cloud (OpenRouter / OpenAI / Anthropic)
```

### Block types

`text`, `table_simple`, `table_complex`, `formula`, `figure`

### Storage layout

```
data/
├── uploads/{doc_id}/          # meta.json, original.pdf
├── pages/{doc_id}/            # page_001.png … (300 DPI)
├── results/{doc_id}/
│   ├── blocks.json            # all block detections + OCR outputs
│   ├── blocks/{block_id}.png  # cropped block images
│   └── export.{md,json,csv}
├── training/                  # pairs.jsonl, train_dataset.json
├── models/versions/           # fine-tuned checkpoints
└── settings.json              # API keys (plaintext on disk)
```

`blocks.json` is the central state file. All PATCH/DELETE/POST /blocks endpoints modify it in-place.

### Key backend files

- `backend/main.py` — FastAPI app, `ModelRegistry` singleton loaded at startup
- `backend/routers/processing.py` — layout detection, OCR, export, block CRUD
- `backend/routers/documents.py` — upload, PDF split, page-image serving
- `backend/routers/training.py` — training pairs, fine-tune launch, model switching
- `backend/pipeline/layout_detector.py` — DocLayout-YOLO wrapper; tuning constants `MERGE_GAP_PX`, `TABLE_COMPLEX_RATIO`
- `backend/pipeline/ocr_pipeline.py` — routes blocks to the right model

### Key frontend files

- `frontend/pages/2_Viewer.py` — main interactive canvas (~1000+ LOC); block overlay, draw mode, edit geometry, undo stack
- `frontend/pages/1_Upload.py` — upload flow, quick-parse vs detail-layout choice

### Viewer session state keys

| Key | Purpose |
|-----|---------|
| `viewer_doc_id` | current document |
| `viewer_page` | current page number |
| `viewer_selected_block` | block_id of selected block |
| `viewer_draw_mode` | bool — draw new block mode (uses `st_canvas`) |
| `viewer_draw_type` | type for new block |
| `viewer_mode` | `None` or `"edit"` — drag-edit selected block bbox |
| `viewer_canvas_version` | int — increment to reset `st_canvas` state |
| `undo_stack` | list of `{action, block_id, snapshot}`, max 10 |
| `pending_bbox` / `pending_bbox_for` | unsaved bbox from drag edit |
| `preview_bust_{block_id}` | `int(time.time())` to bust browser image cache |

### Two canvas widgets (2_Viewer.py)

- **View mode** (`viewer_draw_mode=False`, `viewer_mode=None`): `streamlit_image_coordinates` — click to select block
- **Draw mode** (`viewer_draw_mode=True`): `st_canvas(drawing_mode="rect")` — drag to draw new block
- **Edit mode** (`viewer_mode="edit"`): `st_canvas(drawing_mode="transform")` — drag handles to resize existing block; saves on explicit "💾 Сохранить" click

Both draw/edit canvases use `viewer_canvas_version` in the key to force a fresh canvas on mode changes.

### PATCH /blocks re-crops image

When `bbox` is in the PATCH payload, `processing.py` re-crops `blocks/{block_id}.png` from the original page PNG. Always PATCH bbox together with any type change if both change simultaneously.

### Two `shared/` directories

`backend/shared/` and `shared/` exist separately — the Dockerfile bind-mounts `./shared` to `/app/shared`. The backend's `backend/shared/schemas.py` is the canonical version actually used at runtime; `shared/schemas.py` at repo root is a lighter copy. Keep them in sync when modifying schemas.

## Session log — 2026-03-26

### ✅ Markdown Viewer (первая версия) — завершено

**Новая страница `frontend/pages/4_MarkdownViewer.py`:**
- Список документов с фильтром по статусу `ocr_done`
- Три режима: 👁 Просмотр (рендер markdown), ✏️ Редактор (text_area), ↕️ Split (side-by-side)
- Кэширование контента в `session_state` с ключами `md_edit_{doc_id}` и `md_dirty_{doc_id}`
- Отслеживание изменений: кнопка Сохранить активна только если `is_dirty=True`
- Кнопки: Сохранить (primary), Сбросить (reset), Скачать (download)
- Auto-generate экспорта если `export.md` не существует

**Backend PATCH endpoint:**
- Добавлен `PATCH /{doc_id}/export-file/markdown` в `backend/routers/processing.py` (lines 408–431)
- Создаёт `.bak` перед перезаписью, возвращает `{"saved": true, "doc_id": ..., "size": ...}`
- Валидация: контент не может быть пустым, файл должен существовать

**Frontend — переход из Viewer:**
- Кнопка "📄 Открыть в MD Viewer" в `_render_export_buttons()` (2_Viewer.py, lines 133–135)
- Устанавливает `md_viewer_doc_id` в session_state и переходит на новую страницу

**Фикс**:
- `fetch_documents()` вызывает `/documents/` (с trailing slash) — httpx не следит редирект 307

### Текущий статус
- ✅ Markdown viewer полностью функционален на фронтенде
- ✅ Сохранение отредактированного markdown на бэкенде
- ✅ Навигация между Viewer и MD Viewer
- Протестировано с 3 документами статуса `ocr_done`

---

### ❌ Попытка: Canvas flash fix — удаление selection из hash (не сработало)

**Проблема**: Viewer canvas периодически пропадает/сворачивается (~50% случаев) при клике на блок, добавлении нового блока, перелистывании, редактировании.

**Гипотеза**: `streamlit_image_coordinates` коллапсит при перезагрузке изображения браузером. Каждый выбор блока вызывает `viewer_selected_block` change → `draw_blocks_on_image()` возвращает новый PIL object → Streamlit генерирует новый URL → браузер перезагружает изображение → **flash ~50% случаев**.

**Попытанный fix** (lines 636–658 в 2_Viewer.py):
- Убрать `viewer_selected_block` из `_annot_hash` (оставить только `show_types` + блоки на странице)
- Передать `selected_id=None` в `draw_blocks_on_image()` (без жёлтого контура на выбранном блоке)
- Selection feedback остаётся через `▶` префикс в кнопке блока + детали в правой панели
- PIL object в session_state должен был оставаться стабильным → тот же Streamlit URL → нет browser reload

**Результат**: Не сработало. Canvas всё ещё коллапсит при клике на блок через buttons below или image.

**Возможные причины**:
1. `streamlit_image_coordinates` UI component сам перезагружает браузер при любом rerun
2. Хеш всё ещё меняется из-за другого фактора (и check_session_state?)
3. PIL object в session_state не сохраняется между reruns
4. Симптом: перезагрузка происходит ещё до rerun (браузер теряет focus на canvas)

**Следующие идеи для расследования**:
- Переключиться с `streamlit_image_coordinates` на `st.image()` + custom JS click handler (требует обработки координат вручную)
- Профилировать через браузер DevTools когда happens canvas collapse — есть ли XHR rerun?
- Проверить есть ли в 2_Viewer.py безусловный `st.rerun()` который срабатывает после block click
- Кэшировать `annotated` не в session_state а в глобальную переменную (обход rerun-очистки?)
- Попробовать использовать `@st.experimental_fragment()` для изоляции canvas от остального UI

---

## Session log — 2026-03-25

### ✅ Завершено в этой сессии

**1. Сортировка блоков в экспорте (✅ завершено)**
- `blocks_to_markdown()`: сортировка по `(page_num, y1, x1)` вместо `block_idx`
- `POST /blocks`: новые блоки вставляются в правильную позицию в `blocks.json` по визуальному порядку
- Отредактированные блоки теперь появляются на месте, а не в конце

**2. Инфраструктура Docker (✅ завершено)**
- `docker-compose.yml`: добавлен `runtime: nvidia` для ollama
- OLLAMA_KEEP_ALIVE: 24h → 10m (сбережение VRAM при простое)
- OLLAMA_NUM_PARALLEL: 2 → 1 (OCR-pipeline строго последовательный)
- Опции: NVIDIA_VISIBLE_DEVICES, NVIDIA_DRIVER_CAPABILITIES

**3. Модели Ollama (✅ завершено)**
- Скачали `qwen2.5vl:3b` (~2.3 GB) — быстрая обработка фигур
- Добавлена env var `OLLAMA_FIGURE_MODEL` (дефолт: 3b)
- `OLLAMA_FALLBACK_MODEL` остаётся на 7b для fallback (text, formula, table)
- В settings: выбор figure моделей `ollama_3b` (быстро) и `ollama_7b` (качество)

**4. Условный VRAM offload (✅ завершено)**
- `HEAVY_FIGURE_MODELS` constant: offload dots.ocr только для 7b/72b модели
- Для 3b offload не требуется (9.4 GB < 24 GB RTX 4090)
- Логирование: когда offload нужен и когда нет
- Восстановлены методы `_unload_table_model()` / `_reload_table_model()` для будущего расширения

**5. Figure embedding в Markdown (✅ завершено)**
- Картинки фигур: resize до max 1200px, encode как base64 PNG
- Формат: `![alt](data:image/png;base64,...)`
- Alt-текст очищен: убраны переносы, кавычки, скобки
- Типичный кроп 500 KB → 80-120 KB после resize

**6. Горизонтальный скроллинг таблиц (✅ завершено)**
- HTML-таблицы обёрнуты в `<div style="overflow-x: auto">`
- `table_recognizer.py`: добавлены inline-стили `border-collapse: collapse; min-width: 600px`
- Скролл показывается только когда таблица шире viewport

### Текущий статус
**Экспорт в Markdown полнофункционален:**
- ✅ Текст, таблицы (с горизонтальным скроллом), формулы, картинки (base64)
- ✅ Правильный визуальный порядок блоков
- ✅ Оптимизирован размер файла
- ✅ Работает в Obsidian и других Markdown-просмотрщиках

## Session log — 2026-03-24

### Сделано

**1. Статус моделей (✅ завершено)**
- Убран circular HTTP запрос в `/available-models` → теперь прямой импорт моделей из синглтона
- Все локальные модели показываются как ✅ доступные (easyocr, dots_ocr, texteller, ollama)

**2. Экспорт в Markdown/JSON/CSV (✅ завершено)**
- Убрано дублирование кода экспорта — единая функция `_render_export_buttons()`
- Байты кэшируются в `session_state` → download_button не пропадает при rerun
- Использование `st_canvas` для drawing без кнопки подтверждения
- Сброс кэша при смене документа

**3. OCR — Polling архитектура (✅ завершено)**
- Backend: добавлены `POST /ocr` (запускает в фоне, возвращает сразу) и `GET /{doc_id}/ocr-status`
- Хранилище `_ocr_status: dict` в памяти с полями {status, processed, total, errors, error_msg}
- Callback `on_progress()` обновляет статус каждые 10 блоков
- Frontend: вместо `timeout=600` с spinner, теперь polling блок каждые 3 сек с progress-bar
- Сортировка блоков по типу (text/table/formula → figure) для оптимальной работы с VRAM

**4. VRAM management (✅ завершено)**
- Выгрузка dots.ocr на CPU один раз перед первым figure-блоком
- Более подробное логирование свободной VRAM после выгрузки
- `hasattr()` проверка перед доступом к `table_model`

**5. Markdown экспорт (✅ завершено)**
- HTML wrapper `<html><body>` теперь удаляется из markdown — regex вырезает только `<table>...</table>`
- `max_new_tokens` увеличен с 2048 до 8192 для широких таблиц (10+ колонок)

**6. Viewer (✅ завершено)**
- Viewer самостоятельно показывает список всех документов с кнопками "Открыть"
- Список открывается если `viewer_doc_id` не установлен (вместо ошибки "выберите в Upload")
- Кнопка "↩ Сменить документ" в левой панели для быстрого переключения
- Документы фильтруются: открывать можно только при статусе `layout_done` или `ocr_done`

**7. Upload — обработка повторного /start (✅ завершено)**
- `POST /start` теперь возвращает 400 с `detail="layout_already_done"` при уже обработанных документах
- Frontend ловит эту ошибку и сразу открывает Viewer через `st.switch_page()`
- Пользователь может перетестировать тот же документ без повторной загрузки

## Session log — 2026-03-23

### Сделано

**Инфраструктура**
- Добавлен bind-mount `./frontend:/app` в `docker-compose.yml` — теперь frontend тоже hot-reload без rebuild (как backend)
- Исправлен `backend/main.py`: удалён несуществующий импорт `ProcessingStatus`
- Исправлен `backend/shared/schemas.py`: `HealthResponse.version` получил дефолт `"1.1.0"`, поля `gpu_memory_*` стали `Optional[float]`

**Viewer — Undo**
- В `_defaults` добавлен `undo_stack: []` (max 10 записей)
- Пуш в стек: перед `DELETE`, после успешного `POST /blocks`, перед `PATCH` геометрии
- Кнопка "↩ (N)" в верхнем тулбаре рядом с "Рисовать"; disabled когда стек пуст
- Логика undo вынесена в функцию `_do_undo()` — замыкание над `doc_id`/`BACKEND_URL`/`fetch_blocks`
- `action=delete` → `POST /blocks` (новый id), `action=add` → `DELETE`, `action=patch` → `PATCH` снапшота

**Viewer — рисование блоков (drag-and-drop)**
- Заменён двухкликовый механизм (pt1→pt2) на `st_canvas(drawing_mode="rect")` — drag-and-drop
- Библиотека `streamlit-drawable-canvas==0.9.3` заменена на `streamlit-drawable-canvas-fix` (форк, совместимый со Streamlit ≥ 1.41; оригинал сломан из-за переезда `image_to_url`)
- Состояния `viewer_draw_pt1`, `viewer_draw_preview`, `viewer_draw_last_hash` удалены
- Добавлен `viewer_canvas_version: int` — инкрементируется для сброса канваса при смене режима/страницы/документа
- В режиме view остался `streamlit_image_coordinates` (клик → выбор блока)
- Тулбар перестроен: `tc1` (Рисовать), `tc2` (тип), `tc3` (↩ undo)

**Viewer — редактирование геометрии**
- Expander "Геометрия и тип": убраны `number_input` x1/y1/x2/y2 и кнопка "Сохранить геометрию"
- Тип блока теперь сохраняется **немедленно** при изменении selectbox + undo push
- Кнопка "📐 Редактировать геометрию" → `viewer_mode="edit"` → `st_canvas(drawing_mode="transform")` с `initial_drawing` bbox блока
- Изменение bbox сохраняется в `pending_bbox` / `pending_bbox_for` (без автосейва)
- Кнопки "💾 Сохранить" (disabled пока нет pending) и "✕ Отмена"
- `viewer_mode` сбрасывается в `None` при смене блока, страницы, документа

**Backend — перекроп при PATCH**
- `PATCH /blocks/{id}` с полем `bbox` теперь перекропирует `blocks/{block_id}.png` из страничного PNG и обновляет `image_path` в `blocks.json`

**Превью блока в правой панели**
- Убрана `fetch_block_image` (кэшированная функция — кэш мешал обновлению)
- Вместо этого: `st.image(f"...block-image/{id}?t={bust}")` — браузер видит новый URL при каждом сохранении
- `preview_bust_{block_id}` = `int(time.time())` после успешного PATCH bbox

### Приоритет (2026-03-24)

**Текущие задачи:**
- **Фикс статуса моделей** — backend должен корректно сообщать о доступности моделей (статус, загрузка и т.д.)
- **Запуск OCR с выбором модели** — фронтенд: dropdown choices для выбора модели (EasyOCR, dots.ocr, Ollama, cloud), spinner прогресса обработки
- **Цикл конвертации в Markdown** — полный цикл OCR → документ в детальном режиме использования (detail layout)

**Отложено (не приоритет):**
- **Table merge** — заголовок таблицы при детекции разбивается в отдельный блок, нужен анализ bbox

## Environment variables

Defined in `.env.example`. Key ones:

```
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_FALLBACK_MODEL=qwen2.5vl:7b          # fallback для text/formula/table
OLLAMA_FIGURE_MODEL=qwen2.5vl:3b            # figure обработка (быстро)
PDF_DPI=300
BLOCK_CONFIDENCE_THRESHOLD=0.3
MIN_PAIRS_FOR_FINETUNE=50
DATA_DIR=/app/data
MODELS_DIR=/app/models
PYTORCH_ALLOC_CONF=expandable_segments:True
```
