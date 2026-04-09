"""
formula_ocr.py — распознавание формул через TexTeller Python API

Загружаем модель один раз при инициализации FormulaOCR,
а не на каждый вызов через subprocess.

Вход:  PIL Image формулы
Выход: строка LaTeX (например: r'\frac{Q_o}{B_o} + \frac{Q_g}{B_g}')
"""
import logging
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger("prms.formula_ocr")


class FormulaOCR:
    def __init__(self):
        self.model     = None
        self.tokenizer = None
        self._use_cli  = False
        self._load_model()

    def _load_model(self):
        """Загружает TexTeller один раз в память (Python API, не CLI)."""
        try:
            import texteller
            self.model     = texteller.load_model()
            self.tokenizer = texteller.load_tokenizer()
            logger.info("FormulaOCR: TexTeller Python API загружен в память")
        except Exception as e:
            logger.warning(f"FormulaOCR: Python API недоступен: {e}, пробуем CLI fallback")
            self.model     = None
            self.tokenizer = None
            self._init_cli_fallback()

    def _init_cli_fallback(self):
        """Fallback: CLI с коротким таймаутом."""
        import subprocess
        try:
            result = subprocess.run(
                ["texteller", "--help"],
                capture_output=True, timeout=5
            )
            self._use_cli = result.returncode == 0
        except Exception:
            self._use_cli = False
        if self._use_cli:
            logger.info("FormulaOCR: TexTeller CLI доступен (fallback режим)")

    def recognize(self, image: Image.Image) -> str | None:
        """Распознаёт формулу. Возвращает LaTeX или None."""
        image = image.convert("RGB")
        if self.model is not None and self.tokenizer is not None:
            return self._recognize_python_api(image)
        if self._use_cli:
            return self._recognize_cli(image)
        return None

    def _recognize_python_api(self, image: Image.Image) -> str | None:
        try:
            import torch
            import texteller
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            # img2latex принимает list[np.ndarray] (RGB)
            np_img  = np.array(image)
            results = texteller.img2latex(
                self.model, self.tokenizer, [np_img],
                device=device, out_format="latex",
            )
            latex = results[0].strip() if results else None
            if latex:
                logger.debug(f"FormulaOCR Python API: {latex[:80]}")
            return latex or None
        except Exception as e:
            logger.error(f"FormulaOCR Python API error: {e}")
            return None

    def _recognize_cli(self, image: Image.Image) -> str | None:
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
            image.save(tmp.name, format="PNG")
            try:
                result = subprocess.run(
                    ["texteller", "inference", tmp.name],
                    capture_output=True, text=True, timeout=20
                )
                if result.returncode != 0:
                    return None
                return result.stdout.strip() or None
            except subprocess.TimeoutExpired:
                logger.error("FormulaOCR CLI timeout (20s)")
                return None

    def recognize_file(self, image_path) -> str | None:
        return self.recognize(Image.open(str(image_path)).convert("RGB"))
