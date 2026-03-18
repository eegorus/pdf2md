"""
layout_detector.py — находит блоки на странице через DocLayout-YOLO

Вход:  PIL Image или путь к PNG-файлу страницы, модель из ModelRegistry
Выход: список BlockDetection (тип блока + координаты + confidence)

Классы DocLayout-YOLO (из DocStructBench):
  0: title
  1: plain text
  2: abandon       (колонтитулы, номера страниц — игнорируем)
  3: figure
  4: figure_caption
  5: table
  6: table_caption
  7: table_footnote
  8: isolate_formula   (отдельная формула-блок)
  9: formula_caption

Маппинг на наши типы: text/table/figure/formula
"""
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# ── Layout postprocess tuning ─────────────────────────────────
MERGE_GAP_PX            = 40   # макс. зазор между text-блоками для склейки
TABLE_HEADER_GAP_PX  = 120  # макс. зазор header-row → body для склейки таблицы
TABLE_COMPLEX_RATIO     = 0.80  # ширина > 80% страницы → сложная
TABLE_COMPLEX_MIN_H     = 400  # высота > 400px → сложная (≈8+ строк при 300dpi)
# ──────────────────────────────────────────────────────────────

logger = logging.getLogger("prms.layout_detector")

# ── Маппинг классов DocLayout-YOLO → наши типы блоков ────────────────
# Ключ: имя класса из модели (lowercase)
# Значение: наш тип для pipeline
YOLO_CLASS_MAP = {
    "title":            "text",
    "plain text":       "text",
    "abandon":          None,          # Пропускаем колонтитулы
    "figure":           "figure",
    "figure_caption":   "text",        # Подпись к рисунку = текст
    "table":            "table",
    "table_caption":    "text",        # Подпись к таблице = текст
    "table_footnote":   "text",
    "isolate_formula":  "formula",
    "formula_caption":  "text",
}

# ── Классификатор table_simple / table_complex ────────────────────────────
PLAIN_TEXT_CONF_THRESHOLD = 0.15



@dataclass
class BlockDetection:
    """Один обнаруженный блок на странице."""
    block_type: str          # text / table / figure / formula
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float
    raw_class: str           # оригинальный класс из YOLO
    page_num: int
    block_idx: int           # порядковый номер на странице


