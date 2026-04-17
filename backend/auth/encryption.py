import os
from cryptography.fernet import Fernet, InvalidToken


def _get_fernet() -> Fernet:
    key = os.getenv("FERNET_KEY", "").encode()
    if not key:
        raise RuntimeError("FERNET_KEY не задан в переменных окружения")
    return Fernet(key)


def encrypt_api_key(plaintext: str) -> str:
    """Зашифровать API-ключ перед записью в БД."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    """Расшифровать API-ключ из БД."""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Не удалось расшифровать ключ — возможно, FERNET_KEY изменился")
