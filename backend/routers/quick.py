"""
quick.py — быстрый режим: конвертация PDF целиком в Markdown
POST /quick/{doc_id}/run?parser=marker
GET  /quick/{doc_id}/status
GET  /quick/parsers
"""
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException

from auth.dependencies import verify_document_ownership
from database.models import Document

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))

router = APIRouter(prefix="/quick", tags=["quick"])

# Состояние задач: doc_id → {status, markdown, error, parser, started_at}
_jobs: dict[str, dict] = {}

# Маппинг parser_name → provider_id в settings.json
_PARSER_PROVIDER = {
    "llamaparse": "llamaparse",
    "gpt4o":      "openai",
    "claude":     "anthropic",
    "openrouter": "openrouter",
}


def _resolve_api_key(parser_name: str, api_key: str) -> str:
    """Если api_key пустой — пробуем взять из settings.json."""
    if api_key:
        return api_key
    provider = _PARSER_PROVIDER.get(parser_name)
    if not provider:
        return ""
    try:
        from routers.settings import _load_settings
        return _load_settings().get("keys", {}).get(provider, "")
    except Exception:
        return ""


def _run_parser(doc_id: str, parser_name: str, api_key: str):
    from pipeline.quick_parsers import get_parser

    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    try:
        meta     = json.loads(meta_file.read_text())
        pdf_path = DATA_DIR / "uploads" / doc_id / meta["filename"]

        parser   = get_parser(parser_name)
        resolved_key = _resolve_api_key(parser_name, api_key)
        markdown = parser.run(pdf_path, api_key=resolved_key)

        # Сохраняем результат
        out_dir = DATA_DIR / "results" / doc_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.md").write_text(markdown, encoding="utf-8")

        # Обновляем meta
        meta["status"]     = "done"
        meta["mode"]       = "quick"
        meta["parser"]     = parser_name
        meta["done_at"]    = datetime.utcnow().isoformat()
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

        _jobs[doc_id].update({"status": "done", "markdown": markdown})

    except Exception as e:
        _jobs[doc_id].update({"status": "error", "error": str(e)})
        # Пишем error в meta если файл существует
        try:
            meta = json.loads(meta_file.read_text())
            meta["status"] = "error"
            meta["error"]  = str(e)
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        except Exception:
            pass


@router.get("/parsers", summary="Список доступных парсеров")
async def list_parsers():
    from pipeline.quick_parsers import ALL_PARSERS
    return [
        {
            "name":           p.name,
            "label":          p.label,
            "description":    p.description,
            "needs_api_key":  p.needs_api_key,
            "available":      p.is_available(),
        }
        for p in ALL_PARSERS
    ]


@router.post("/{doc_id}/run", summary="Запустить быстрый парсинг")
async def run_quick(
    doc_id: str,
    payload: dict = Body(...),
    _doc: Document = Depends(verify_document_ownership),
):
    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")

    meta = json.loads(meta_file.read_text())
    if meta.get("status") not in ("split_done", "error"):
        raise HTTPException(
            status_code=409,
            detail=f"Документ в статусе '{meta.get('status')}', нельзя запустить парсер"
        )

    parser_name = payload.get("parser", "pymupdf")
    api_key     = payload.get("api_key", "")

    from pipeline.quick_parsers import get_parser
    parser = get_parser(parser_name)
    if not parser.is_available():
        raise HTTPException(
            status_code=422,
            detail=f"Парсер '{parser_name}' не установлен"
        )
    if parser.needs_api_key:
        resolved = _resolve_api_key(parser_name, api_key)
        if not resolved:
            raise HTTPException(
                status_code=422,
                detail=f"Парсер '{parser_name}' требует API key — задайте в Settings или введите вручную"
            )
        api_key = resolved

    _jobs[doc_id] = {
        "status":     "running",
        "parser":     parser_name,
        "started_at": datetime.utcnow().isoformat(),
        "markdown":   None,
        "error":      None,
    }

    meta["status"] = "quick_processing"
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    threading.Thread(
        target=_run_parser, args=(doc_id, parser_name, api_key), daemon=True
    ).start()

    return {"status": "running", "parser": parser_name, "doc_id": doc_id}


@router.get("/{doc_id}/status", summary="Статус быстрого парсинга")
async def quick_status(
    doc_id: str,
    _doc: Document = Depends(verify_document_ownership),
):
    job = _jobs.get(doc_id)
    if not job:
        # Проверяем файл — вдруг уже готов с прошлого запуска
        result_file = DATA_DIR / "results" / doc_id / "result.md"
        if result_file.exists():
            return {"status": "done", "doc_id": doc_id,
                    "markdown": result_file.read_text(encoding="utf-8")}
        raise HTTPException(status_code=404, detail=f"Задача для {doc_id} не найдена")
    return {"doc_id": doc_id, **job}
