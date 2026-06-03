"""
Роутер: запуск полного pipeline обработки документа
POST /processing/{doc_id}/start   — запустить layout detection
GET  /processing/{doc_id}/status  — статус
GET  /processing/{doc_id}/results — результаты по блокам
"""
import asyncio
import os
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Body

import sys
sys.path.insert(0, "/app")

from auth.dependencies import verify_document_ownership
from database.models import Document

# Хранилище статуса OCR-задач (in-memory, один на процесс)
# { doc_id: {"status": "running"|"done"|"error", "processed": N, "total": N, "errors": N} }
_ocr_status: dict[str, dict] = {}

logger = logging.getLogger("prms.router.processing")
router = APIRouter()

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))


def _run_layout_detection(doc_id: str):
    """
    Фоновая задача:
    1. Загружаем страницы документа
    2. Для каждой страницы запускаем DocLayout-YOLO
    3. Сохраняем вырезанные блоки и JSON с результатами
    """
    # Импорт здесь чтобы не грузить при старте (модели уже в ModelRegistry)
    from main import models
    from pipeline.layout_detector import LayoutDetector
    from PIL import Image

    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    if not meta_file.exists():
        logger.error(f"meta.json не найден для {doc_id}")
        return

    meta = json.loads(meta_file.read_text())
    pages = meta.get("pages", [])

    if models.layout_model is None:
        logger.error("DocLayout-YOLO не загружен!")
        return

    detector = LayoutDetector(
        model=models.layout_model,
        confidence_threshold=float(os.getenv("BLOCK_CONFIDENCE_THRESHOLD", "0.3")),
    )

    try:
        all_blocks = []
        for page_info in pages:
            page_num  = page_info["page_num"]
            page_path = page_info["path"]

            if not Path(page_path).exists():
                logger.warning(f"Страница {page_num} не найдена: {page_path}")
                continue

            image  = Image.open(page_path).convert("RGB")
            blocks = detector.detect(image, page_num=page_num)

            # Очищаем VRAM после каждой страницы
            import torch
            torch.cuda.empty_cache()

            for block in blocks:
                # Сохраняем изображение блока
                block_img_path = detector.save_block_image(
                    image, block, doc_id, DATA_DIR
                )
                all_blocks.append({
                    "block_id":   f"{doc_id}_p{page_num:03d}_{block.block_type}_{block.block_idx:03d}",
                    "page_num":   page_num,
                    "block_type": block.block_type,
                    "bbox":       [block.x1, block.y1, block.x2, block.y2],
                    "confidence": block.confidence,
                    "raw_class":  block.raw_class,
                    "image_path": block_img_path,
                    "status":     "detected",
                    "output":     None,
                    "sort_order": block.block_idx,
                    "ignore":     False,
                })

        # Сохраняем результаты
        results_dir = DATA_DIR / "results" / doc_id
        results_dir.mkdir(parents=True, exist_ok=True)
        blocks_file = results_dir / "blocks.json"
        blocks_file.write_text(
            json.dumps(all_blocks, ensure_ascii=False, indent=2)
        )

        # Обновляем статус документа
        meta["status"]       = "layout_done"
        meta["total_blocks"] = len(all_blocks)
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

        logger.info(f"✅ Layout detection: {doc_id} — {len(all_blocks)} блоков")

    except Exception as e:
        # При любой ошибке (OOM, etc) — пишем статус error чтобы UI не завис
        logger.error(f"❌ Layout detection failed для {doc_id}: {e}", exc_info=True)
        import torch
        torch.cuda.empty_cache()
        meta["status"] = "error"
        meta["error"]  = str(e)
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))


@router.post("/{doc_id}/start", summary="Запустить layout detection")
async def start_processing(
    doc_id: str,
    background_tasks: BackgroundTasks,
    _doc: Document = Depends(verify_document_ownership),
):
    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")

    meta = json.loads(meta_file.read_text())
    if meta.get("status") not in ("split_done",):
        if meta.get("status") in ("layout_done", "ocr_done"):
            raise HTTPException(
                status_code=400,
                detail="layout_already_done",
            )
        raise HTTPException(
            status_code=400,
            detail=f"Документ не готов к обработке. Статус: {meta.get('status')}"
        )

    background_tasks.add_task(_run_layout_detection, doc_id)

    return {
        "doc_id":  doc_id,
        "status":  "processing",
        "message": "Layout detection запущен в фоне",
    }


