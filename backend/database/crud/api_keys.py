from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.models import UserApiKey


async def get_user_api_keys(session: AsyncSession, user_id) -> list[UserApiKey]:
    result = await session.execute(
        select(UserApiKey).where(UserApiKey.user_id == user_id)
    )
    return result.scalars().all()


async def get_user_api_key(session: AsyncSession, user_id, provider: str) -> UserApiKey | None:
    result = await session.execute(
        select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == provider)
    )
    return result.scalar_one_or_none()


async def upsert_api_key(session: AsyncSession, user_id, provider: str, encrypted_key: str) -> UserApiKey:
    key = await get_user_api_key(session, user_id, provider)
    if key:
        key.encrypted_key = encrypted_key
    else:
        key = UserApiKey(user_id=user_id, provider=provider, encrypted_key=encrypted_key)
        session.add(key)
    await session.flush()
    await session.refresh(key)
    return key


async def delete_api_key(session: AsyncSession, user_id, provider: str) -> bool:
    key = await get_user_api_key(session, user_id, provider)
    if key:
        await session.delete(key)
        await session.flush()
        return True
    return False
