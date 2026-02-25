"""
Роутер: управление документами
- POST /documents/upload    — загрузка PDF
- GET  /documents/          — список документов
- GET  /documents/{doc_id}  — инфо о документе
- DELETE /documents/{doc_id} — удаление
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/", summary="Список документов")
async def list_documents():
    # TODO: Шаг 7
    return {"documents": [], "message": "Not implemented yet"}


@router.post("/upload", summary="Загрузить PDF")
async def upload_document():
    # TODO: Шаг 7
    return {"message": "Not implemented yet"}


@router.get("/{doc_id}", summary="Информация о документе")
async def get_document(doc_id: str):
    # TODO: Шаг 7
    return {"doc_id": doc_id, "message": "Not implemented yet"}