@router.get("/{doc_id}/status", summary="Статус обработки")
async def processing_status(
    doc_id: str,
    _doc: Document = Depends(verify_document_ownership),
):
    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")

    meta = json.loads(meta_file.read_text())

    # Считаем processed_blocks из blocks.json
    total = meta.get("total_blocks", 0)
    processed = 0
    blocks_file = DATA_DIR / "results" / doc_id / "blocks.json"
    if blocks_file.exists():
        blocks = json.loads(blocks_file.read_text())
        processed = sum(1 for b in blocks if b.get("status") not in ("detected", None))
        total = len(blocks)

    progress_pct = round(processed / total * 100) if total > 0 else 0

    return {
        "doc_id":            doc_id,
        "status":            meta.get("status"),
        "page_count":        meta.get("page_count", 0),
        "total_blocks":      total,
        "processed_blocks":  processed,
        "progress_pct":      progress_pct,
    }


@router.get("/{doc_id}/results", summary="Результаты layout detection")
async def get_results(
    doc_id: str,
    _doc: Document = Depends(verify_document_ownership),
):
    blocks_file = DATA_DIR / "results" / doc_id / "blocks.json"
    if not blocks_file.exists():
        raise HTTPException(
            status_code=404,
            detail="Результаты ещё не готовы. Запусти /processing/{doc_id}/start"
        )

    blocks = json.loads(blocks_file.read_text())
    from collections import Counter
    stats = Counter(b["block_type"] for b in blocks)

    return {
        "doc_id":       doc_id,
        "total_blocks": len(blocks),
        "by_type":      dict(stats),
        "blocks":       blocks,
    }


def _run_ocr(doc_id: str):
    """Фоновая задача: OCR всех блоков документа."""
    import os
    from main import models
    from pipeline.ocr_pipeline import OCRPipeline

    data_dir = Path(os.getenv("DATA_DIR", "/app/data"))
    pipeline = OCRPipeline(models=models, data_dir=data_dir)

    try:
        stats = pipeline.process_document(doc_id)
        logger.info(f"OCR завершён для {doc_id}: {stats}")
    except Exception as e:
        logger.error(f"OCR pipeline ошибка ({doc_id}): {e}", exc_info=True)



@router.post("/{doc_id}/ocr-block/{block_id}", summary="OCR одного блока")
async def ocr_single_block(
    doc_id: str,
    block_id: str,
    payload: dict = Body(default={}),
    _doc: Document = Depends(verify_document_ownership),
):
    """
    Синхронный OCR для одного блока — результат сразу в ответе.
    Используется из Viewer по кнопке "▶ Этот блок".
    payload.model_id — опционально: "gpt4o" | "claude" | "openrouter" | None (local)
    """
    from main import models
    from pipeline.ocr_pipeline import OCRPipeline

    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")

    model_id = payload.get("model_id")
    pipeline = OCRPipeline(models=models, data_dir=DATA_DIR)
    try:
        block = pipeline.process_single_block(doc_id, block_id, model_id=model_id)
        return {
            "block_id":   block_id,
            "status":     block.get("status"),
            "block_type": block.get("block_type"),
            "output":     block.get("output", ""),
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"OCR блока {block_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{doc_id}/ocr", summary="Запустить OCR всех блоков (фоновая задача)")
async def start_ocr(
    doc_id: str,
    payload: dict = Body(default={}),
    _doc: Document = Depends(verify_document_ownership),
):
    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")

    meta = json.loads(meta_file.read_text())
    if meta.get("status") not in ("layout_done", "ocr_done"):
        raise HTTPException(
            status_code=400,
            detail=f"Сначала запусти layout detection. Статус: {meta.get('status')}"
        )

    # Если уже выполняется — не запускаем повторно
    if _ocr_status.get(doc_id, {}).get("status") == "running":
        s = _ocr_status[doc_id]
        return {"doc_id": doc_id, "status": "already_running",
                "processed": s["processed"], "total": s["total"]}

    from main import models
    from pipeline.ocr_pipeline import OCRPipeline
    from starlette.concurrency import run_in_threadpool

    model_choices = payload.get("model_choices", {})

    blocks_file = DATA_DIR / "results" / doc_id / "blocks.json"
    total = len(json.loads(blocks_file.read_text())) if blocks_file.exists() else 0

    _ocr_status[doc_id] = {"status": "running", "processed": 0, "total": total, "errors": 0, "cancel_requested": False}

    async def _run():
        try:
            pipeline = OCRPipeline(models=models, data_dir=DATA_DIR)

            def on_progress(processed: int, errors: int):
                _ocr_status[doc_id]["processed"] = processed
                _ocr_status[doc_id]["errors"]    = errors

            def on_cancel_check() -> bool:
                return _ocr_status.get(doc_id, {}).get("cancel_requested", False)

            stats = await run_in_threadpool(
                pipeline.process_document, doc_id, model_choices, on_progress, on_cancel_check
            )
            if stats.get("status") == "cancelled":
                _ocr_status[doc_id].update({
                    "status":    "cancelled",
                    "processed": stats.get("processed", 0),
                    "errors":    stats.get("errors", 0),
                    "by_type":   stats.get("by_type", {}),
                })
            else:
                _ocr_status[doc_id].update({
                    "status":    "done",
                    "processed": stats.get("processed", 0),
                    "errors":    stats.get("errors", 0),
                    "by_type":   stats.get("by_type", {}),
                })
        except Exception as e:
            logger.error(f"OCR фоновая задача ({doc_id}): {e}", exc_info=True)
            _ocr_status[doc_id]["status"]    = "error"
            _ocr_status[doc_id]["error_msg"] = str(e)

    asyncio.create_task(_run())
    return {"doc_id": doc_id, "status": "started", "total": total}


