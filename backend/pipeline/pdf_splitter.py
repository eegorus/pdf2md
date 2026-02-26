"""
pdf_splitter.py — разбивает PDF на страницы (PNG 300 DPI)

Вход:  путь к PDF-файлу, doc_id, базовая директория данных
Выход: список путей к PNG-файлам страниц

Почему 300 DPI:
- 72 DPI (экранное) — DocLayout-YOLO теряет мелкие блоки
- 150 DPI — таблицы с тонкими линиями плохо детектируются
- 300 DPI — стандарт для OCR, баланс качества и размера файла
- 600 DPI — избыточно, +4x памяти без прироста качества
"""
import logging
from pathlib import Path

from pdf2image import convert_from_path
from PIL import Image

logger = logging.getLogger("prms.pdf_splitter")


class PDFSplitter:
    def __init__(
        self,
        data_dir: str | Path,
        dpi: int = 300,
        output_format: str = "PNG",
    ):
        self.data_dir = Path(data_dir)
        self.dpi = dpi
        self.output_format = output_format
        # poppler-utils должен быть установлен в контейнере (apt)
        # pdf2image использует pdftoppm из этого пакета
        logger.info(f"PDFSplitter инициализирован: DPI={dpi}, format={output_format}")

    def split(self, pdf_path: str | Path, doc_id: str) -> list[dict]:
        """
        Разбивает PDF на страницы.

        Возвращает список словарей:
        [
          {
            "page_num": 1,            # 1-based
            "path": "/app/data/pages/doc_id/page_001.png",
            "width": 2480,
            "height": 3508,
          },
          ...
        ]
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF не найден: {pdf_path}")

        # Создаём директорию для страниц этого документа
        pages_dir = self.data_dir / "pages" / doc_id
        pages_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Конвертируем {pdf_path.name} → PNG ({self.dpi} DPI)...")

        # convert_from_path — основная функция pdf2image
        # thread_count=4 — параллельная конвертация страниц (4 потока CPU)
        # use_pdftocairo=False — pdftoppm быстрее для обычных PDF
        images: list[Image.Image] = convert_from_path(
            str(pdf_path),
            dpi=self.dpi,
            fmt=self.output_format.lower(),
            thread_count=4,
            use_pdftocairo=False,
        )

        page_count = len(images)
        logger.info(f"Получено {page_count} страниц")

        result = []
        for i, img in enumerate(images, start=1):
            # Имя файла: page_001.png, page_002.png и т.д.
            filename = f"page_{i:03d}.png"
            page_path = pages_dir / filename
            img.save(str(page_path), format="PNG", optimize=False)

            result.append({
                "page_num":  i,
                "path":      str(page_path),
                "width":     img.width,
                "height":    img.height,
            })

        logger.info(
            f"✅ Разбивка завершена: {page_count} страниц → {pages_dir}"
        )
        return result

    def get_page_count(self, pdf_path: str | Path) -> int:
        """Быстро возвращает количество страниц без конвертации."""
        from pdf2image.pdf2image import pdfinfo_from_path
        info = pdfinfo_from_path(str(pdf_path))
        return info["Pages"]
