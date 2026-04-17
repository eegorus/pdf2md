"""
Скрипт для привязки существующих документов (без user_id) к admin-пользователю.
Запускать ОДИН РАЗ после деплоя.
Использование: docker compose exec backend python /app/scripts/migrate_existing_docs.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from database.engine import AsyncSessionFactory
from database.models import User, Document
from database.crud.users import get_user_by_email, create_user
from database.crud.documents import get_document_by_docid, create_document
from auth.password import hash_password

DATADIR = Path(os.getenv("DATA_DIR", "/app/data"))


async def main():
    async with AsyncSessionFactory() as session:
        # Найти или создать admin
        admin = await get_user_by_email(session, "admin@prms.local")
        if not admin:
            admin = await create_user(
                session,
                email="admin@prms.local",
                username="admin",
                password_hash=hash_password("Admin1234"),
            )
            admin.is_admin = True
            await session.flush()
            print(f"Admin создан: {admin.email}")
        else:
            print(f"Admin найден: {admin.email}")

        # Пройти по всем существующим docid
        uploads_dir = DATADIR / "uploads"
        if not uploads_dir.exists():
            print("Нет директории uploads — нечего мигрировать")
            return

        migrated = 0
        skipped = 0
        for docid_dir in uploads_dir.iterdir():
            if not docid_dir.is_dir():
                continue
            docid = docid_dir.name
            meta_path = docid_dir / "meta.json"
            if not meta_path.exists():
                continue

            # Проверить: уже есть в БД?
            existing = await get_document_by_docid(session, docid)
            if existing:
                skipped += 1
                continue

            meta = json.loads(meta_path.read_text())
            filename = meta.get("filename", f"{docid}.pdf")
            pdf_path = docid_dir / "original.pdf"
            file_size = pdf_path.stat().st_size if pdf_path.exists() else 0

            await create_document(
                session,
                docid=docid,
                user_id=admin.id,
                filename=filename,
                file_size_bytes=file_size,
            )
            migrated += 1
            print(f"  Мигрирован: {docid} ({filename})")

        await session.commit()
        print(f"\nГотово: мигрировано {migrated}, пропущено {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