@router.get("/{doc_id}/ocr-status", summary="Статус OCR задачи")
async def get_ocr_status(
    doc_id: str,
    _doc: Document = Depends(verify_document_ownership),
):
    status = _ocr_status.get(doc_id)
    if not status:
        blocks_file = DATA_DIR / "results" / doc_id / "blocks.json"
        if blocks_file.exists():
            blocks = json.loads(blocks_file.read_text())
            done = sum(1 for b in blocks if b.get("status") == "ocr_done")
            if done > 0:
                return {"status": "done", "processed": done,
                        "total": len(blocks), "errors": 0}
        return {"status": "idle", "processed": 0, "total": 0, "errors": 0}
    return status


@router.post("/{doc_id}/ocr/cancel", summary="Отмена OCR")
async def cancel_ocr(
    doc_id: str,
    _doc: Document = Depends(verify_document_ownership),
):
    """Устанавливает флаг отмены для бегущего OCR. Pipeline проверяет его на каждом блоке."""
    entry = _ocr_status.get(doc_id)
    if entry and entry.get("status") == "running":
        entry["cancel_requested"] = True
        return {"doc_id": doc_id, "status": "cancel_requested"}
    return {"doc_id": doc_id, "status": "not_running"}


@router.post("/{doc_id}/export", summary="Экспорт результатов")
async def export_results(
    doc_id: str,
    format: str = "markdown",
    _doc: Document = Depends(verify_document_ownership),
):
    """
    Экспортирует OCR результаты в файл.

    format: markdown | json | csv
    Файл сохраняется в /app/data/results/{doc_id}/export.*
    """
    import csv
    import io

    blocks_file = DATA_DIR / "results" / doc_id / "blocks.json"
    if not blocks_file.exists():
        raise HTTPException(status_code=404, detail="Результаты не найдены")

    blocks = json.loads(blocks_file.read_text())
    export_dir = DATA_DIR / "results" / doc_id
    export_dir.mkdir(parents=True, exist_ok=True)

    if format == "markdown":
        from shared.utils import blocks_to_markdown
        content  = blocks_to_markdown(blocks)
        out_path = export_dir / "export.md"
        out_path.write_text(content, encoding="utf-8")

    elif format == "json":
        out_path = export_dir / "export.json"
        out_path.write_text(
            json.dumps(blocks, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    elif format == "csv":
        out_path = export_dir / "export.csv"
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=[
            "block_id", "page_num", "block_type",
            "confidence", "status", "output"
        ])
        writer.writeheader()
        for b in blocks:
            writer.writerow({
                "block_id":   b.get("block_id", ""),
                "page_num":   b.get("page_num", ""),
                "block_type": b.get("block_type", ""),
                "confidence": b.get("confidence", ""),
                "status":     b.get("status", ""),
                "output":     (b.get("output") or "")[:500],
            })
        out_path.write_text(buf.getvalue(), encoding="utf-8")

    else:
        raise HTTPException(status_code=400, detail=f"Формат не поддерживается: {format}")

    size = out_path.stat().st_size
    logger.info(f"Экспорт {doc_id} → {format} ({size} байт)")

    return {
        "doc_id":     doc_id,
        "format":     format,
        "file_path":  str(out_path),
        "size_bytes": size,
        "message":    f"Экспорт готов: {out_path.name}",
    }


@router.get("/{doc_id}/export-file/{fmt}", summary="Скачать файл экспорта")
async def download_export(
    doc_id: str,
    fmt: str,
    _doc: Document = Depends(verify_document_ownership),
):
    """
    Отдаёт файл экспорта для скачивания браузером.
    Сначала вызови POST /{doc_id}/export?format=..., затем GET этот endpoint.
    """
    from fastapi.responses import FileResponse

    ext_map  = {"markdown": "md",   "json": "json", "csv": "csv"}
    mime_map = {"markdown": "text/markdown", "json": "application/json", "csv": "text/csv"}

    if fmt not in ext_map:
        raise HTTPException(status_code=400, detail=f"Формат не поддерживается: {fmt}")

    out_path = DATA_DIR / "results" / doc_id / f"export.{ext_map[fmt]}"
    if not out_path.exists():
        # Fallback для quick-mode документов: result.md вместо export.md
        if fmt == "markdown":
            result_path = DATA_DIR / "results" / doc_id / "result.md"
            if result_path.exists():
                out_path = result_path
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"Файл не найден — сначала POST /{doc_id}/export?format={fmt}",
                )
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Файл не найден — сначала POST /{doc_id}/export?format={fmt}",
            )

    filename = f"{doc_id[:8]}_{fmt}.{ext_map[fmt]}"
    return FileResponse(
        path=str(out_path),
        media_type=mime_map[fmt],
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{doc_id}/media/{filename}", summary="Serve block image")
async def serve_media(
    doc_id: str,
    filename: str,
    _doc: Document = Depends(verify_document_ownership),
):
    """
    Сервит PNG-файл из data/results/{doc_id}/blocks/{filename}.
    Используется Markdown Viewer для отображения изображений вместо base64.
    """
    from fastapi.responses import FileResponse

    safe = Path(filename).name
    if not safe or ".." in safe:
        raise HTTPException(status_code=400, detail="Invalid filename")
    img_path = DATA_DIR / "results" / doc_id / "blocks" / safe
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"Not found: {safe}")
    if img_path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(status_code=400, detail="Only image files allowed")
    return FileResponse(
        str(img_path),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/{doc_id}/export-zip", summary="Скачать ZIP с export.md + изображениями")
async def export_zip(
    doc_id: str,
    _doc: Document = Depends(verify_document_ownership),
):
    """
    Создаёт ZIP-архив для импорта в Obsidian:
      export.md          ← markdown с ./blocks/filename.png ссылками
      blocks/
        p007figure001.png
        ...
    Сначала вызовите POST /{doc_id}/export?format=markdown чтобы export.md был актуальным.
    """
    import io
    import zipfile
    from fastapi.responses import StreamingResponse

    md_file = DATA_DIR / "results" / doc_id / "export.md"
    blocks_dir = DATA_DIR / "results" / doc_id / "blocks"

    if not md_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"export.md не найден. Сначала вызовите POST /processing/{doc_id}/export?format=markdown",
        )

    figure_images = []
    if blocks_dir.exists():
        figure_images = [
            p for p in blocks_dir.iterdir()
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
            and "figure" in p.name
        ]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.write(str(md_file), "export.md")
        for img in figure_images:
            zf.write(str(img), f"blocks/{img.name}")
    buf.seek(0)

    short_id = doc_id[:8]
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{short_id}_export.zip"'},
    )


