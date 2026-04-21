import logging
from pathlib import Path
import os
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr
from database.engine import get_db
from database.models import User
from database.crud.users import get_user_by_email, get_user_by_username
from database.crud.documents import get_user_documents, get_user_storage_used
from database.crud.api_keys import get_user_api_keys, upsert_api_key, delete_api_key, get_user_api_key
from auth.dependencies import get_current_user
from auth.password import verify_password, hash_password, validate_password_strength
from auth.encryption import encrypt_api_key, decrypt_api_key

logger = logging.getLogger("prms.users")
router = APIRouter(prefix="/users", tags=["users"])

DATADIR = Path(os.getenv("DATADIR", "/app/data"))

SUPPORTED_PROVIDERS = {"openrouter", "llamaparse", "openai", "anthropic"}


class UserProfile(BaseModel):
    id: str
    email: str
    username: str
    is_admin: bool
    storage_quota_mb: int
    created_at: str


class UpdateProfileRequest(BaseModel):
    username: str | None = None
    email: EmailStr | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ApiKeyRequest(BaseModel):
    key: str


class ApiKeyInfo(BaseModel):
    provider: str
    is_set: bool


class StorageStats(BaseModel):
    used_bytes: int
    used_mb: float
    quota_mb: int
    quota_bytes: int
    usage_percent: float
    document_count: int


@router.get("/me", response_model=UserProfile)
async def get_profile(current_user: User = Depends(get_current_user)):
    return UserProfile(
        id=str(current_user.id),
        email=current_user.email,
        username=current_user.username,
        is_admin=current_user.is_admin,
        storage_quota_mb=current_user.storage_quota_mb,
        created_at=current_user.created_at.isoformat(),
    )


@router.patch("/me", response_model=UserProfile)
async def update_profile(
    body: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    if body.username and body.username != current_user.username:
        existing = await get_user_by_username(session, body.username)
        if existing:
            raise HTTPException(409, "Username уже занят")
        current_user.username = body.username

    if body.email and body.email != current_user.email:
        existing = await get_user_by_email(session, body.email)
        if existing:
            raise HTTPException(409, "Email уже используется")
        current_user.email = body.email

    await session.flush()
    return UserProfile(
        id=str(current_user.id),
        email=current_user.email,
        username=current_user.username,
        is_admin=current_user.is_admin,
        storage_quota_mb=current_user.storage_quota_mb,
        created_at=current_user.created_at.isoformat(),
    )


@router.post("/me/change-password", response_model=dict)
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(400, "Текущий пароль неверен")
    err = validate_password_strength(body.new_password)
    if err:
        raise HTTPException(400, err)
    current_user.password_hash = hash_password(body.new_password)
    await session.flush()
    logger.info(f"Пароль изменён: {current_user.email}")
    return {"message": "Пароль успешно изменён"}


@router.get("/me/stats", response_model=StorageStats)
async def get_stats(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    docs = await get_user_documents(session, current_user.id)
    used_bytes = await get_user_storage_used(session, current_user.id)
    quota_bytes = current_user.storage_quota_mb * 1024 * 1024
    usage_percent = round(used_bytes / quota_bytes * 100, 1) if quota_bytes > 0 else 0
    return StorageStats(
        used_bytes=used_bytes,
        used_mb=round(used_bytes / (1024 * 1024), 2),
        quota_mb=current_user.storage_quota_mb,
        quota_bytes=quota_bytes,
        usage_percent=usage_percent,
        document_count=len(docs),
    )


@router.get("/me/api-keys", response_model=list[ApiKeyInfo])
async def list_api_keys(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    user_keys = await get_user_api_keys(session, current_user.id)
    set_providers = {k.provider for k in user_keys}
    return [
        ApiKeyInfo(provider=p, is_set=(p in set_providers))
        for p in sorted(SUPPORTED_PROVIDERS)
    ]


@router.put("/me/api-keys/{provider}", response_model=dict)
async def set_api_key(
    provider: str,
    body: ApiKeyRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(400, f"Провайдер '{provider}' не поддерживается. Допустимые: {SUPPORTED_PROVIDERS}")
    if not body.key.strip():
        raise HTTPException(400, "Ключ не может быть пустым")
    encrypted = encrypt_api_key(body.key.strip())
    await upsert_api_key(session, current_user.id, provider, encrypted)
    logger.info(f"API key set: user={current_user.email}, provider={provider}")
    return {"message": f"Ключ для {provider} сохранён"}


@router.delete("/me/api-keys/{provider}", response_model=dict)
async def remove_api_key(
    provider: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    deleted = await delete_api_key(session, current_user.id, provider)
    if not deleted:
        raise HTTPException(404, f"Ключ для {provider} не найден")
    return {"message": f"Ключ для {provider} удалён"}


@router.get("/me/api-keys/{provider}/value", response_model=dict)
async def get_api_key_value(
    provider: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    key = await get_user_api_key(session, current_user.id, provider)
    if not key:
        raise HTTPException(404, f"Ключ для {provider} не задан")
    return {"provider": provider, "key": decrypt_api_key(key.encrypted_key)}
