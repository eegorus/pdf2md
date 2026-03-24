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
| ollama    | 11434 | Qwen2.5vl:7b, GPU, 24h keep-alive |
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

## Session log — 2026-03-24

### Статус моделей

**Фикс: убран circular HTTP запрос в `/available-models`**
- Было: `GET /health` → HTTP `localhost:8000/health` (циклический запрос)
- Стало: прямой импорт `from main import models` + чтение `models.status` из синглтона
- Все локальные модели теперь корректно показываются как ✅ doступные (easyocr, dots_ocr, texteller, ollama)
- Добавлен лог при ошибке импорта для отладки

**Текущий приоритет (WIP):**
- ✅ Фикс статуса моделей — backend корректно читает models.status из памяти
- **Передача model_choices в OCR endpoint** — фронтенд отправляет выбранную модель при запросе OCR
- Фронтенд: UI для выбора модели OCR (dropdown choices)
- Spinner прогресса во время обработки блоков
- Полный цикл OCR → Markdown в detail-layout режиме

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
OLLAMA_FALLBACK_MODEL=qwen2.5vl:7b
PDF_DPI=300
BLOCK_CONFIDENCE_THRESHOLD=0.3
MIN_PAIRS_FOR_FINETUNE=50
DATA_DIR=/app/data
MODELS_DIR=/app/models
PYTORCH_ALLOC_CONF=expandable_segments:True
```
