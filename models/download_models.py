#!/usr/bin/env python3
"""
Запускается ВНУТРИ backend-контейнера:
  docker compose run --rm -v "$(pwd)/models:/app/models" backend python /app/models/download_models.py
"""
import sys
import subprocess
from pathlib import Path

OK   = "\033[92m✅\033[0m"
WARN = "\033[93m⚠️ \033[0m"
ERR  = "\033[91m❌\033[0m"
INFO = "\033[94mℹ️ \033[0m"

def step(n, total, name):
    print(f"\n{'='*60}\n  [{n}/{total}] {name}\n{'='*60}")

def ok(msg):   print(f"  {OK}  {msg}")
def warn(msg): print(f"  {WARN} {msg}")
def info(msg): print(f"  {INFO} {msg}")
def err(msg):  print(f"  {ERR}  {msg}", file=sys.stderr)

# ── 1. DocLayout-YOLO ─────────────────────────────────────────
step(1, 4, "DocLayout-YOLO (~60 МБ)")
try:
    from huggingface_hub import hf_hub_download
    from doclayout_yolo import YOLOv10

    save_path = Path("/app/models/doclayout-yolo")
    save_path.mkdir(parents=True, exist_ok=True)
    model_file = save_path / "doclayout_yolo_docstructbench_imgsz1024.pt"

    if model_file.exists():
        ok(f"Уже скачан ({model_file.stat().st_size / 1e6:.0f} МБ)")
    else:
        info("Скачиваем с HuggingFace...")
        hf_hub_download(
            repo_id="juliozhao/DocLayout-YOLO-DocStructBench",
            filename="doclayout_yolo_docstructbench_imgsz1024.pt",
            local_dir=str(save_path)
        )
        ok("Скачан")

    # Верификация: просто загружаем модель
    model = YOLOv10(str(model_file))
    ok("Загрузка в память — OK")
    del model
except Exception as e:
    err(f"DocLayout-YOLO: {e}")
    sys.exit(1)

# ── 2. EasyOCR ────────────────────────────────────────────────
step(2, 4, "EasyOCR models (ru + en, ~100 МБ)")
try:
    import easyocr, numpy as np
    from PIL import Image, ImageDraw

    reader = easyocr.Reader(['ru', 'en'], gpu=True, download_enabled=True, verbose=False)

    img = Image.new('RGB', (300, 50), 'white')
    ImageDraw.Draw(img).text((10, 10), "PRMS Test 2026", fill='black')
    result = reader.readtext(np.array(img), detail=0)
    ok(f"EasyOCR работает, тест: {result}")
    del reader
except Exception as e:
    err(f"EasyOCR: {e}")
    sys.exit(1)

# ── 3. dots.ocr ───────────────────────────────────────────────
step(3, 4, "dots.ocr 1.7B (~5.8 ГБ)")
try:
    from huggingface_hub import snapshot_download

    save_path = Path("/app/models/dots-ocr")
    config_file = save_path / "config.json"

    if config_file.exists():
        # Считаем размер скачанного
        total_mb = sum(
            f.stat().st_size for f in save_path.rglob("*.safetensors")
        ) / 1e6
        ok(f"Уже скачан ({total_mb:.0f} МБ safetensors)")
    else:
        info("Скачиваем rednote-hilab/dots.ocr (~5.8 ГБ)...")
        save_path.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id="rednote-hilab/dots.ocr",
            local_dir=str(save_path),
            resume_download=True,
            ignore_patterns=["*.msgpack", "flax_model*", "*.ot"]
        )
        ok("dots.ocr скачан")

    # Верификация ТОЛЬКО через файлы — НЕ грузим модель
    # (загрузка через transformers сломана в 4.48+, фиксим пином)
    safetensors = list(save_path.rglob("*.safetensors"))
    if safetensors:
        total_mb = sum(f.stat().st_size for f in safetensors) / 1e6
        ok(f"Файлы модели на месте: {len(safetensors)} файлов ({total_mb:.0f} МБ)")
    else:
        err("Файлы .safetensors не найдены!")
        sys.exit(1)

except Exception as e:
    err(f"dots.ocr: {e}")
    sys.exit(1)

# ── 4. TexTeller ─────────────────────────────────────────────
step(4, 4, "TexTeller 3.0 — CLI инструмент")
try:
    # texteller — мета-пакет, не импортируется как Python-модуль.
    # Используется как CLI: texteller inference image.jpg
    # Проверяем доступность CLI
    result = subprocess.run(
        ["texteller", "--help"],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode == 0:
        ok("texteller CLI доступен")
        info("Модель скачается при первом реальном вызове")
        info("Используется как: texteller inference /path/to/formula.png")
    else:
        warn(f"texteller CLI вернул код {result.returncode}")
        warn(result.stderr[:200] if result.stderr else "нет stderr")

except FileNotFoundError:
    err("texteller CLI не найден в PATH")
    warn("Нужно пересобрать образ: docker compose build backend")
    warn("requirements.txt должен содержать: texteller")
except Exception as e:
    warn(f"TexTeller проверка: {e}")

# ── Итог ─────────────────────────────────────────────────────
print(f"\n{'='*60}\n  ИТОГ\n{'='*60}")
models_dir = Path("/app/models")
all_models = (
    list(models_dir.rglob("*.pt")) +
    list(models_dir.rglob("*.safetensors"))
)
for f in sorted(all_models):
    print(f"  📦 {str(f.relative_to(models_dir)):60s} {f.stat().st_size/1e6:>8.0f} МБ")

print()
ok("Скрипт завершён. Теперь: docker compose up -d")
print(f"{'='*60}\n")
