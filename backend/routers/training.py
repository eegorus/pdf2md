import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

router = APIRouter(tags=["Training"])

DATA_DIR = Path("/app/data")
PAIRS_FILE  = DATA_DIR / "training" / "pairs.jsonl"
STATS_FILE  = DATA_DIR / "training" / "stats.json"
MODELS_DIR  = DATA_DIR / "models" / "versions"

PAIRS_FILE.parent.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)


def _read_pairs() -> list[dict]:
    if not PAIRS_FILE.exists():
        return []
    pairs = []
    with open(PAIRS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    pairs.append(json.loads(line))
                except Exception:
                    pass
    return pairs


def _write_pair(pair: dict):
    with open(PAIRS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(pair, ensure_ascii=False) + "\n")
    _update_stats()


def _update_stats():
    pairs = _read_pairs()
    from collections import Counter
    by_type = dict(Counter(p.get("block_type") for p in pairs))
    stats = {
        "total_pairs":         len(pairs),
        "by_type":             by_type,
        "min_pairs_for_finetune": 50,
        "ready_for_finetune":  len(pairs) >= 50,
        "last_updated":        datetime.utcnow().isoformat(),
    }
    STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# GET /training/pairs
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/pairs", summary="Список training pairs")
async def list_pairs(
    block_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    pairs = _read_pairs()
    if block_type:
        pairs = [p for p in pairs if p.get("block_type") == block_type]
    total = len(pairs)
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "pairs": pairs[offset : offset + limit],
    }


# ──────────────────────────────────────────────────────────────────────────────
# POST /training/pairs  — создать пару
# ──────────────────────────────────────────────────────────────────────────────
@router.post("/pairs", summary="Создать training pair")
async def create_pair(payload: dict = Body(...)):
    required = ("block_id", "doc_id", "block_type", "local_model_output", "target_output")
    missing = [f for f in required if not payload.get(f)]
    if missing:
        raise HTTPException(status_code=422, detail=f"Обязательные поля: {missing}")

    # Проверка: local_model_output и target_output должны отличаться
    if payload["local_model_output"].strip() == payload["target_output"].strip():
        raise HTTPException(
            status_code=400,
            detail="local_model_output и target_output одинаковые — пара не имеет смысла",
        )

    pair_id = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
    pair = {
        "pair_id":             pair_id,
        "created_at":          datetime.utcnow().isoformat(),
        "block_id":            payload["block_id"],
        "doc_id":              payload["doc_id"],
        "block_type":          payload["block_type"],
        "source_page":         payload.get("source_page"),
        "bbox":                payload.get("bbox"),
        "image_path":          payload.get("image_path"),
        "local_model_output":  payload["local_model_output"],
        "target_output":       payload["target_output"],
        "quality_assessment":  payload.get("quality_assessment", {}),
    }
    _write_pair(pair)
    return {"pair_id": pair_id, "created": True, "total_pairs": len(_read_pairs())}


# ──────────────────────────────────────────────────────────────────────────────
# GET /training/stats
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/stats", summary="Статистика датасета")
async def get_stats():
    if STATS_FILE.exists():
        try:
            return json.loads(STATS_FILE.read_text())
        except Exception:
            pass
    return _update_stats()


# ──────────────────────────────────────────────────────────────────────────────
# POST /training/switch-model
# ──────────────────────────────────────────────────────────────────────────────
@router.post("/switch-model", summary="Переключить версию модели")
async def switch_model(payload: dict = Body(...)):
    version = payload.get("version", "").strip()
    if not version:
        raise HTTPException(status_code=422, detail="Укажи version, например 'v002'")

    version_dir = MODELS_DIR / version
    if not version_dir.exists():
        raise HTTPException(status_code=404, detail=f"Версия {version} не найдена в {MODELS_DIR}")

    active_file = DATA_DIR / "models" / "active_version.json"
    active_file.parent.mkdir(parents=True, exist_ok=True)
    active_file.write_text(json.dumps({"version": version, "switched_at": datetime.utcnow().isoformat()}))

    return {"version": version, "active": True}


# ──────────────────────────────────────────────────────────────────────────────
# POST /training/start  — запуск fine-tuning в фоне
# ──────────────────────────────────────────────────────────────────────────────
import subprocess, threading

_finetune_state = {"status": "idle", "version": None, "log": "", "pid": None}


def _run_finetune(version: str, epochs: int, data_path: str):
    global _finetune_state
    _finetune_state.update({"status": "running", "log": "Запуск контейнера finetune...\n"})
    try:
        cmd = [
            "docker", "compose", "--profile", "finetune",
            "run", "--rm", "finetune",
            "python", "/workspace/train.py",
            "--model_id", "Qwen/Qwen2.5-VL-7B-Instruct",
            "--data_path", data_path,
            "--output_dir", f"/workspace/models/versions/{version}",
            "--num_train_epochs", str(epochs),
            "--use_qlora", "true",
        ]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd="/app"
        )
        _finetune_state["pid"] = proc.pid
        log_lines = []
        for line in proc.stdout:
            log_lines.append(line)
            _finetune_state["log"] = "".join(log_lines[-50:])  # последние 50 строк
        proc.wait()
        if proc.returncode == 0:
            _finetune_state.update({"status": "done", "version": version})
            # Автоматически переключаем на новую версию
            active = DATA_DIR / "models" / "active_version.json"
            active.parent.mkdir(parents=True, exist_ok=True)
            active.write_text(json.dumps({"version": version, "switched_at": datetime.utcnow().isoformat()}))
        else:
            _finetune_state["status"] = "error"
    except Exception as e:
        _finetune_state.update({"status": "error", "log": str(e)})