class LayoutDetector:
    def __init__(
        self,
        model,                           # YOLOv10 объект из ModelRegistry
        confidence_threshold: float = 0.3,
        imgsz: int = 1024,
    ):
        """
        model: уже загруженный YOLOv10 из main.py (models.layout_model)
        confidence_threshold: минимальный порог уверенности (0.3 = мягкий)
        imgsz: размер входного изображения для модели (1024 = родной)
        """
        self.model = model
        self.conf = confidence_threshold
        self.imgsz = imgsz
        logger.info(
            f"LayoutDetector: conf={confidence_threshold}, imgsz={imgsz}"
        )

    def detect(
        self,
        image: Image.Image | str | Path,
        page_num: int = 1,
    ) -> list[BlockDetection]:
        """
        Запускает детекцию блоков на странице.

        image: PIL Image или путь к PNG-файлу
        page_num: номер страницы (для логов и BlockDetection)

        Возвращает список BlockDetection, отсортированный сверху-вниз
        (по y1 координатe) — это естественный порядок чтения документа.
        """
        # Загружаем изображение если передан путь
        if isinstance(image, (str, Path)):
            image = Image.open(str(image)).convert("RGB")

        # YOLOv10.predict — стандартный API из ultralytics
        # verbose=False — убираем спам в логи от YOLO
        results = self.model.predict(
            source=image,
            imgsz=self.imgsz,
            conf=self.conf,
            verbose=False,
        )

        blocks: list[BlockDetection] = []
        block_idx = 0

        if not results or len(results) == 0:
            logger.warning(f"Страница {page_num}: YOLO вернул пустой результат")
            return blocks

        result = results[0]  # batch=1, берём первый (единственный) результат

        if result.boxes is None or len(result.boxes) == 0:
            logger.info(f"Страница {page_num}: блоки не найдены")
            return blocks

        # result.boxes.data: тензор [N, 6] — (x1, y1, x2, y2, conf, class_id)
        for box in result.boxes.data.tolist():
            x1, y1, x2, y2, conf, class_id = box
            class_id = int(class_id)

            # Получаем имя класса
            raw_class = result.names.get(class_id, f"class_{class_id}")

            # Маппим на наш тип
            block_type = YOLO_CLASS_MAP.get(raw_class.lower())

            # None = abandon (колонтитулы) — пропускаем
            if block_type is None:
                continue

            # Округляем координаты до целых пикселей
            blocks.append(BlockDetection(
                block_type=block_type,
                x1=max(0, int(x1)),
                y1=max(0, int(y1)),
                x2=int(x2),
                y2=int(y2),
                confidence=round(conf, 4),
                raw_class=raw_class,
                page_num=page_num,
                block_idx=block_idx,
            ))
            block_idx += 1

        # Сортируем сверху-вниз (порядок чтения)
        blocks.sort(key=lambda b: (b.y1, b.x1))

        # Переназначаем block_idx после сортировки
        for i, b in enumerate(blocks):
            b.block_idx = i

        # Постобработка: merge text, split tables, classify table type
        blocks = self._postprocess(blocks, image)

        logger.info(
            f"Страница {page_num}: найдено {len(blocks)} блоков "
            f"({self._stats(blocks)})"
        )
        return blocks


    # ═══════════════════════════════════════════════════════════════
    #  POSTPROCESS PIPELINE
    # ═══════════════════════════════════════════════════════════════

    def _postprocess(self, blocks, image):
        blocks = self._merge_table_fragments(blocks)
        blocks = self._merge_text_blocks(blocks)
        blocks = self._classify_tables(blocks, image)
        blocks = self._split_merged_tables(blocks, image)
        for i, b in enumerate(blocks):
            b.block_idx = i
        return blocks

    def _merge_table_fragments(self, blocks):
        """Склеивает header-row таблицы (отдельный bbox) с телом."""
        table_types = {"table", "table_simple", "table_complex"}
        tables = [b for b in blocks if b.block_type in table_types]
        others = [b for b in blocks if b.block_type not in table_types]
        if len(tables) < 2:
            return blocks
        tables.sort(key=lambda b: (b.page_num, b.y1))
        merged_flags = [False] * len(tables)
        result = []
        for i, top in enumerate(tables):
            if merged_flags[i]:
                continue
            current = top
            for j in range(i + 1, len(tables)):
                if merged_flags[j]:
                    continue
                bot = tables[j]
                if current.page_num != bot.page_num:
                    break
                gap = bot.y1 - current.y2
                if gap > TABLE_HEADER_GAP_PX:
                    break
                overlap_x = min(current.x2, bot.x2) - max(current.x1, bot.x1)
                span      = max(current.x2, bot.x2) - min(current.x1, bot.x1)
                if span > 0 and overlap_x / span >= 0.6:
                    current = type(current)(
                        block_type=bot.block_type,
                        x1=min(current.x1, bot.x1), y1=current.y1,
                        x2=max(current.x2, bot.x2), y2=bot.y2,
                        confidence=max(current.confidence, bot.confidence),
                        raw_class=current.raw_class + "+hdr",
                        page_num=current.page_num,
                        block_idx=current.block_idx,
                    )
                    merged_flags[j] = True
            result.append(current)
        result += others
        result.sort(key=lambda b: (b.page_num, b.y1, b.x1))
        return result

    def _merge_text_blocks(self, blocks):
        """Склеивает text-блоки с одинаковым X и малым вертикальным зазором."""
        text_blocks  = [b for b in blocks if b.block_type == "text"]
        other_blocks = [b for b in blocks if b.block_type != "text"]
        if len(text_blocks) < 2:
            return blocks
        text_blocks.sort(key=lambda b: (b.page_num, b.y1))
        used = [False] * len(text_blocks)
        result = []
        for i, b1 in enumerate(text_blocks):
            if used[i]:
                continue
            group = [b1]
            used[i] = True
            for j in range(i + 1, len(text_blocks)):
                if used[j]:
                    continue
                b2 = text_blocks[j]
                if b1.page_num != b2.page_num:
                    break
                if b2.y1 - group[-1].y2 > MERGE_GAP_PX:
                    break
                x_close = abs(b1.x1 - b2.x1) < 80 and abs(b1.x2 - b2.x2) < 80
                if x_close:
                    group.append(b2)
                    used[j] = True
            if len(group) > 1:
                result.append(type(b1)(
                    block_type="text",
                    x1=min(b.x1 for b in group), y1=min(b.y1 for b in group),
                    x2=max(b.x2 for b in group), y2=max(b.y2 for b in group),
                    confidence=max(b.confidence for b in group),
                    raw_class="plain text (merged)",
                    page_num=b1.page_num, block_idx=b1.block_idx,
                ))
            else:
                result.append(b1)
        result += other_blocks
        result.sort(key=lambda b: (b.page_num, b.y1, b.x1))
        return result

    def _classify_tables(self, blocks, image):
        """Простая / сложная таблица по размеру относительно страницы."""
        img_w, _ = image.size
        for b in blocks:
            if b.block_type not in ("table", "table_simple", "table_complex"):
                continue
            w = b.x2 - b.x1
            h = b.y2 - b.y1
            b.block_type = (
                "table_complex"
                if (w > img_w * TABLE_COMPLEX_RATIO or h > TABLE_COMPLEX_MIN_H or (h and w / h > 2.5))
                else "table_simple"
            )
        return blocks

    def _split_merged_tables(self, blocks, image):
        """Разрезает аномально высокую таблицу по светлой горизонтальной полосе."""
        import numpy as np
        result = []
        for b in blocks:
            if b.block_type not in ("table_simple", "table_complex"):
                result.append(b)
                continue
            h = b.y2 - b.y1
            if h < 250:
                result.append(b)
                continue
            try:
                crop = image.crop((b.x1, b.y1, b.x2, b.y2)).convert("L")
                arr  = np.array(crop)
                row_bright = arr.mean(axis=1)
                GAP_THR, MIN_GAP = 240, 25
                in_gap, gap_start, best_gap, best_len = False, 0, None, 0
                for ri, bright in enumerate(row_bright):
                    if bright > GAP_THR:
                        if not in_gap:
                            in_gap, gap_start = True, ri
                    else:
                        if in_gap:
                            gap_len = ri - gap_start
                            in_margin = gap_start < h * 0.1 or ri > h * 0.9
                            if gap_len > MIN_GAP and gap_len > best_len and not in_margin:
                                best_gap, best_len = (gap_start, ri), gap_len
                            in_gap = False
                if best_gap:
                    split_y = b.y1 + (best_gap[0] + best_gap[1]) // 2
                    for y1, y2 in [(b.y1, split_y), (split_y, b.y2)]:
                        result.append(type(b)(
                            block_type=b.block_type, x1=b.x1, y1=y1, x2=b.x2, y2=y2,
                            confidence=b.confidence, raw_class=b.raw_class,
                            page_num=b.page_num, block_idx=b.block_idx,
                        ))
                    continue
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"_split_merged_tables: {e}")
            result.append(b)
        return result

    def crop_block(
        self,
        image: Image.Image,
        block: BlockDetection,
        padding: int = 5,
    ) -> Image.Image:
        """
        Вырезает блок из изображения страницы с небольшим отступом.

        padding: пиксели отступа вокруг bbox (улучшает качество OCR)
        """
        w, h = image.size
        x1 = max(0, block.x1 - padding)
        y1 = max(0, block.y1 - padding)
        x2 = min(w, block.x2 + padding)
        y2 = min(h, block.y2 + padding)
        return image.crop((x1, y1, x2, y2))

    def save_block_image(
        self,
        image: Image.Image,
        block: BlockDetection,
        doc_id: str,
        data_dir: str | Path,
        padding: int = 5,
    ) -> str:
        """
        Вырезает блок и сохраняет как PNG.

        Возвращает путь к сохранённому файлу.
        Формат имени: page_001_text_000.png
        """
        data_dir = Path(data_dir)
        blocks_dir = data_dir / "blocks" / doc_id
        blocks_dir.mkdir(parents=True, exist_ok=True)

        cropped = self.crop_block(image, block, padding)
        filename = (
            f"page_{block.page_num:03d}_"
            f"{block.block_type}_"
            f"{block.block_idx:03d}.png"
        )
        path = blocks_dir / filename
        cropped.save(str(path), format="PNG")
        return str(path)

    @staticmethod
    def _stats(blocks: list[BlockDetection]) -> str:
        """Краткая статистика для лога."""
        from collections import Counter
        counts = Counter(b.block_type for b in blocks)
        return ", ".join(f"{t}:{n}" for t, n in sorted(counts.items()))
