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
