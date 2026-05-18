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

        self.unload_all_models()
        return block

    def process_document(self, doc_id: str, model_choices: dict | None = None, on_progress=None, on_cancel_check=None) -> dict:
        """
        OCR всех блоков с умным управлением VRAM при смене моделей.

        Блоки сортируются по семейству модели (local → dotsocr → cloud → ollama),
        чтобы минимизировать swap между VRAM моделями.
        При смене семейства предыдущая модель выгружается.
        После завершения всего батча — выгрузка всех моделей.
        """
        blocks_file = self.data_dir / "results" / doc_id / "blocks.json"
        if not blocks_file.exists():
            raise FileNotFoundError(f"blocks.json не найден: {blocks_file}")

        blocks = json.loads(blocks_file.read_text())

        already_done = sum(1 for b in blocks if b.get("status") == "ocr_done")
        if already_done == len(blocks):
            logger.warning(f"OCR {doc_id}: все блоки уже обработаны, пропускаем")
            return {"processed": already_done, "errors": 0, "by_type": {}}

        logger.info(f"OCR pipeline {doc_id}: {len(blocks)} блоков (уже готово: {already_done})")

        # Семейства моделей для управления VRAM
        NEEDS_DOTSOCR = {"table", "table_simple", "table_complex"}

        def get_model_family(block_type: str, mid: str | None) -> str:
            """Возвращает 'local' | 'dotsocr' | 'cloud' | 'ollama'."""
            if mid in ("gpt4o", "claude", "openrouter"):
                return "cloud"
            if mid == "ollama":
                return "ollama"
            if block_type in NEEDS_DOTSOCR and mid != "ollama_7b":
                return "dotsocr"
            if block_type == "figure":
                return "ollama"
            # formula: TexTeller (local/CPU), formula с ollama_7b → ollama
            if block_type == "formula":
                return "local"
            return "local"

        # Сортируем: сначала local/easyocr, потом dotsocr, потом cloud, потом ollama
        # Внутри каждой группы — порядок страниц сохраняется
        FAMILY_ORDER = {"local": 0, "dotsocr": 1, "cloud": 2, "ollama": 3}

        pending = [b for b in blocks if b.get("status") != "ocr_done"]
        done    = [b for b in blocks if b.get("status") == "ocr_done"]

        pending_sorted = sorted(
            pending,
            key=lambda b: (
                FAMILY_ORDER.get(
                    get_model_family(
                        b.get("block_type", "text"),
                        (model_choices or {}).get(b.get("block_type", "text"))
                    ), 99
                ),
                b.get("page_num", 0),
            )
        )

        family_counts = {}
        for b in pending_sorted:
            fam = get_model_family(b.get("block_type", "text"),
                                   (model_choices or {}).get(b.get("block_type", "text")))
            family_counts[fam] = family_counts.get(fam, 0) + 1
        logger.info("Порядок обработки: " + ", ".join(f"{k}={v}" for k, v in family_counts.items()))

        stats         = {"processed": already_done, "errors": 0, "by_type": {}}
        active_family = None

        for i, block in enumerate(pending_sorted):
            # Проверка отмены — первое что делаем в итерации
            if on_cancel_check and on_cancel_check():
                logger.info(f"OCR {doc_id} отменён на блоке {i}/{len(pending_sorted)}")
                blocks_file.write_text(
                    json.dumps(done + pending_sorted, ensure_ascii=False, indent=2)
                )
                return {"status": "cancelled", "processed": stats["processed"],
                        "errors": stats["errors"], "by_type": stats["by_type"]}

            block_type = block.get("block_type", "text")
            image_path = block.get("image_path", "")
            model_id   = (model_choices or {}).get(block_type)
            needed_fam = get_model_family(block_type, model_id)

            # Управление VRAM при смене семейства моделей
            if active_family is not None and active_family != needed_fam:
                logger.info(f"Смена модели: {active_family} → {needed_fam}, выгружаем {active_family}")
                import torch, gc
                if active_family == "ollama":
                    self.unload_ollama()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                gc.collect()

            active_family = needed_fam

            if not image_path or not Path(image_path).exists():
                logger.warning(f"Блок {block['block_id']}: файл не найден")
                block["output"] = "[error: image not found]"
                block["status"] = "error"
                stats["errors"] += 1
                if on_progress and (i + 1) % 10 == 0:
                    on_progress(stats["processed"], stats["errors"])
                continue

            try:
                image  = Image.open(image_path).convert("RGB")
                output = self._process_block(image, block_type, model_id=model_id)

                block["output"] = output
                if not block.get("original_output"):
                    block["original_output"] = output
                block["status"] = "ocr_done"
                stats["processed"] += 1
                stats["by_type"][block_type] = stats["by_type"].get(block_type, 0) + 1

                # Очистка VRAM после каждой таблицы — dots.ocr жадный
                if block_type in NEEDS_DOTSOCR:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()

                if (i + 1) % 10 == 0:
                    logger.info(f"  Обработано {i+1}/{len(pending_sorted)} блоков...")
                    blocks_file.write_text(
                        json.dumps(done + pending_sorted, ensure_ascii=False, indent=2)
                    )
                    if on_progress:
                        on_progress(stats["processed"], stats["errors"])

            except Exception as e:
                logger.error(f"Блок {block['block_id']}: {e}", exc_info=True)
                block["output"] = f"[error: {type(e).__name__}]"
                block["status"] = "error"
                stats["errors"] += 1

        # Финальный сброс на диск
        blocks_file.write_text(
            json.dumps(done + pending_sorted, ensure_ascii=False, indent=2)
        )
        if on_progress:
            on_progress(stats["processed"], stats["errors"])

        # Выгружаем все модели из VRAM
        logger.info("OCR завершён. Выгружаем все модели из VRAM...")
        self.unload_all_models()

        # Обновляем метаданные
        meta_file = self.data_dir / "uploads" / doc_id / "meta.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
            meta["status"] = "ocr_done"
            meta["ocr_stats"] = stats
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

        logger.info(f"✅ OCR {doc_id}: ok={stats['processed']}, err={stats['errors']}")
        return stats

    def _unload_table_model(self):
        """dots.ocr остаётся в VRAM — 6 GB, места достаточно на RTX 4090 (24 GB).
        Метод оставлен для совместимости, но ничего не делает."""
        logger.debug("_unload_table_model: пропускаем (dots.ocr остаётся на GPU)")

    def unload_ollama(self) -> None:
        """Принудительно выгружает Ollama-модели из VRAM через keep_alive: 0."""
        import httpx
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
        models_to_unload = {
            os.getenv("OLLAMA_FALLBACK_MODEL", "qwen2.5vl:7b"),
            os.getenv("OLLAMA_FIGURE_MODEL",   "qwen2.5vl:3b"),
            os.getenv("OLLAMA_FORMULA_MODEL",  "qwen2.5vl:3b"),
        }
        for model in models_to_unload:
            try:
                httpx.post(
                    f"{ollama_url}/api/generate",
                    json={"model": model, "keep_alive": 0},
                    timeout=10,
                )
                logger.info(f"Ollama: {model} выгружен из VRAM")
            except Exception as e:
                logger.warning(f"Ollama unload {model}: {e}")

    def unload_all_models(self) -> None:
        """Выгружает Ollama из VRAM. dots.ocr остаётся загруженным на GPU."""
        import torch, gc
        self.unload_ollama()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
        gc.collect()
        if torch.cuda.is_available():
            used = torch.cuda.memory_allocated() / 1e9
            logger.info(f"VRAM после выгрузки Ollama: {used:.1f} GB PyTorch allocated")

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
                # Если явно выбран Ollama fallback — используем его
                if model_id == "ollama_7b":
                    logger.info(f"Использую Ollama fallback для таблицы (явный выбор)")
                    return self.fallback.process(image, "table")

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
                logger.warning("dots.ocr недоступен — fallback на Ollama для таблицы")
                return self.fallback.process(image, "table")

            elif block_type == "formula":
                # Если явно выбрана облачная модель — идём напрямую
                if model_id in ("gpt4o", "claude", "openrouter"):
                    return self._process_cloud(image, "formula", model_id)
                # Если явно выбран Ollama — пропускаем TexTeller
                if model_id == "ollama":
                    return self._formula_via_ollama(image)
                # Дефолт: TexTeller Python API
                if self.formula_ocr:
                    result = self.formula_ocr.recognize(image)
                    if result:
                        return self._normalize_latex(result)
                # Fallback: Ollama с лёгкой моделью
                return self._formula_via_ollama(image)

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

    def _formula_via_ollama(self, image: "Image.Image") -> str:
        """Распознаёт формулу через Ollama с улучшенным промптом."""
        formula_model = os.getenv(
            "OLLAMA_FORMULA_MODEL",
            os.getenv("OLLAMA_FIGURE_MODEL",
                      os.getenv("OLLAMA_FALLBACK_MODEL", "qwen2.5vl:7b"))
        )
        prompt = (
            "This image contains a mathematical formula or equation. "
            "Convert it to LaTeX. Return ONLY the LaTeX expression wrapped in $$...$$. "
            "Example: $$\\sigma_{\\max} = 20.69\\text{ MPa}$$. "
            "No explanations, no surrounding text."
        )
        return self.fallback.process_with_model(image, "formula",
                                                model=formula_model,
                                                prompt=prompt)

    def _normalize_latex(self, latex: str) -> str:
        """Нормализует LaTeX вывод в $$...$$ для Markdown рендеринга."""
        import re
        latex = latex.strip()
        latex = re.sub(r'\n{2,}', '\n', latex)
        if latex.startswith("$$") and latex.endswith("$$"):
            return latex
        if latex.startswith("\\[") and latex.endswith("\\]"):
            return "$$" + latex[2:-2].strip() + "$$"
        if not latex.startswith(("$", "\\(")):
            return f"$$\n{latex}\n$$"
        return latex

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

        typeprompts = {
            "text": "Extract all text from this image exactly as written. Preserve paragraph breaks. Return plain text only.",
            "table": """Reproduce this table in GitHub-Flavored Markdown (GFM) pipe table format.
Rules:
- Include ALL rows and columns — never skip any data.
- Add separator row (---|---|---) after the first (header) row.
- Use right-align (---:) for numeric columns, left-align for text.
- If cells are merged (colspan/rowspan), use HTML <table> instead of GFM.
- Empty cells: use a single space.
Return ONLY the Markdown table, no explanation.""",
            "table_simple": """Reproduce this simple table in GitHub-Flavored Markdown pipe table format.
Include ALL rows and columns. Add a separator row after the header.
Use right-align (---:) for numbers. Return ONLY the Markdown table.""",
            "table_complex": """Reproduce this complex table. It may have merged cells, multi-level headers, or nested structure.
If merged cells are present, use HTML <table> with colspan/rowspan attributes.
If no merged cells, use GFM pipe table. Include ALL data — never skip rows or columns.
Return ONLY the table markup (HTML or Markdown), no explanation.""",
            "figure": """Describe this figure, chart, diagram, or image in detail for a technical document.
Output format:

![<type>]()

**Figure:** <Two to four sentences describing: what type of visualization this is,
what data or subject it shows, key values or labels visible, and any legend items.>

Where <type> is one of: chart | diagram | photo | map | screenshot | illustration

Example output:

![chart]()

**Figure:** Line chart with time (months) on the X-axis and pressure (MPa) on the Y-axis.
Three wells are shown: Well-1 (blue), Well-2 (red), Well-3 (green).
Well-1 starts at 28.5 MPa and declines to 14.2 MPa by month 12.

Return ONLY this formatted output.""",
            "formula": "Convert this mathematical formula or equation to LaTeX. Return ONLY the LaTeX expression wrapped in \\(...\\) for inline or \\[...\\] for block. No explanations.",
        }

        user_prompt = typeprompts.get(block_type, "Extract content from this image.")

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
                json={"model": "openai/gpt-4o", "messages": [
                    {"role": "user", "content": [
                        {"type": "text",      "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]},
                ], "max_tokens": 4096},
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
                json={"model": "gpt-4o", "messages": [
                    {"role": "user", "content": [
                        {"type": "text",      "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]},
                ], "max_tokens": 4096},
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
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                json={
                    "model":      "claude-3-5-sonnet-20241022",
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                        {"type": "text",  "text": user_prompt},
                    ]}],
                },
                timeout=60,
            )
            r.raise_for_status()
            return r.json()["content"][0]["text"]

        raise ValueError(f"Неизвестная облачная модель: {model_id}")
