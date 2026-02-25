"""
Роутер: обработка документов (pipeline)
- POST /processing/{doc_id}/start   — запустить pipeline
- GET  /processing/{doc_id}/status  — статус обработки
- GET  /processing/{doc_id}/results — результаты
"""
from fastapi import APIRouter

router = APIRouter()


@router.post("/{doc_id}/start", summary="Запустить обработку PDF")
async def start_processing(doc_id: str):
    # TODO: Шаг 7-8
    return {"doc_id": doc_id, "message": "Not implemented yet"}


@router.get("/{doc_id}/status", summary="Статус обработки")
async def processing_status(doc_id: str):
    # TODO: Шаг 7-8
    return {"doc_id": doc_id, "status": "unknown"}


@router.get("/{doc_id}/results", summary="Результаты обработки")
async def get_results(doc_id: str):
    # TODO: Шаг 8
    return {"doc_id": doc_id, "blocks": []}