@router.patch("/{doc_id}/export-file/markdown", summary="Сохранить отредактированный markdown")
async def save_markdown(
    doc_id: str,
    payload: dict = Body(default={}),
    _doc: Document = Depends(verify_document_ownership),
):
    """Перезаписывает export.md отредактированным содержимым из фронтенда."""
    import shutil

    content = payload.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="content не может быть пустым")

    md_file = DATA_DIR / "results" / doc_id / "export.md"
    # Для quick-mode документов export.md может не существовать — создаём его
    if md_file.exists():
        backup = DATA_DIR / "results" / doc_id / "export.md.bak"
        shutil.copy2(str(md_file), str(backup))

    md_file.write_text(content, encoding="utf-8")
    logger.info(f"export.md сохранён для {doc_id}, {len(content)} символов")
    return {"saved": True, "doc_id": doc_id, "size": len(content)}


# ─── PATCH: обновить статус/output конкретного блока ───────────────────────
@router.patch("/{doc_id}/blocks/{block_id}")
async def patch_block(
    doc_id: str,
    block_id: str,
    payload: dict = Body(...),
    _doc: Document = Depends(verify_document_ownership),
):
    """
    Обновить статус или output блока.
    payload: {"status": "needs_review"} или {"output": "...", "status": "accepted"}
    """
    import json
    blocks_file = DATA_DIR / "results" / doc_id / "blocks.json"
    if not blocks_file.exists():
        raise HTTPException(status_code=404, detail="Результаты не найдены")

    blocks = json.loads(blocks_file.read_text())
    updated = False
    updated_block = None
    for block in blocks:
        if block.get("block_id") == block_id:
            if "output" in payload and not block.get("original_output"):
                block["original_output"] = block.get("output") or ""
            for field in ("status", "output", "block_type", "bbox", "ignore", "sort_order"):
                if field in payload:
                    block[field] = payload[field]
            updated = True
            updated_block = block
            break

    if not updated:
        raise HTTPException(status_code=404, detail=f"Блок {block_id} не найден")

    # ── Перекропировать если обновился bbox ──────────────────────────────────
    if "bbox" in payload and updated_block is not None:
        try:
            from PIL import Image as PILImage
            page_num = updated_block.get("page_num", 1)
            new_bbox = payload["bbox"]
            page_png = DATA_DIR / "pages" / doc_id / f"page_{page_num:03d}.png"
            if not page_png.exists():
                page_png = DATA_DIR / "pages" / doc_id / f"page{page_num:03d}.png"
            if page_png.exists() and len(new_bbox) == 4:
                page_img = PILImage.open(str(page_png)).convert("RGB")
                pw, ph   = page_img.size
                x1, y1, x2, y2 = new_bbox
                x1 = max(0, min(int(x1), pw - 1))
                y1 = max(0, min(int(y1), ph - 1))
                x2 = max(x1 + 1, min(int(x2), pw))
                y2 = max(y1 + 1, min(int(y2), ph))
                crop      = page_img.crop((x1, y1, x2, y2))
                crops_dir = DATA_DIR / "results" / doc_id / "blocks"
                crops_dir.mkdir(parents=True, exist_ok=True)
                crop_path = crops_dir / f"{block_id}.png"
                crop.save(str(crop_path))
                updated_block["image_path"] = str(crop_path)
                logger.info(f"Кроп обновлён: {crop_path} ({x2-x1}x{y2-y1}px)")
        except Exception as _e:
            logger.error(f"Ошибка обновления кропа для {block_id}: {_e}")

    blocks_file.write_text(json.dumps(blocks, ensure_ascii=False, indent=2))
    return {"block_id": block_id, "updated": True, **payload}
