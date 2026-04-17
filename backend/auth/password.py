from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Хешировать пароль bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверить пароль против bcrypt-хеша."""
    return pwd_context.verify(plain_password, hashed_password)


def validate_password_strength(password: str) -> str | None:
    """
    Вернуть сообщение об ошибке или None если пароль OK.
    Правила: минимум 8 символов, хотя бы 1 буква и 1 цифра.
    """
    if len(password) < 8:
        return "Пароль должен содержать минимум 8 символов"
    if not any(c.isalpha() for c in password):
        return "Пароль должен содержать хотя бы одну букву"
    if not any(c.isdigit() for c in password):
        return "Пароль должен содержать хотя бы одну цифру"
    return None