@router.post("/start", summary="Запустить fine-tuning")
async def start_finetune(payload: dict = Body(...)):
    global _finetune_state
    if _finetune_state["status"] == "running":
        raise HTTPException(status_code=409, detail="Fine-tuning уже запущен")

    pairs = _read_pairs()
    min_pairs = 10  # минимум для запуска (50 для продакшена)
    if len(pairs) < min_pairs:
        raise HTTPException(
            status_code=400,
            detail=f"Нужно минимум {min_pairs} пар, сейчас: {len(pairs)}"
        )

    version = payload.get("version") or f"v{datetime.utcnow().strftime('%Y%m%d_%H%M')}"
    epochs  = int(payload.get("epochs", 3))

    # Строим датасет
    dataset_path = DATA_DIR / "training" / "train_dataset.json"
    dataset = []
    for p in pairs:
        dataset.append({
            "messages": [
                {"role": "system",  "content": "Ты — OCR-движок для извлечения содержимого блоков из PDF-отчётов."},
                {"role": "user",    "content": [
                    {"type": "image", "image": p.get("image_path", "")},
                    {"type": "text",  "text": f"Извлеки содержимое блока типа '{p.get('block_type','text')}'"},
                ]},
                {"role": "assistant", "content": p["target_output"]},
            ]
        })
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2))

    _finetune_state = {"status": "starting", "version": version, "log": "", "pid": None}
    thread = threading.Thread(
        target=_run_finetune,
        args=(version, epochs, str(dataset_path)),
        daemon=True,
    )
    thread.start()

    return {"status": "starting", "version": version, "pairs_count": len(pairs), "epochs": epochs}


@router.get("/status", summary="Статус fine-tuning")
async def finetune_status():
    versions = []
    if MODELS_DIR.exists():
        versions = sorted([d.name for d in MODELS_DIR.iterdir() if d.is_dir()])
    active_file = DATA_DIR / "models" / "active_version.json"
    active = None
    if active_file.exists():
        try:
            active = json.loads(active_file.read_text()).get("version")
        except Exception:
            pass
    return {
        **_finetune_state,
        "versions":       versions,
        "active_version": active,
    }
