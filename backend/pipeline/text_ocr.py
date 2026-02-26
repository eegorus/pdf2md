"""
text_ocr.py — распознавание текстовых блоков через EasyOCR

Вход:  PIL Image блока (уже вырезанный)
Выход: строка распознанного текста

Почему EasyOCR а не Tesseract:
- Нативная поддержка GPU без доп. настроек
- Лучше работает с нестандартными шрифтами (технические документы)
- Поддержка кириллицы + латиницы в одном документе из коробки
"""
import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageEnhance

logger = logging.getLogger("prms.text_ocr")


class TextOCR:
    def __init__(self, reader):
        """
        reader: уже инициализированный easyocr.Reader из ModelRegistry
        Не создаём новый Reader — это дорого (~3 сек и VRAM).
        """
        self.reader = reader
        logger.info("TextOCR инициализирован")

    def preprocess(self, image: Image.Image) -> Image.Image:
        """
        Предобработка изображения перед OCR.

        Шаги:
        1. Конвертация в RGB (на случай RGBA/Grayscale)
        2. Масштабирование если блок слишком мелкий (<100px высотой)
           EasyOCR теряет качество на маленьких изображениях
        3. Небольшое повышение резкости — улучшает распознавание
           размытых сканов

        НЕ делаем:
        - Бинаризацию (Otsu threshold) — EasyOCR справляется сам
        - Агрессивное повышение контраста — ломает полутона
        """
        img = image.convert("RGB")

        # Масштабируем мелкие блоки
        w, h = img.size
        min_height = 80
        if h < min_height:
            scale = min_height / h
            img = img.resize(
                (int(w * scale), int(h * scale)),
                Image.LANCZOS
            )

        # Лёгкое повышение резкости
        img = img.filter(ImageFilter.SHARPEN)

        return img

    def recognize(
        self,
        image: Image.Image,
        detail: int = 0,
        paragraph: bool = True,
    ) -> str:
        """
        Распознаёт текст в изображении.

        detail=0  → возвращает только строки текста (без bbox/confidence)
        detail=1  → возвращает [bbox, text, confidence] — для отладки

        paragraph=True → объединяет близкие строки в параграфы
                         (лучше для многострочных блоков)

        Возвращает строку. Несколько строк соединяются через '\n'.
        """
        img = self.preprocess(image)
        img_array = np.array(img)

        try:
            results = self.reader.readtext(
                img_array,
                detail=detail,
                paragraph=paragraph,
            )
        except Exception as e:
            logger.error(f"EasyOCR ошибка: {e}")
            return ""

        if detail == 0:
            # results = ['строка1', 'строка2', ...]
            text = "\n".join(str(r) for r in results if r)
        else:
            # results = [(bbox, text, conf), ...]
            text = "\n".join(r[1] for r in results if r[1])

        return text.strip()

    def recognize_file(self, image_path: str | Path) -> str:
        """Удобный метод для распознавания из файла."""
        image = Image.open(str(image_path)).convert("RGB")
        return self.recognize(image)
