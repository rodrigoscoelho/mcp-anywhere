"""Persistência e resolvedor de configurações globais (DB > ENV).

Fornece:
- async set_app_setting(key, value, *, encrypt=False)
- async get_app_setting(key) -> tuple(value, encrypted) | None
- async get_effective_setting(key) -> str | None
"""
from typing import Optional, Tuple

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_anywhere.config import Config
from mcp_anywhere.crypto_utils import decrypt_value, encrypt_value
from mcp_anywhere.database import AppSetting, get_async_session
from mcp_anywhere.logging_config import get_logger

logger = get_logger(__name__)


async def set_app_setting(key: str, value: Optional[str], *, encrypt: bool = False) -> None:
    """Salva/atualiza uma configuração global no DB.

    - Se encrypt=True, o valor será criptografado antes de salvar (e `encrypted` marcado True).
    - Se value is None, a chave será removida do DB (se existir).
    """
    async with get_async_session() as session:  # type: AsyncSession
        async with session.begin():
            stmt = select(AppSetting).where(AppSetting.key == key)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()

            if value is None:
                # Remove entry if exists
                if row:
                    await session.execute(delete(AppSetting).where(AppSetting.key == key))
                    logger.info("Removed app setting %s from DB", key)
                return

            store_value = encrypt_value(value) if encrypt else value
            encrypted_flag = bool(encrypt)

            if row:
                # Update
                row.value = store_value
                row.encrypted = encrypted_flag
                # SQLAlchemy will update updated_at via onupdate when committing
                session.add(row)
                logger.info("Updated app setting %s (encrypted=%s)", key, encrypted_flag)
            else:
                # Insert new
                new = AppSetting(key=key, value=store_value, encrypted=encrypted_flag)
                session.add(new)
                logger.info("Inserted app setting %s (encrypted=%s)", key, encrypted_flag)


async def get_app_setting(key: str) -> Optional[Tuple[Optional[str], bool]]:
    """Retorna o valor cru armazenado no DB (value, encrypted) ou None se inexistir.

    OBS: Se encrypted==True o `value` retornado será o ciphertext tal como armazenado.
    """
    async with get_async_session() as session:  # type: AsyncSession
        stmt = select(AppSetting).where(AppSetting.key == key)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if not row:
            return None
        return (row.value, bool(row.encrypted))


async def get_effective_setting(key: str) -> Optional[str]:
    """Resolve configuração em runtime com precedência DB > ENV.

    Regras por chave suportada:
    - "llm.provider": ENV LLM_PROVIDER; se não existir, inferir "anthropic" se ANTHROPIC_API_KEY estiver setado.
    - "llm.model": se provider for "anthropic" considerar ANTHROPIC_MODEL_NAME antes de LLM_MODEL.
    - "llm.openrouter_api_key": ENV OPENROUTER_API_KEY (sensitive)
    - "llm.anthropic_api_key": ENV ANTHROPIC_API_KEY

    Se o valor existir no DB:
    - Se encrypted==True: decriptar antes de retornar
    - Retornar o valor persistido (mesmo se vazio explícito)
    """
    # 1) Check DB first
    db_row = await get_app_setting(key)
    if db_row is not None:
        value, encrypted = db_row
        if encrypted and value is not None:
            try:
                return decrypt_value(value)
            except Exception:
                logger.exception("Failed to decrypt app setting %s", key)
                return None
        return value

    # 2) Fallback to ENV based on mapping rules
    if key == "llm.provider":
        # ENV override
        if Config.LLM_PROVIDER:
            return Config.LLM_PROVIDER
        # Infer anthropic if Anthropic key present
        if Config.ANTHROPIC_API_KEY:
            return "anthropic"
        return None

    if key == "llm.model":
        # Need to determine provider preference:
        # - Prefer DB provider if present (without using get_effective_setting to avoid recursion)
        provider_db = await get_app_setting("llm.provider")
        provider = None
        if provider_db:
            prov_val, prov_encrypted = provider_db
            if prov_val is not None:
                if prov_encrypted:
                    try:
                        provider = decrypt_value(prov_val)
                    except Exception:
                        logger.exception("Failed to decrypt llm.provider from DB")
                        provider = None
                else:
                    provider = prov_val

        # If no DB provider, fall back to ENV / inference
        if not provider:
            provider = Config.LLM_PROVIDER or ( "anthropic" if Config.ANTHROPIC_API_KEY else None)

        # If Anthropic provider, try ANTHROPIC_MODEL_NAME before LLM_MODEL
        if provider == "anthropic":
            if Config.ANTHROPIC_MODEL_NAME:
                return Config.ANTHROPIC_MODEL_NAME
            return Config.LLM_MODEL
        # Other providers: return LLM_MODEL (may be None)
        return Config.LLM_MODEL

    if key == "llm.openrouter_api_key":
        return Config.OPENROUTER_API_KEY

    if key == "llm.anthropic_api_key":
        return Config.ANTHROPIC_API_KEY

    if key == "mcp.disable_auth":
        env_value = "true" if Config.MCP_DISABLE_AUTH else "false"
        return env_value

    # Unknown key - no fallback
    return None
