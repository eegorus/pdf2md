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

# Модели, которые не помещаются в VRAM вместе с dots.ocr (>6 GB) — требуют offload
HEAVY_FIGURE_MODELS = {"qwen2.5vl:7b", "qwen2.5vl:72b"}


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

    def process_single_block(self, doc_id: str, block_id: str, model_id: str | None = None) -> dict:
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
        output = self._process_block(image, block["block_type"], model_id=model_id)

        block["output"] = output
        if not block.get("original_output"):
            block["original_output"] = output
        block["status"] = "ocr_done"

        blocks_file.write_text(json.dumps(blocks, ensure_ascii=False, indent=2))
        logger.info(f"✅ OCR блока {block_id}: {len(output)} символов")
        return block

    def process_document(self, doc_id: str, model_choices: dict | None = None, on_progress=None) -> dict:
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

        # Сортируем: сначала text/table (EasyOCR + dots.ocr), потом figure (Ollama)
        # Это позволяет выгрузить dots.ocr один раз перед запуском Ollama
        _block_order = ["text", "table_simple", "table_complex", "table", "formula", "figure"]
        blocks_sorted = sorted(
            blocks,
            key=lambda b: _block_order.index(b.get("block_type", "text"))
            if b.get("block_type") in _block_order else 99
        )

        stats     = {"processed": 0, "errors": 0, "by_type": {}}
        last_type = None

        for i, block in enumerate(blocks_sorted):
            block_type = block.get("block_type", "text")
            image_path = block.get("image_path", "")

            # При переходе к figure — offload dots.ocr только если модель тяжёлая
            if block_type == "figure" and last_type != "figure":
                figure_model = (model_choices or {}).get("figure",
                    getattr(self.figure_proc, "model", "qwen2.5vl:3b"))
                # resolve model_id alias → actual model name
                if figure_model == "ollama_7b":
                    figure_model = os.getenv("OLLAMA_FALLBACK_MODEL", "qwen2.5vl:7b")
                elif figure_model in ("ollama_3b", "ollama"):
                    figure_model = os.getenv("OLLAMA_FIGURE_MODEL", "qwen2.5vl:3b")

                if figure_model in HEAVY_FIGURE_MODELS:
                    self._unload_table_model()
                    logger.info(f"dots.ocr offloaded перед {figure_model}")
                else:
                    logger.info(f"dots.ocr остаётся на GPU, {figure_model} не требует offload")

            last_type = block_type

            if not image_path or not Path(image_path).exists():
                logger.warning(f"Блок {block['block_id']}: файл не найден")
                block["output"] = "[error: image not found]"
                block["status"] = "error"
                stats["errors"] += 1
                if on_progress and (i + 1) % 10 == 0:
                    on_progress(stats["processed"], stats["errors"])
                continue

            try:
                image    = Image.open(image_path).convert("RGB")
                model_id = (model_choices or {}).get(block_type)
                output   = self._process_block(image, block_type, model_id=model_id)

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
                if block_type in {"table", "table_simple", "table_complex"}:
                    import torch
                    torch.cuda.empty_cache()

                if (i + 1) % 10 == 0:
                    logger.info(f"  Обработано {i+1}/{len(blocks_sorted)} блоков...")
                    if on_progress:
                        on_progress(stats["processed"], stats["errors"])

            except Exception as e:
                logger.error(f"Блок {block['block_id']}: {e}", exc_info=True)
                block["output"] = f"[error: {type(e).__name__}]"
                block["status"] = "error"
                stats["errors"] += 1

        # Сохраняем обновлённые блоки (порядок из blocks_sorted — по типу)
        blocks_file.write_text(
            json.dumps(blocks_sorted, ensure_ascii=False, indent=2)
        )
        if on_progress:
            on_progress(stats["processed"], stats["errors"])

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
        """Выгружаем dots.ocr из VRAM перед вызовом тяжёлой Ollama-модели."""
        import torch, gc
        if hasattr(self.models, "table_model") and self.models.table_model is not None:
            self.models.table_model.cpu()
            gc.collect()
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            logger.info(f"VRAM после offload dots.ocr: {torch.cuda.memory_allocated()/1e9:.1f}GB allocated")

    def _reload_table_model(self):
        """Возвращаем dots.ocr на GPU (для будущих сценариев)."""
        import torch
        if hasattr(self.models, "table_model") and self.models.table_model is not None:
            self.models.table_model.to("cuda")
            logger.debug("dots.ocr возвращён на GPU")

    def _process_block(self, image: Image.Image, block_type: str, model_id: str | None = None) -> str:
        logger.debug(f"[_process_block] type={block_type!r} model_id={model_id!r}")
        """Выбирает модуль и запускает OCR для одного блока."""
        import torch
        try:
            # Если явно указана облачная модель — роутим туда
            if model_id in ("gpt4o", "claude", "openrouter"):
                return self._process_cloud(image, block_type, model_id)

            if block_type == "text":
                if self.text_ocr:
                    return self.text_ocr.recognize(image)
                return self.fallback.process(image, "text")

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
                model_override = None
                if model_id == "ollama_7b":
                    import os
                    model_override = os.getenv("OLLAMA_FALLBACK_MODEL", "qwen2.5vl:7b")
                return self.figure_proc.describe(image, model_override=model_override)

            else:
                logger.warning(f"Неизвестный тип блока: {block_type}")
                return self.fallback.process(image, "text")

        except Exception as e:
            logger.error(f"Ошибка {block_type} модуля: {e}, пробуем fallback")
            torch.cuda.empty_cache()
            return self.fallback.process(image, block_type)

    def _process_cloud(self, image: "Image.Image", block_type: str, model_id: str) -> str:
        """Отправляет блок в облачную модель через settings API-ключи."""
        import base64, io, httpx, json

        settings_file = self.data_dir / "settings.json"
        keys = {}
        if settings_file.exists():
            keys = json.loads(settings_file.read_text()).get("keys", {})

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        type_prompts = {
            "text":          "Extract all text from this image. Return plain text only.",
            "figure":        "Describe this image/figure in detail for a technical document.",
            "table_simple":  "Convert this table to Markdown table format.",
            "table_complex": "Convert this complex table to HTML format preserving all merged cells.",
            "table":         "Convert this table to HTML format.",
            "formula":       "Convert this mathematical formula to LaTeX. Return only the LaTeX code.",
        }
        prompt = type_prompts.get(block_type, "Extract content from this image.")

        if model_id == "openrouter":
            api_key = keys.get("openrouter", "")
            if not api_key:
                raise ValueError("OpenRouter API key не задан — добавь в Settings")
            r = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer":  "https://prms-local",
                    "X-Title":       "PRMS Table Extractor",
                },
                json={
                    "model": "openai/gpt-4o",
                    "messages": [{"role": "user", "content": [
                        {"type": "text",      "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]}],
                    "max_tokens": 2048,
                },
                timeout=60,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

        elif model_id == "gpt4o":
            api_key = keys.get("openai", "")
            if not api_key:
                raise ValueError("OpenAI API key не задан")
            r = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": [
                        {"type": "text",      "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]}],
                    "max_tokens": 2048,
                },
                timeout=60,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

        elif model_id == "claude":
            api_key = keys.get("anthropic", "")
            if not api_key:
                raise ValueError("Anthropic API key не задан")
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key":         api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model":      "claude-3-5-sonnet-20241022",
                    "max_tokens": 2048,
                    "messages": [{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                        {"type": "text",  "text": prompt},
                    ]}],
                },
                timeout=60,
            )
            r.raise_for_status()
            return r.json()["content"][0]["text"]

        raise ValueError(f"Неизвестная облачная модель: {model_id}")
