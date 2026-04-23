# CLAUDE.md

Guidance for Claude Code when working with this repository.

## Stack

- **Backend**: FastAPI (Python), PostgreSQL, Redis, Ollama (GPU)
- **Frontend**: Streamlit
- **Infra**: Docker Compose, RTX 4090, Alembic migrations

## Quick Start

```bash
make up                           # Start all services
make pull-model                  # Pull Qwen2.5-VL (~6 GB, 5 min)
docker compose restart backend   # Restart after code changes (hot-reload)
docker compose restart frontend
make logs-backend
```

UI: `http://localhost:8501` | Manual testing only (no test suite)

## Architecture

**Data flow:** PDF → pages (300 DPI PNG) → layout detection (DocLayout-YOLO) → blocks.json → per-block OCR → export (MD/JSON/CSV)

**Services:**
| Service   | Port  | Notes |
|-----------|-------|-------|
| ollama    | 11434 | Qwen2.5vl:7b/3b; GPU |
| backend   | 8000  | FastAPI; GPU; ~2 min startup |
| frontend  | 8501  | Streamlit; no GPU |
| postgres  | 5432  | Users, documents, tokens (internal) |
| redis     | 6379  | Session cache (internal) |

**OCR routing:** text→EasyOCR | table→dots.ocr | formula→TexTeller | figure→Ollama/cloud

**Block types:** `text`, `table_simple`, `table_complex`, `formula`, `figure`

**Storage:** `data/uploads/{doc_id}/, data/pages/{doc_id}/, data/results/{doc_id}/blocks.json` (central state)

**Key backend files:**
- `main.py` — FastAPI app, ModelRegistry singleton
- `routers/auth.py` — JWT + bcrypt auth (2026-04-17)
- `routers/users.py` — `/users/me` profile, password, API keys per-user encrypted (2026-04-21)
- `routers/documents.py` — upload, ownership checks
- `routers/processing.py` — OCR, export, block CRUD
- `routers/quick.py` — fast parsing (marker/llamaparse/gpt4o/claude), per-user DB key lookup
- `routers/settings.py` — public `/available-models` only (legacy `/settings/keys/raw` removed)
- `pipeline/ocr_pipeline.py` — model routing, VRAM mgmt
- `database/models.py` — User, Document, UserApiKey, RefreshToken (SQLAlchemy + Alembic)
- `database/crud/*.py` — CRUD for users, documents, api_keys

**Key frontend files:**
- `pages/0Auth.py` — login/register tabs, token storage (2026-04-21)
- `components/auth_guard.py` — `require_auth()`, `render_sidebar_user()`, auth helpers (2026-04-21)
- `pages/1_Upload.py` — PDF upload, quick vs detail layout, uses `/users/me/api-keys`
- `pages/2_Viewer.py` — canvas, draw/edit blocks, undo (1000+ LOC)
- `pages/4_MarkdownViewer.py` — edit markdown, LaTeX toolbar, find & replace

**Frontend API pattern:**
- Each page defines a local `api(method, path, **kw)` that reads `st.session_state["access_token"]` and injects `Authorization: Bearer` header; returns raw `httpx.Response` or `None`
- `@st.cache_data` helpers accept `token: str = ""` as explicit param (so cache key includes the token); call them with `st.session_state.get("access_token", "")`
- Never use bare `httpx.*` calls outside of cache helpers

**Viewer session state:** `viewer_doc_id`, `viewer_page`, `viewer_selected_block`, `viewer_draw_mode`, `viewer_mode` ("edit" or None), `viewer_canvas_version`, `undo_stack` (max 10)

**Canvas modes:** View (click to select) | Draw (st_canvas rect) | Edit (st_canvas transform)

**Notes:**
- `backend/shared/` is canonical; `shared/` is a mirror — keep in sync
- `PATCH /blocks` with `bbox` re-crops image from page PNG
- Canvas click replay guard: `canvas_last_coord` prevents duplicate clicks

## Environment Variables

```
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_FALLBACK_MODEL=qwen2.5vl:7b
OLLAMA_FIGURE_MODEL=qwen2.5vl:3b
OLLAMA_FORMULA_MODEL=qwen2.5vl:3b
PDF_DPI=300
PYTORCH_ALLOC_CONF=expandable_segments:True

# Auth / Database (required)
DATABASE_URL=postgresql+asyncpg://prms:${POSTGRES_PASSWORD}@postgres:5432/prms
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
JWT_SECRET_KEY=<64-char hex>
FERNET_KEY=<base64 32-byte key>
```

## Current Status (2026-04-22)

**Last completed (2026-04-22):**
- ✅ Auth token injected in all API calls in `2_Viewer.py` and `4_MarkdownViewer.py` — fixed 401 errors on page images, blocks, block previews
- ✅ Local `api(method, path, **kw)` helper pattern established in all frontend pages

**Last completed (2026-04-21):**
- ✅ `/users/me` router — profile, password, per-user encrypted API keys
- ✅ Removed legacy `/settings/keys/raw` (security hole — plaintext keys in JSON)
- ✅ Frontend: login/register page (`0Auth.py`)
- ✅ Auth guard on all protected pages (1_Upload, 2_Viewer, 4_MarkdownViewer)
- ✅ Sidebar user block with logout

**Features:**
- ✅ Multi-user with JWT tokens (30 min access, 14 day refresh)
- ✅ Document ownership checks (admin bypass)
- ✅ API key encryption (Fernet) — per-user in DB, with settings.json fallback
- ✅ Frontend auth flow: register → login → token storage → app access
- ✅ Bearer token injected in all API calls across all pages
- ✅ Polling OCR with cancel support
- ✅ Block undo/redo (max 10)
- ✅ Draw/edit blocks via canvas
- ✅ Markdown export with ZIP + images
- ✅ Find & Replace + LaTeX toolbar
- ✅ 8 parsers (4 local + 4 cloud)
- ✅ Auto-fix OCR: superscripts, subscripts, currency

**Known issues:**
- `0Auth.py` and `0_Settings.py` both start with `0` (alphabetically `0Auth` first)

**Next priorities:**
1. Test full auth cycle: register → login → upload → OCR → export via UI
2. Token refresh on 401 (currently just logs out)
3. Deploy with ~20GB models on RTX 4090

## Compact Instructions

- **Prefer editing existing files** over creating new ones
- **No comments** unless WHY is non-obvious (hidden constraints, subtle invariants)
- **No error handling for impossible scenarios** (trust framework guarantees, validate only at system boundaries)
- **Don't introduce abstractions** beyond task scope
- **Test UI features** manually at `http://localhost:8501` before marking complete
- **Keep `backend/shared/schemas.py` in sync** with `shared/schemas.py`
- **Rate limits** in slowapi module (limiter.py) to avoid circular imports
- **Auth dependency:** `verify_document_ownership(doc_id: str)` matches path param name
- **VRAM:** dots.ocr stays on GPU; blocks sorted by model_family before processing
