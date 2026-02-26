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

        logger.info(
            f"Страница {page_num}: найдено {len(blocks)} блоков "
            f"({self._stats(blocks)})"
        )
        return blocks

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
