"""
Роутер: управление документами
POST /documents/upload  — загрузка и первичный разбор PDF
GET  /documents/        — список документов
GET  /documents/{doc_id}/pages — страницы документа
"""
import json
import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

import sys
sys.path.insert(0, "/app")
from shared.utils import generate_doc_id, ensure_dir

from auth.dependencies import get_current_user, verify_document_ownership
from database.engine import get_db
from database.models import Document, User
from database.crud.documents import (
    create_document,
    delete_document,
    get_user_documents,
    get_user_storage_used,
)

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
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
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

    # Проверяем квоту хранилища
    file_size  = pdf_path.stat().st_size
    used_bytes = await get_user_storage_used(session, current_user.id)
    quota_bytes = current_user.storage_quota_mb * 1024 * 1024
    if used_bytes + file_size > quota_bytes:
        pdf_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail=f"Превышена квота хранилища ({current_user.storage_quota_mb} MB)"
        )

    # Создаём запись в БД
    await create_document(
        session,
        docid=doc_id,
        user_id=current_user.id,
        filename=file.filename,
        file_size_bytes=file_size,
    )

    # ── Сразу пишем meta.json со статусом splitting ──────────────────────
    # Это нужно чтобы /processing/{doc_id}/status не возвращал 404
    # пока PDF конвертируется в PNG (может занять 30-60 сек)
    meta_path = upload_dir / "meta.json"
    meta_path.write_text(json.dumps({
        "doc_id":     doc_id,
        "filename":   file.filename,
        "size_mb":    round(size_mb, 2),
        "status":     "splitting",
        "page_count": 0,
        "pages":      [],
    }, ensure_ascii=False, indent=2))

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
async def list_documents(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    if current_user.is_admin:
        # Для админа — читаем все документы с диска (как раньше)
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

    # Обычный пользователь — только свои документы из БД
    db_docs = await get_user_documents(session, current_user.id)
    docs_result = []
    for db_doc in db_docs:
        meta_path = DATA_DIR / "uploads" / db_doc.docid / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            meta["filename"] = db_doc.filename
            meta["created_at"] = db_doc.created_at.isoformat() if db_doc.created_at else None
            docs_result.append(meta)
    return {"documents": docs_result, "total": len(docs_result)}


@router.get("/{doc_id}/pages", summary="Страницы документа")
async def get_pages(
    doc_id: str,
    _doc: Document = Depends(verify_document_ownership),
):
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


@router.get("/{doc_id}/page-image/{page_num}")
async def get_page_image(
    doc_id: str,
    page_num: int,
    _doc: Document = Depends(verify_document_ownership),
):
    """PNG страницы целиком для Viewer."""
    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")

    # pages хранятся как page001.png, page002.png ...
    page_path = DATA_DIR / "pages" / doc_id / f"page_{page_num:03d}.png"
    if not page_path.exists():
        raise HTTPException(status_code=404, detail=f"Страница {page_num} не найдена")

    return FileResponse(str(page_path), media_type="image/png")


@router.get("/{doc_id}/block-image/{block_id}")
async def get_block_image(
    doc_id: str,
    block_id: str,
    _doc: Document = Depends(verify_document_ownership),
):
    """PNG кропа конкретного блока."""
    blocks_file = DATA_DIR / "results" / doc_id / "blocks.json"
    if not blocks_file.exists():
        raise HTTPException(status_code=404, detail="Результаты не найдены")

    blocks = json.loads(blocks_file.read_text())
    block = next((b for b in blocks if b.get("block_id") == block_id), None)
    if not block:
        raise HTTPException(status_code=404, detail=f"Блок {block_id} не найден")

    image_path = block.get("image_path") or block.get("imagepath")
    if not image_path or not Path(image_path).exists():
        raise HTTPException(status_code=404, detail="Изображение блока не найдено")

    return FileResponse(str(image_path), media_type="image/png")


@router.delete("/{doc_id}", summary="Удалить документ и все его данные")
async def delete_document_endpoint(
    doc_id: str,
    _doc: Document = Depends(verify_document_ownership),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Удалить документ: из БД и с диска."""
    # Удалить файлы с диска
    for folder in ["uploads", "pages", "blocks", "results"]:
        path = DATA_DIR / folder / doc_id
        if path.exists():
            shutil.rmtree(path)

    # Удалить из БД
    await delete_document(session, doc_id)
    logger.info(f"Документ {doc_id} удалён пользователем {current_user.email}")
    return {"deleted": True, "doc_id": doc_id}
