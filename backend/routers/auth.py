import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr, field_validator

from database.engine import get_db
from database.crud.users import get_user_by_email, get_user_by_username, create_user, get_user_by_id
from database.models import RefreshToken
from auth.password import hash_password, verify_password, validate_password_strength
from auth.jwt_handler import (
    create_access_token, create_refresh_token,
    decode_refresh_token, ACCESS_TOKEN_EXPIRE_MINUTES
)
from auth.dependencies import hash_token
from limiter import limiter

logger = logging.getLogger("prms.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


# --- Pydantic schemas ---

class RegisterRequest(BaseModel):
    email: EmailStr
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Username минимум 3 символа")
        if len(v) > 64:
            raise ValueError("Username максимум 64 символа")
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username может содержать только буквы, цифры, _ и -")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TOKEN_EXPIRE_MINUTES * 60


class RefreshRequest(BaseModel):
    refresh_token: str


class MessageResponse(BaseModel):
    message: str


# --- Endpoints ---

@router.post("/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/hour")
async def register(request: Request, body: RegisterRequest, session: AsyncSession = Depends(get_db)):
    """Регистрация нового пользователя."""
    err = validate_password_strength(body.password)
    if err:
        raise HTTPException(status_code=400, detail=err)

    if await get_user_by_email(session, body.email):
        raise HTTPException(status_code=409, detail="Email уже зарегистрирован")
    if await get_user_by_username(session, body.username):
        raise HTTPException(status_code=409, detail="Username уже занят")

    pwd_hash = hash_password(body.password)
    user = await create_user(session, email=body.email, username=body.username, password_hash=pwd_hash)
    logger.info(f"Новый пользователь: {user.username} ({user.email})")
    return {"message": f"Пользователь {user.username} успешно зарегистрирован"}


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, session: AsyncSession = Depends(get_db)):
    """Вход по email + пароль. Возвращает access + refresh токены."""
    user = await get_user_by_email(session, body.email)
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Аккаунт деактивирован")

    access_token = create_access_token(str(user.id), user.email)
    refresh_token_str, expires_at = create_refresh_token(str(user.id))

    token_hash = hash_token(refresh_token_str)
    db_token = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    session.add(db_token)

    logger.info(f"Login: {user.email}")
    return TokenResponse(access_token=access_token, refresh_token=refresh_token_str)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, session: AsyncSession = Depends(get_db)):
    """Обновить access token по refresh token."""
    payload = decode_refresh_token(body.refresh_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Невалидный или истёкший refresh token")

    token_hash = hash_token(body.refresh_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    db_token = result.scalar_one_or_none()
    if not db_token or db_token.revoked:
        raise HTTPException(status_code=401, detail="Refresh token отозван или не найден")
    if db_token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token истёк")

    user = await get_user_by_id(session, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Пользователь не найден")

    # Ротация: отозвать старый, выдать новый
    db_token.revoked = True
    new_access = create_access_token(str(user.id), user.email)
    new_refresh, new_expires = create_refresh_token(str(user.id))
    new_db_token = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(new_refresh),
        expires_at=new_expires,
    )
    session.add(new_db_token)
    return TokenResponse(access_token=new_access, refresh_token=new_refresh)


@router.post("/logout", response_model=MessageResponse)
async def logout(body: RefreshRequest, session: AsyncSession = Depends(get_db)):
    """Отозвать refresh token (logout)."""
    token_hash = hash_token(body.refresh_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    db_token = result.scalar_one_or_none()
    if db_token:
        db_token.revoked = True
    return {"message": "Выход выполнен успешно"}
