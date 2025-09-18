"""Helpers para criptografia de valores sensíveis usando Fernet.

A chave Fernet é derivada a partir de Config.SECRET_KEY (SHA-256 -> urlsafe base64)
de modo que a mesma SECRET_KEY gere a mesma chave Fernet entre reinícios.
"""
import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from mcp_anywhere.config import Config
from mcp_anywhere.logging_config import get_logger

logger = get_logger(__name__)


def _derive_fernet_key(secret: str | bytes) -> bytes:
    """Deriva uma chave compatível com Fernet a partir de `secret`.

    Processo:
    - Se recebe str, converte para bytes usando UTF-8
    - Calcula SHA-256 (32 bytes)
    - Faz base64.urlsafe_b64encode do digest (resultado aceito pelo Fernet)
    """
    if secret is None:
        raise ValueError("SECRET_KEY must be set to derive Fernet key")
    secret_bytes = secret.encode() if isinstance(secret, str) else secret
    digest = hashlib.sha256(secret_bytes).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_value(plaintext: Optional[str]) -> Optional[str]:
    """Encripta `plaintext` com Fernet e retorna o ciphertext como str UTF-8.

    Retorna None se plaintext for None.
    Levanta exceção em caso de erro (com logging).
    """
    if plaintext is None:
        return None
    try:
        key = _derive_fernet_key(Config.SECRET_KEY)
        f = Fernet(key)
        token = f.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")
    except Exception:
        logger.exception("Failed to encrypt value")
        raise


def decrypt_value(ciphertext: Optional[str]) -> Optional[str]:
    """Desencripta `ciphertext` (UTF-8 str) e retorna o plaintext.

    Retorna None se ciphertext for None.
    Se o token for inválido (ex.: SECRET_KEY diferente), lança ValueError.
    """
    if ciphertext is None:
        return None
    try:
        key = _derive_fernet_key(Config.SECRET_KEY)
        f = Fernet(key)
        plaintext = f.decrypt(ciphertext.encode("utf-8"))
        return plaintext.decode("utf-8")
    except InvalidToken:
        logger.exception("Invalid encryption token - decryption failed")
        raise ValueError("Invalid encrypted value or wrong SECRET_KEY")
    except Exception:
        logger.exception("Failed to decrypt value")
        raise