@router.delete("/{doc_id}/blocks/{block_id}", summary="Удалить блок")
async def delete_block(
    doc_id: str,
    block_id: str,
    _doc: Document = Depends(verify_document_ownership),
):
    blocks_file = DATA_DIR / "results" / doc_id / "blocks.json"
    if not blocks_file.exists():
        raise HTTPException(status_code=404, detail="blocks.json not found")
    blocks = json.loads(blocks_file.read_text())
    new_blocks = [b for b in blocks if b.get("block_id") != block_id]
    if len(new_blocks) == len(blocks):
        raise HTTPException(status_code=404, detail=f"block {block_id} not found")
    blocks_file.write_text(json.dumps(new_blocks, ensure_ascii=False, indent=2))
    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    if meta_file.exists():
        meta = json.loads(meta_file.read_text())
        meta["total_blocks"] = len(new_blocks)
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return {"block_id": block_id, "deleted": True}


@router.post("/{doc_id}/blocks/reorder", summary="Переупорядочить блоки")
async def reorder_blocks(
    doc_id: str,
    payload: dict = Body(...),
    _doc: Document = Depends(verify_document_ownership),
):
    """
    Принимает {"order": ["blockid1", "blockid2", ...]}
    Обновляет sort_order для переданных блоков.
    """
    new_order = payload.get("order", [])
    blocks_file = DATA_DIR / "results" / doc_id / "blocks.json"
    if not blocks_file.exists():
        raise HTTPException(status_code=404, detail="blocks.json not found")
    blocks = json.loads(blocks_file.read_text())
    order_map = {bid: i for i, bid in enumerate(new_order)}
    for b in blocks:
        if b["block_id"] in order_map:
            b["sort_order"] = order_map[b["block_id"]]
    blocks_file.write_text(json.dumps(blocks, ensure_ascii=False, indent=2))
    return {"updated": len(order_map)}


