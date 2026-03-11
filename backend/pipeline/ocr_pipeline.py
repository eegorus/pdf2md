"""
ocr_pipeline.py — оркестратор OCR для всех блоков документа

Читает blocks.json → для каждого блока определяет модуль →
запускает OCR → записывает output обратно в blocks.json

Логика выбора модуля:
  text    → EasyOCR → [fallback: Ollama]
  table   → dots.ocr → [fallback: Ollama]
  formula → TexTeller → [fallback: Ollama]
  figure  → Ollama (всегда, это описание а не OCR)
"""
import json
import logging
import os
from pathlib import Path

from PIL import Image

logger = logging.getLogger("prms.ocr_pipeline")


class OCRPipeline:
    def __init__(self, models, data_dir: str | Path):
        """
        models:   ModelRegistry из main.py
        data_dir: базовый путь к данным (/app/data)
        """
        self.models   = models
        self.data_dir = Path(data_dir)

        # Инициализируем модули лениво (только когда нужны)
        self._text_ocr    = None
        self._table_rec   = None
        self._formula_ocr = None
        self._figure_proc = None
        self._fallback    = None

    # ── Lazy initialization ───────────────────────────────────────────
    @property
    def text_ocr(self):
        if self._text_ocr is None and self.models.ocr_reader:
            from pipeline.text_ocr import TextOCR
            self._text_ocr = TextOCR(self.models.ocr_reader)
        return self._text_ocr

    @property
    def table_rec(self):
        if self._table_rec is None and self.models.table_model:
            from pipeline.table_recognizer import TableRecognizer
            self._table_rec = TableRecognizer(
                self.models.table_model,
                self.models.table_processor,
            )
        return self._table_rec

    @property
    def formula_ocr(self):
        if self._formula_ocr is None and self.models.status.get("texteller"):
            from pipeline.formula_ocr import FormulaOCR
            self._formula_ocr = FormulaOCR()
        return self._formula_ocr

    @property
    def figure_proc(self):
        if self._figure_proc is None:
            from pipeline.figure_processor import FigureProcessor
            self._figure_proc = FigureProcessor()
        return self._figure_proc

    @property
    def fallback(self):
        if self._fallback is None:
            from pipeline.fallback_api import FallbackAPI
            self._fallback = FallbackAPI()
        return self._fallback

    # ── Основной метод ────────────────────────────────────────────────

    def process_single_block(self, doc_id: str, block_id: str) -> dict:
        """
        OCR для одного блока по block_id.
        Читает blocks.json, обновляет нужный блок, сохраняет обратно.
        Возвращает обновлённый блок.
        """
        blocks_file = self.data_dir / "results" / doc_id / "blocks.json"
        if not blocks_file.exists():
            raise FileNotFoundError(f"blocks.json не найден: {blocks_file}")

        blocks = json.loads(blocks_file.read_text())
        block  = next((b for b in blocks if b.get("block_id") == block_id), None)
        if block is None:
            raise ValueError(f"Блок {block_id} не найден")

        image_path = block.get("image_path", "")
        if not image_path or not Path(image_path).exists():
            block["output"] = "[error: image not found]"
            block["status"] = "error"
            blocks_file.write_text(json.dumps(blocks, ensure_ascii=False, indent=2))
            return block

        image  = Image.open(image_path).convert("RGB")
        output = self._process_block(image, block["block_type"])

        block["output"] = output
        if not block.get("original_output"):
            block["original_output"] = output
        block["status"] = "ocr_done"

        blocks_file.write_text(json.dumps(blocks, ensure_ascii=False, indent=2))
        logger.info(f"✅ OCR блока {block_id}: {len(output)} символов")
        return block

    def process_document(self, doc_id: str) -> dict:
        """
        Запускает OCR для всех блоков документа.

        Читает results/{doc_id}/blocks.json
        Обновляет поле 'output' у каждого блока
        Сохраняет обратно в blocks.json

        Возвращает статистику: {'processed': N, 'errors': M, 'by_type': {...}}
        """
        blocks_file = self.data_dir / "results" / doc_id / "blocks.json"
        if not blocks_file.exists():
            raise FileNotFoundError(f"blocks.json не найден: {blocks_file}")

        blocks = json.loads(blocks_file.read_text())

        # Защита от двойного запуска — пропускаем уже обработанные блоки
        already_done = sum(1 for b in blocks if b.get("status") == "ocr_done")
        if already_done == len(blocks):
            logger.warning(f"OCR уже выполнен для {doc_id}, пропускаем")
            return {"processed": already_done, "errors": 0, "by_type": {}}

        logger.info(f"OCR pipeline: {doc_id} — {len(blocks)} блоков (уже готово: {already_done})")

        stats = {"processed": 0, "errors": 0, "by_type": {}}

        for i, block in enumerate(blocks):
            block_type = block["block_type"]
            image_path = block.get("image_path", "")

            if not image_path or not Path(image_path).exists():
                logger.warning(f"Блок {block['block_id']}: файл не найден")
                block["output"] = "[error: image not found]"
                block["status"] = "error"
                stats["errors"] += 1
                continue

            try:
                image  = Image.open(image_path).convert("RGB")
                output = self._process_block(image, block_type)

                block["output"] = output
                # Сохраняем оригинал один раз — original_output нужен для training pairs
                if not block.get("original_output"):
                    block["original_output"] = output
                block["status"] = "ocr_done"
                stats["processed"] += 1
                stats["by_type"][block_type] = (
                    stats["by_type"].get(block_type, 0) + 1
                )

                # Очищаем VRAM после каждой таблицы — dots.ocr жадный
                if block_type == "table":
                    import torch
                    torch.cuda.empty_cache()

                if (i + 1) % 10 == 0:
                    logger.info(f"  Обработано {i+1}/{len(blocks)} блоков...")

            except Exception as e:
                logger.error(f"Блок {block['block_id']}: {e}", exc_info=True)
                block["output"] = f"[error: {type(e).__name__}]"
                block["status"] = "error"
                stats["errors"] += 1

        # Сохраняем обновлённые блоки
        blocks_file.write_text(
            json.dumps(blocks, ensure_ascii=False, indent=2)
        )

        # Обновляем статус документа
        meta_file = self.data_dir / "uploads" / doc_id / "meta.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
            meta["status"] = "ocr_done"
            meta["ocr_stats"] = stats
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

        logger.info(
            f"✅ OCR завершён: {doc_id} — "
            f"ok={stats['processed']}, err={stats['errors']}"
        )
        return stats

    def _unload_table_model(self):
        """Выгружаем dots.ocr из VRAM перед вызовом Ollama."""
        import torch
        if self.models.table_model is not None:
            self.models.table_model.to("cpu")
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            logger.debug("dots.ocr выгружен на CPU для освобождения VRAM")

    def _reload_table_model(self):
        """Возвращаем dots.ocr на GPU после вызова Ollama."""
        import torch
        if self.models.table_model is not None:
            self.models.table_model.to("cuda")
            logger.debug("dots.ocr возвращён на GPU")

    def _process_block(self, image: Image.Image, block_type: str) -> str:
        """
        Выбирает модуль и запускает OCR для одного блока.
        Figure/fallback → выгружаем dots.ocr перед Ollama вызовом.
        """
        import torch
        try:
            if block_type == "text":
                if self.text_ocr:
                    return self.text_ocr.recognize(image)
                self._unload_table_model()
                result = self.fallback.process(image, "text")
                self._reload_table_model()
                return result

            elif block_type in ("table", "table_simple", "table_complex"):
                if self.table_rec:
                    try:
                        result = self.table_rec.recognize(image)
                        torch.cuda.empty_cache()
                        return result
                    except torch.cuda.OutOfMemoryError:
                        logger.warning("OOM на таблице -> empty_cache -> retry")
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
                        try:
                            result = self.table_rec.recognize(image)
                            torch.cuda.empty_cache()
                            return result
                        except Exception as retry_e:
                            logger.error(f"Retry таблицы не помог: {retry_e}")
                            return "[table: requires manual review]"
                logger.warning("dots.ocr недоступен - таблица помечена для ревью")
                return "[table: requires manual review]"

            elif block_type == "formula":
                if self.formula_ocr:
                    result = self.formula_ocr.recognize(image)
                    if result:
                        return result
                # TexTeller — CPU инструмент, VRAM не нужна
                return self.fallback.process(image, "formula")

            elif block_type == "figure":
                # Ollama требует VRAM — выгружаем dots.ocr
                self._unload_table_model()
                result = self.figure_proc.describe(image)
                self._reload_table_model()
                return result

            else:
                logger.warning(f"Неизвестный тип блока: {block_type}")
                self._unload_table_model()
                result = self.fallback.process(image, "text")
                self._reload_table_model()
                return result

        except Exception as e:
            logger.error(f"Ошибка {block_type} модуля: {e}, пробуем fallback")
            torch.cuda.empty_cache()
            self._unload_table_model()
            result = self.fallback.process(image, block_type)
            self._reload_table_model()
            return result
