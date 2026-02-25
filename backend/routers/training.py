"""
Роутер: управление обучением
- GET  /training/pairs           — список пар для обучения
- POST /training/pairs           — добавить пару
- GET  /training/stats           — статистика датасета
- POST /training/switch-model    — переключить версию модели
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/pairs", summary="Список training pairs")
async def list_pairs():
    # TODO: Шаг 11
    return {"pairs": [], "total": 0}


@router.get("/stats", summary="Статистика датасета")
async def training_stats():
    # TODO: Шаг 11
    return {"total_pairs": 0, "by_type": {}}


@router.post("/switch-model", summary="Переключить версию модели")
async def switch_model(version: str):
    # TODO: Шаг 12
    return {"message": f"Switch to {version} not implemented yet"}
