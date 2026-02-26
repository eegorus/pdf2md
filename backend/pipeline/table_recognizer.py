"""
table_recognizer.py — распознавание таблиц через dots.ocr

dots.ocr (rednote-hilab) — специализированная модель для таблиц,
основана на Qwen2-VL архитектуре. Возвращает HTML-разметку таблицы.

Вход:  PIL Image таблицы
Выход: HTML-строка <table>...</table>

Почему HTML а не Markdown:
- HTML сохраняет colspan/rowspan (объединённые ячейки)
- Легко конвертируется в DataFrame через pandas.read_html()
- Фронтенд рендерит HTML нативно
"""
import logging
import re
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoProcessor, AutoModelForCausalLM

logger = logging.getLogger("prms.table_recognizer")

# Системный промпт для dots.ocr
# Взят из официальной документации rednote-hilab/dots.ocr
DOTS_SYSTEM_PROMPT = (
    "You are a document understanding assistant. "
    "Convert the table in the image to HTML format. "
    "Preserve all cell content, merged cells (colspan/rowspan), "
    "headers, and structure exactly as shown."
)

DOTS_USER_PROMPT = "Convert this table to HTML."


class TableRecognizer:
    def __init__(self, model, processor):
        """
        model:     AutoModelForCausalLM (DotsOCR) из ModelRegistry
        processor: AutoProcessor из ModelRegistry
        Модели уже на GPU — не перемещаем их здесь.
        """
        self.model     = model
        self.processor = processor
        logger.info("TableRecognizer инициализирован (dots.ocr)")

    def recognize(self, image: Image.Image) -> str:
        """
        Распознаёт таблицу и возвращает HTML.

        Если модель вернула некорректный HTML — возвращаем
        то что есть, фронтенд разберётся.
        """
        image = image.convert("RGB")

        # Масштабируем большие таблицы — главная причина OOM
        # dots.ocr пытается выделить 6-12 ГБ на таблицы >2MP
        # resize до 2MP решает OOM без заметной потери качества
        from shared.utils import resize_for_inference
        image, was_resized = resize_for_inference(image, max_pixels=2_000_000)
        if was_resized:
            logger.debug(f"Таблица масштабирована для инференса")


        # Формируем chat-сообщение в формате Qwen2-VL
        messages = [
            {
                "role": "system",
                "content": DOTS_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text",  "text": DOTS_USER_PROMPT},
                ],
            },
        ]

        # apply_chat_template подготавливает текст в формат модели
        text_input = self.processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

        # Обрабатываем изображение
        inputs = self.processor(
            text=[text_input],
            images=[image],
            return_tensors="pt",
            padding=True,
        ).to("cuda")

        with torch.no_grad():
            output_ids = self.model.generate(
                **inputs,
                max_new_tokens=2048,    # таблицы могут быть длинными
                do_sample=False,        # greedy decoding — детерминированность
                temperature=None,       # отключаем при do_sample=False
                top_p=None,
                repetition_penalty=1.1, # борьба с повторяющимися токенами
                pad_token_id=self.processor.tokenizer.eos_token_id,
            )

        # Декодируем только новые токены (не входной prompt)
        input_len  = inputs["input_ids"].shape[1]
        new_tokens = output_ids[:, input_len:]
        result     = self.processor.batch_decode(
            new_tokens,
            skip_special_tokens=True,
        )[0]

        # Очищаем результат
        html = self._clean_html(result.strip())
        return html

    def recognize_file(self, image_path: str | Path) -> str:
        """Удобный метод для распознавания из файла."""
        image = Image.open(str(image_path)).convert("RGB")
        return self.recognize(image)

    def html_to_text(self, html: str) -> str:
        """
        Конвертирует HTML таблицы в plain text для индексации/поиска.
        Простая реализация через regex — без BeautifulSoup.
        """
        # Убираем теги
        text = re.sub(r"<[^>]+>", " ", html)
        # Нормализуем пробелы
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _clean_html(raw: str) -> str:
        """
        Вытаскивает HTML-блок из ответа модели.
        Модель иногда оборачивает ответ в markdown ```html ... ```
        """
        # Убираем markdown code blocks
        raw = re.sub(r"```html\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)

        # Если есть <table> — берём от него
        table_match = re.search(r"(<table[\s\S]*?</table>)", raw, re.IGNORECASE)
        if table_match:
            return table_match.group(1).strip()

        return raw.strip()
