"""
formula_ocr.py — распознавание формул через TexTeller CLI

TexTeller принимает изображение, возвращает LaTeX-строку.
Запускается как subprocess — CLI инструмент, не Python API.

Вход:  PIL Image формулы
Выход: строка LaTeX (например: r'\frac{Q_o}{B_o} + \frac{Q_g}{B_g}')
"""
import logging
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

logger = logging.getLogger("prms.formula_ocr")


class FormulaOCR:
    def __init__(self, timeout: int = 60):
        """
        timeout: максимальное время ожидания TexTeller (секунды)
                 Первый запуск медленнее — модель грузится в память
        """
        self.timeout = timeout
        self._verify_cli()

    def _verify_cli(self):
        """Проверяем что texteller доступен в PATH."""
        try:
            result = subprocess.run(
                ["texteller", "--help"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info("FormulaOCR: TexTeller CLI доступен")
            else:
                logger.warning("FormulaOCR: TexTeller вернул ненулевой код")
        except FileNotFoundError:
            logger.error("FormulaOCR: texteller не найден в PATH!")

    def recognize(self, image: Image.Image) -> str:
        """
        Распознаёт формулу в изображении.

        Сохраняем во временный файл → вызываем CLI → читаем stdout.
        Временный файл удаляется автоматически через context manager.

        Возвращает LaTeX-строку или пустую строку при ошибке.
        """
        image = image.convert("RGB")

        with tempfile.NamedTemporaryFile(
            suffix=".png",
            delete=True,
            prefix="prms_formula_"
        ) as tmp:
            image.save(tmp.name, format="PNG")

            try:
                result = subprocess.run(
                    ["texteller", "inference", tmp.name],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired:
                logger.error(f"TexTeller timeout ({self.timeout}s)")
                return ""
            except Exception as e:
                logger.error(f"TexTeller subprocess error: {e}")
                return ""

        if result.returncode != 0:
            logger.warning(f"TexTeller stderr: {result.stderr[:200]}")
            return ""

        latex = result.stdout.strip()

        # TexTeller иногда оборачивает результат в $...$ или $$...$$
        # Убираем обёртку — фронтенд сам добавит нужное форматирование
        latex = latex.strip("$").strip()

        logger.debug(f"Formula OCR result: {latex[:80]}...")
        return latex

    def recognize_file(self, image_path: str | Path) -> str:
        """Удобный метод для распознавания из файла."""
        image = Image.open(str(image_path)).convert("RGB")
        return self.recognize(image)
