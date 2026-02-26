"""
Роутер: управление документами
POST /documents/upload  — загрузка и первичный разбор PDF
GET  /documents/        — список документов
GET  /documents/{doc_id}/pages — страницы документа
"""
import os
import json
import logging
from pathlib import Path

from fastapi import APIRouter, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

import sys
sys.path.insert(0, "/app")
from shared.utils import generate_doc_id, ensure_dir

logger = logging.getLogger("prms.router.documents")
router = APIRouter()

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
MAX_MB   = int(os.getenv("MAX_UPLOAD_SIZE_MB", "100"))


def _run_splitting(pdf_path: Path, doc_id: str):
    """Фоновая задача: разбиваем PDF на страницы."""
    from pipeline.pdf_splitter import PDFSplitter
    splitter = PDFSplitter(data_dir=DATA_DIR, dpi=int(os.getenv("PDF_DPI", "300")))
    pages = splitter.split(pdf_path, doc_id)

    # Сохраняем мета-информацию
    meta_path = DATA_DIR / "uploads" / doc_id / "meta.json"
    meta = {
        "doc_id":      doc_id,
        "filename":    pdf_path.name,
        "page_count":  len(pages),
        "pages":       pages,
        "status":      "split_done",
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    logger.info(f"✅ Документ {doc_id} разбит на {len(pages)} страниц")


@router.post("/upload", summary="Загрузить PDF")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    # Проверяем тип файла
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Только PDF файлы")

    # Читаем содержимое
    content = await file.read()

    # Проверяем размер
    size_mb = len(content) / 1024 / 1024
    if size_mb > MAX_MB:
        raise HTTPException(
            status_code=413,
            detail=f"Файл слишком большой: {size_mb:.1f} МБ (макс. {MAX_MB} МБ)"
        )

    # Генерируем уникальный ID документа
    doc_id = generate_doc_id(file.filename)

    # Сохраняем PDF на диск
    upload_dir = ensure_dir(DATA_DIR / "uploads" / doc_id)
    pdf_path   = upload_dir / file.filename
    pdf_path.write_bytes(content)

    logger.info(f"Загружен {file.filename} ({size_mb:.1f} МБ) → doc_id={doc_id}")

    # Запускаем разбивку в фоне — не блокируем HTTP-ответ
    background_tasks.add_task(_run_splitting, pdf_path, doc_id)

    return {
        "doc_id":   doc_id,
        "filename": file.filename,
        "size_mb":  round(size_mb, 2),
        "status":   "uploaded",
        "message":  "PDF принят. Страницы генерируются в фоне.",
    }


@router.get("/", summary="Список документов")
async def list_documents():
    uploads_dir = DATA_DIR / "uploads"
    if not uploads_dir.exists():
        return {"documents": []}

    docs = []
    for doc_dir in sorted(uploads_dir.iterdir()):
        meta_file = doc_dir / "meta.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
            docs.append({
                "doc_id":     meta.get("doc_id"),
                "filename":   meta.get("filename"),
                "page_count": meta.get("page_count", 0),
                "status":     meta.get("status"),
            })

    return {"documents": docs, "total": len(docs)}


@router.get("/{doc_id}/pages", summary="Страницы документа")
async def get_pages(doc_id: str):
    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")

    meta = json.loads(meta_file.read_text())
    return {
        "doc_id":     doc_id,
        "filename":   meta.get("filename"),
        "page_count": meta.get("page_count", 0),
        "status":     meta.get("status"),
        "pages":      meta.get("pages", []),
    }
