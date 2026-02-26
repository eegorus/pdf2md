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

from fastapi import APIRouter, HTTPException, BackgroundTasks

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
    return {
        "doc_id":        doc_id,
        "status":        meta.get("status"),
        "page_count":    meta.get("page_count", 0),
        "total_blocks":  meta.get("total_blocks", 0),
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
