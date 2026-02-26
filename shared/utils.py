import uuid
import hashlib
from pathlib import Path
from datetime import datetime


def generate_doc_id(filename: str) -> str:
    """
    Генерируем уникальный ID документа на основе имени файла + timestamp.
    Используем короткий hash чтобы ID был читаемым но уникальным.
    """
    timestamp = datetime.utcnow().isoformat()
    raw = f"{filename}_{timestamp}_{uuid.uuid4()}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def generate_block_id(doc_id: str, page_num: int, block_type: str, idx: int) -> str:
    """
    Формат: {doc_id}_p{page:03d}_{type}_{idx:03d}
    Пример: a3f9b2c1d4e5_p001_table_002
    """
    return f"{doc_id}_p{page_num:03d}_{block_type}_{idx:03d}"


def generate_pair_id() -> str:
    """Уникальный 6-значный ID для training pair."""
    return str(uuid.uuid4().int)[:6].zfill(6)


def ensure_dir(path: str | Path) -> Path:
    """Создать директорию если не существует, вернуть Path объект."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_result_dir(base_data_path: str, doc_id: str, block_type: str) -> Path:
    """
    Возвращает путь к директории результатов для конкретного типа блоков.
    Пример: /app/data/results/a3f9b2c1d4e5/tables/
    """
    path = Path(base_data_path) / "results" / doc_id / f"{block_type}s"
    return ensure_dir(path)


def blocks_to_markdown(blocks: list) -> str:
    """
    Конвертирует список блоков OCR-результатов в Markdown.
    text → параграф, table → HTML as-is, formula → LaTeX-блок, figure → описание.
    """
    lines = []
    current_page = None

    for block in blocks:
        page = block.get("page_num", 0)
        if page != current_page:
            current_page = page
            lines.append(f"\n## Страница {page}\n")

        btype  = block.get("block_type", "text")
        output = block.get("output") or ""
        conf   = block.get("confidence", 0)
        bid    = block.get("block_id", "")

        if not output:
            continue

        if btype == "text":
            lines.append(output.strip())
            lines.append("")
        elif btype == "table":
            lines.append(f"<!-- table: {bid} (conf={conf:.2f}) -->")
            lines.append(output.strip())
            lines.append("")
        elif btype == "formula":
            latex = output.strip().lstrip("$").rstrip("$").strip()
            lines.append(f"$$\n{latex}\n$$")
            lines.append("")
        elif btype == "figure":
            lines.append(f"*[Figure: {bid}]*")
            if output:
                lines.append(f"> {output.strip()}")
            lines.append("")
        else:
            lines.append(output.strip())
            lines.append("")

    return "\n".join(lines)
