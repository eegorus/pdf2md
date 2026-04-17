import hashlib
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from auth.jwt_handler import decode_access_token
from database.engine import get_db
from database.crud.users import get_user_by_id
from database.crud.documents import get_document_by_docid
from database.models import User, Document

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=True)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI Depends: извлечь пользователя из JWT access token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Токен недействителен или истёк",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)
    if not payload:
        raise credentials_exception
    user = await get_user_by_id(session, payload["sub"])
    if not user or not user.is_active:
        raise credentials_exception
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Alias для get_current_user (для явности в роутерах)."""
    return current_user


async def verify_document_ownership(
    doc_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Document:
    """
    Проверить что doc_id существует и принадлежит current_user.
    Admin может видеть любой документ.
    """
    doc = await get_document_by_docid(session, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")
    if str(doc.user_id) != str(current_user.id) and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    return doc


def hash_token(token: str) -> str:
    """SHA-256 хеш для хранения refresh token в БД (не храним plaintext)."""
    return hashlib.sha256(token.encode()).hexdigest()
