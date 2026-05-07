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
- `pages/0_Auth.py` — login/register, auto-login after register, smart redirect (docs→1_My_Documents / no docs→2_Upload)
- `pages/1_My_Documents.py` — document library picker + canvas block editor (1000+ LOC); picker: search/sort/cards/delete/download
- `pages/2_Upload.py` — PDF upload, quick vs detail layout, uses `/users/me/api-keys`
- `pages/3_Settings.py` — API keys per provider
- `pages/4_Profile.py` — profile, password change
- `pages/5_Viewer.py` — edit markdown, LaTeX toolbar, find & replace
- `components/auth_guard.py` — `require_auth()`, `render_sidebar_user()`, auth helpers
- `utils/auth.py` — `ensure_authenticated()`: token refresh (5 min before expiry), inactivity logout (4 h), redirect to 0_Auth
- `utils/styles.py` — `inject_global_styles()`: CSS vars + primary button brand color (#7C3AED)

**Frontend API pattern:**
- Each page defines a local `api(method, path, **kw)` that reads `st.session_state["access_token"]` and injects `Authorization: Bearer` header; returns raw `httpx.Response` or `None`
- `@st.cache_data` helpers accept `token: str = ""` as explicit param (so cache key includes the token); call them with `st.session_state.get("access_token", "")`
- Never use bare `httpx.*` calls outside of cache helpers
- Page boot order: `set_page_config` → `ensure_authenticated()` → `inject_global_styles()` → page logic

**Session state keys (auth):** `access_token`, `refresh_token`, `access_token_exp` (Unix ts), `last_activity_ts`, `current_user`, `user_display_name`, `auth_message` (shown on 0_Auth after redirect)

**Viewer session state:** `viewer_doc_id`, `viewer_page`, `viewer_selected_block`, `viewer_draw_mode`, `viewer_mode` ("edit" or None), `viewer_canvas_version`, `undo_stack` (max 10)

**Canvas modes:** View (click to select) | Draw (st_canvas rect) | Edit (st_canvas transform)

**Markdown export (two steps, must follow order):**
1. `POST /processing/{doc_id}/export?format=markdown` — generates `export.md`
2. `GET /processing/{doc_id}/export-file/markdown` — downloads the file
(or `GET /processing/{doc_id}/export-zip` for ZIP with images)

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

## Current Status (2026-05-07)

**Last completed (2026-05-07):**
- ✅ My Documents picker: search/sort/cards with relative timestamps, ···popover (re-parse, download .md, delete)
- ✅ `GET /documents/` now returns `created_at` from DB record
- ✅ Download .md fixed: correct two-step POST→GET flow (same as Viewer)

**Last completed (2026-05-06):**
- ✅ Auth page redesigned: "pdf2md — Sign in", auto-login after register, smart redirect (has docs → My Documents, no docs → Upload)
- ✅ Navigation restructured: pages renamed 0_Auth / 1_My_Documents / 2_Upload / 3_Settings / 4_Profile / 5_Viewer
- ✅ Auth page hidden from sidebar (CSS `display:none` on `href$="/Auth"` nav link + full sidebar hidden on auth page itself)
- ✅ `utils/auth.py` — `ensure_authenticated()`: proactive token refresh 5 min before expiry, 4 h inactivity logout
- ✅ `utils/styles.py` — `inject_global_styles()`: CSS vars + primary button #7C3AED, called on all pages
- ✅ Rebrand PRMS → pdf2md across all frontend files

**Features:**
- ✅ Multi-user with JWT tokens (30 min access, 14 day refresh)
- ✅ Document ownership checks (admin bypass)
- ✅ API key encryption (Fernet) — per-user in DB, with settings.json fallback
- ✅ Silent token refresh (proactive, no page reload)
- ✅ Inactivity logout after 4 hours
- ✅ Smart post-login redirect: existing docs → My Documents, new user → Upload
- ✅ Polling OCR with cancel support
- ✅ Block undo/redo (max 10)
- ✅ Draw/edit blocks via canvas
- ✅ Markdown export with ZIP + images
- ✅ Find & Replace + LaTeX toolbar
- ✅ 8 parsers (4 local + 4 cloud)
- ✅ Auto-fix OCR: superscripts, subscripts, currency

**Next priorities:**
1. Test full auth cycle: register → login → upload → OCR → export via UI
2. Deploy with ~20GB models on RTX 4090

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
