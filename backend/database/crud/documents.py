from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from database.models import Document


async def get_document_by_docid(session: AsyncSession, docid: str) -> Document | None:
    result = await session.execute(select(Document).where(Document.docid == docid))
    return result.scalar_one_or_none()


async def get_user_documents(session: AsyncSession, user_id) -> list[Document]:
    result = await session.execute(
        select(Document).where(Document.user_id == user_id).order_by(Document.created_at.desc())
    )
    return result.scalars().all()


async def create_document(session: AsyncSession, docid: str, user_id, filename: str, file_size_bytes: int = 0) -> Document:
    doc = Document(docid=docid, user_id=user_id, filename=filename, file_size_bytes=file_size_bytes)
    session.add(doc)
    await session.flush()
    await session.refresh(doc)
    return doc


async def update_document_status(session: AsyncSession, docid: str, status: str, page_count: int = None) -> Document | None:
    doc = await get_document_by_docid(session, docid)
    if doc:
        doc.status = status
        if page_count is not None:
            doc.page_count = page_count
        await session.flush()
    return doc


async def delete_document(session: AsyncSession, docid: str) -> bool:
    doc = await get_document_by_docid(session, docid)
    if doc:
        await session.delete(doc)
        await session.flush()
        return True
    return False


async def get_user_storage_used(session: AsyncSession, user_id) -> int:
    """Вернуть суммарный размер файлов пользователя в байтах."""
    result = await session.execute(
        select(func.sum(Document.file_size_bytes)).where(Document.user_id == user_id)
    )
    return result.scalar_one() or 0
