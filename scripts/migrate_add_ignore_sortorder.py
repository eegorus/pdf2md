#!/usr/bin/env python3
"""
Добавляет поля ignore=False и sort_order=blockidx во все существующие блоки.
Идемпотентен — повторный запуск безопасен.
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "results"

for blocks_file in DATA_DIR.rglob("blocks.json"):
    blocks = json.loads(blocks_file.read_text())
    changed = False
    for i, b in enumerate(blocks):
        if "ignore" not in b:
            b["ignore"] = False
            changed = True
        if "sort_order" not in b:
            b["sort_order"] = b.get("blockidx", i)
            changed = True
    if changed:
        blocks_file.write_text(json.dumps(blocks, ensure_ascii=False, indent=2))
        print(f"Updated: {blocks_file}")
    else:
        print(f"OK: {blocks_file}")

print("Done.")