@router.post("/{doc_id}/blocks", summary="Добавить блок вручную")
async def add_block(
    doc_id: str,
    payload: dict = Body(...),
    _doc: Document = Depends(verify_document_ownership),
):
    blocks_file = DATA_DIR / "results" / doc_id / "blocks.json"
    if not blocks_file.exists():
        raise HTTPException(status_code=404, detail="blocks.json not found")
    blocks = json.loads(blocks_file.read_text())
    import uuid
    block_type = payload.get("block_type", "text")
    page_num   = int(payload.get("page_num", 1))
    bbox       = payload.get("bbox", [0, 0, 100, 100])
    block_id   = f"{doc_id}p{page_num:03d}{block_type}{uuid.uuid4().hex[:4]}"
    new_block  = {
        "block_id":   block_id,
        "page_num":   page_num,
        "block_type": block_type,
        "bbox":       bbox,
        "confidence": 1.0,
        "raw_class":  "manual",
        "image_path": None,
        "status":     "detected",
        "output":     None,
        "sort_order": len([b for b in blocks if b.get("page_num") == page_num]),
        "ignore":     False,
    }
    # ── Вырезаем кроп из страницы сразу при создании блока ──────────────────
    try:
        from PIL import Image as PILImage
        # Поддерживаем оба формата: page001.png и page_001.png
        page_png = DATA_DIR / "pages" / doc_id / f"page_{page_num:03d}.png"
        if not page_png.exists():
            page_png = DATA_DIR / "pages" / doc_id / f"page{page_num:03d}.png"
        if page_png.exists() and len(bbox) == 4:
            page_img = PILImage.open(str(page_png)).convert("RGB")
            pw, ph   = page_img.size          # размер PNG страницы
            x1, y1, x2, y2 = bbox             # координаты в пикселях страницы
            # Клампируем чтобы не выйти за границы
            x1 = max(0, min(int(x1), pw - 1))
            y1 = max(0, min(int(y1), ph - 1))
            x2 = max(x1 + 1, min(int(x2), pw))
            y2 = max(y1 + 1, min(int(y2), ph))
            crop = page_img.crop((x1, y1, x2, y2))
            crops_dir = DATA_DIR / "results" / doc_id / "blocks"
            crops_dir.mkdir(parents=True, exist_ok=True)
            crop_path = crops_dir / f"{block_id}.png"
            crop.save(str(crop_path))
            new_block["image_path"] = str(crop_path)
            new_block["status"]     = "detected"
            logger.info(f"Кроп создан: {crop_path} ({x2-x1}x{y2-y1}px)")
        else:
            logger.warning(f"Страница {page_png} не найдена — image_path будет пустым")
    except Exception as e:
        logger.error(f"Ошибка создания кропа для {block_id}: {e}")

    new_page = new_block.get("page_num", 0)
    new_y1   = new_block.get("bbox", [0, 0, 0, 0])[1]

    insert_idx = len(blocks)  # дефолт — в конец
    for i, b in enumerate(blocks):
        b_page = b.get("page_num", 0)
        b_y1   = b.get("bbox", [0, 0, 0, 0])[1]
        if (b_page, b_y1) > (new_page, new_y1):
            insert_idx = i
            break

    blocks.insert(insert_idx, new_block)
    blocks_file.write_text(json.dumps(blocks, ensure_ascii=False, indent=2))
    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    if meta_file.exists():
        meta = json.loads(meta_file.read_text())
        meta["total_blocks"] = len(blocks)
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return {"block_id": block_id, "created": True, "image_path": new_block.get("image_path")}

