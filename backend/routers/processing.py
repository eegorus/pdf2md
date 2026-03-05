"""
Роутер: запуск полного pipeline обработки документа
POST /processing/{doc_id}/start   — запустить layout detection
GET  /processing/{doc_id}/status  — статус
GET  /processing/{doc_id}/results — результаты по блокам
"""
import os
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, BackgroundTasks, Body

import sys
sys.path.insert(0, "/app")

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

    all_blocks = []
    for page_info in pages:
        page_num  = page_info["page_num"]
        page_path = page_info["path"]

        if not Path(page_path).exists():
            logger.warning(f"Страница {page_num} не найдена: {page_path}")
            continue

        image  = Image.open(page_path).convert("RGB")
        blocks = detector.detect(image, page_num=page_num)

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


@router.post("/{doc_id}/start", summary="Запустить layout detection")
async def start_processing(doc_id: str, background_tasks: BackgroundTasks):
    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")

    meta = json.loads(meta_file.read_text())
    if meta.get("status") not in ("split_done", "layout_done"):
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
async def processing_status(doc_id: str):
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
async def get_results(doc_id: str):
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


@router.post("/{doc_id}/ocr", summary="Запустить OCR всех блоков")
async def start_ocr(doc_id: str, background_tasks: BackgroundTasks):
    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")

    meta = json.loads(meta_file.read_text())
    if meta.get("status") != "layout_done":
        raise HTTPException(
            status_code=400,
            detail=f"Сначала запусти layout detection. Статус: {meta.get('status')}"
        )

    background_tasks.add_task(_run_ocr, doc_id)
    return {
        "doc_id":  doc_id,
        "status":  "ocr_processing",
        "message": "OCR запущен в фоне. Проверяй /status",
    }


@router.post("/{doc_id}/export", summary="Экспорт результатов")
async def export_results(doc_id: str, format: str = "markdown"):
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


# ─── PATCH: обновить статус/output конкретного блока ───────────────────────
@router.patch("/{doc_id}/blocks/{block_id}")
async def patch_block(doc_id: str, block_id: str, payload: dict = Body(...)):
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
    for block in blocks:
        if block.get("block_id") == block_id:
            # original_output не трогаем — это эталон для training pairs
            if "output" in payload and not block.get("original_output"):
                block["original_output"] = block.get("output") or ""
            for field in ("status", "output", "block_type", "bbox"):
                if field in payload:
                    block[field] = payload[field]
            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail=f"Блок {block_id} не найден")

    blocks_file.write_text(json.dumps(blocks, ensure_ascii=False, indent=2))
    return {"block_id": block_id, "updated": True, **payload}
@router.delete("/{doc_id}/blocks/{block_id}", summary="Удалить блок")
async def delete_block(doc_id: str, block_id: str):
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


@router.post("/{doc_id}/blocks", summary="Добавить блок вручную")
async def add_block(doc_id: str, payload: dict = Body(...)):
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
    }
    blocks.append(new_block)
    blocks_file.write_text(json.dumps(blocks, ensure_ascii=False, indent=2))
    meta_file = DATA_DIR / "uploads" / doc_id / "meta.json"
    if meta_file.exists():
        meta = json.loads(meta_file.read_text())
        meta["total_blocks"] = len(blocks)
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return {"block_id": block_id, "created": True}

