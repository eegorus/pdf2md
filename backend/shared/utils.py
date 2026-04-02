"""
shared/utils.py — общие утилиты для всего backend

Используется в роутерах и pipeline модулях.
"""
import hashlib
import time
import logging
from pathlib import Path

logger = logging.getLogger("prms.utils")


def generate_doc_id(filename: str) -> str:
    """
    Генерирует уникальный ID документа на основе имени файла + timestamp.

    Используем первые 12 символов SHA256 — достаточно для уникальности
    при разумном количестве документов (коллизия крайне маловероятна).

    Пример: "report_2022.pdf" + 1708123456.789 → "a3f9b2c1d4e5"
    """
    raw = f"{filename}:{time.time()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def ensure_dir(path: str | Path) -> Path:
    """
    Создаёт директорию если не существует, возвращает Path объект.
    Аналог mkdir -p + возврат пути для chaining.

    Пример:
        pdf_path = ensure_dir(DATA_DIR / "uploads" / doc_id) / "file.pdf"
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def format_vram_info() -> str:
    """
    Возвращает строку с текущим состоянием VRAM.
    Используется в логах при старте и health check.
    """
    try:
        import torch
        if not torch.cuda.is_available():
            return "CUDA недоступна"
        used  = torch.cuda.memory_allocated() / 1024**3
        total = torch.cuda.get_device_properties(0).total_memory / 1024**3
        free  = total - used
        return f"VRAM: {used:.1f}/{total:.1f} ГБ (свободно: {free:.1f} ГБ)"
    except Exception:
        return "VRAM: неизвестно"


def resize_for_inference(
    image,
    max_pixels: int = 2_000_000,
):
    """
    Масштабирует изображение если оно слишком большое для инференса.

    max_pixels=2_000_000 (~1414×1414px) — безопасный лимит для dots.ocr
    на RTX 4090 с 24 ГБ VRAM при bfloat16.

    Сохраняет aspect ratio через пропорциональное масштабирование.
    Возвращает (image, was_resized).
    """
    from PIL import Image as PILImage

    w, h   = image.size
    pixels = w * h

    if pixels <= max_pixels:
        return image, False

    scale  = (max_pixels / pixels) ** 0.5
    new_w  = int(w * scale)
    new_h  = int(h * scale)
    resized = image.resize((new_w, new_h), PILImage.LANCZOS)
    logger.debug(f"resize_for_inference: {w}×{h} → {new_w}×{new_h} (scale={scale:.2f})")
    return resized, True


def blocks_to_markdown(blocks: list[dict]) -> str:
    """
    Конвертирует список блоков OCR в читаемый Markdown документ.

    Структура:
    - Заголовок с метаданными
    - Блоки сгруппированы по страницам
    - Таблицы обёрнуты в HTML блок (рендерится в большинстве MD просмотрщиков)
    - Формулы в $$...$$
    - Рисунки как подписи
    """
    if not blocks:
        return "# Документ пустой\n"

    lines = []
    current_page = None

    for block in sorted(
        blocks,
        key=lambda b: (
            b.get("page_num", 0),
            b.get("bbox", [0, 0, 0, 0])[1],   # y1 — верхний край блока
            b.get("bbox", [0, 0, 0, 0])[0],   # x1 — левый край (тай-брейкер)
        )
    ):
        page_num   = block.get("page_num", 0)
        block_type = block.get("block_type", "text")
        output     = block.get("output") or ""

        # Заголовок страницы при смене
        if page_num != current_page:
            current_page = page_num
            lines.append(f"\n---\n\n## Страница {page_num}\n")

        if not output or "requires manual review" in output:
            lines.append(f"*[{block_type}: требует ручной проверки]*\n")
            continue

        if block_type == "text":
            lines.append(output + "\n")

        elif block_type in {"table", "table_simple", "table_complex"}:
            # Вырезаем только <table>...</table> — убираем <html><body> враппер
            import re
            table_match = re.search(
                r'(<table[\s\S]*?</table>)',
                output,
                re.IGNORECASE | re.DOTALL
            )
            if table_match:
                clean = table_match.group(1)
                wrapped = (
                    '<div style="overflow-x: auto; -webkit-overflow-scrolling: touch;">\n'
                    + clean
                    + "\n</div>"
                )
                lines.append("\n" + wrapped + "\n")
            else:
                lines.append("\n" + output + "\n")

        elif block_type == "formula":
            lines.append(f"\n$$\n{output}\n$$\n")

        elif block_type == "figure":
            image_path = block.get("image_path", "")

            # Чистим alt: убираем переносы, кавычки и скобки — они ломают Markdown
            raw_alt = (output or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
            raw_alt = raw_alt.replace('"', "'").replace("[", "(").replace("]", ")")
            alt = raw_alt.strip()[:200]

            if image_path and Path(image_path).exists():
                filename = Path(image_path).name
                lines.append(f"![](./blocks/{filename})")
                if alt:
                    lines.append(f"_{alt}_")
            else:
                lines.append(f"_{alt if alt else 'Figure'}_")

        else:
            lines.append(output + "\n")

    return "\n".join(lines)
