"""Fábrica de provedores LLM.

Responsabilidade:
- Resolver o provedor efetivo e o nome do modelo com precedência DB > ENV
  (utilizando `mcp_anywhere.settings_store.get_effective_setting`).
- Instanciar a implementação concreta do provedor (Anthropic / OpenRouter) quando aplicável.
- Expor constantes PROVIDER_ANTHROPIC e PROVIDER_OPENROUTER.
"""

from __future__ import annotations

from typing import Optional, Tuple

from mcp_anywhere.config import Config
from mcp_anywhere.logging_config import get_logger
from mcp_anywhere.settings_store import get_effective_setting

from .base import ProviderConfig, BaseLLMProvider
from .anthropic_provider import AnthropicProvider
from .openrouter_provider import OpenRouterProvider
from mcp_anywhere.settings_store import get_app_setting

logger = get_logger(__name__)

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENROUTER = "openrouter"


async def get_provider_and_model() -> Tuple[Optional[BaseLLMProvider], Optional[str]]:
    """Resolve e retorne (provider_instance | None, resolved_model_name | None).

    Regras:
    - provider efetivo: get_effective_setting("llm.provider") (DB > ENV, com inferência)
    - model efetivo: get_effective_setting("llm.model") (respeita regras de provider no settings_store)
    - Se provider == "openrouter": instancia OpenRouterProvider usando llm.openrouter_api_key
    - Se provider == "anthropic": instancia AnthropicProvider usando llm.anthropic_api_key
    - Se provider for None:
      - Se Config.ANTHROPIC_API_KEY existir, retornamos (None, resolved_model) para preservar o
        fluxo legado (o chamador continuará usando o cliente Anthropic existente).
      - Caso contrário retornamos (None, None).
    """
    # Resolve provider & model via settings_store (DB > ENV)
    try:
        provider_name = await get_effective_setting("llm.provider")
        resolved_model = await get_effective_setting("llm.model")
    except Exception:
        logger.exception("Failed to resolve LLM settings from settings_store")
        # Fall back to environment-only inference
        provider_name = Config.LLM_PROVIDER or (PROVIDER_ANTHROPIC if Config.ANTHROPIC_API_KEY else None)
        resolved_model = Config.LLM_MODEL

    # Normalize provider name to lowercase if present
    if provider_name:
        provider_name = provider_name.lower().strip()

    # OpenRouter
    if provider_name == PROVIDER_OPENROUTER:
        api_key = await get_effective_setting("llm.openrouter_api_key")
        cfg = ProviderConfig(provider_name=PROVIDER_OPENROUTER, model_name=resolved_model, api_key=api_key)
        provider = OpenRouterProvider(cfg)
        logger.debug("Instantiated OpenRouterProvider", extra={"provider": PROVIDER_OPENROUTER, "model": resolved_model})
        return provider, resolved_model

    # Anthropic explicit
    if provider_name == PROVIDER_ANTHROPIC:
        # If provider was not explicitly set in DB or ENV (inferred from presence of ANTHROPIC_API_KEY),
        # preserve the legacy Anthropic path by returning (None, resolved_model) so callers
        # continue to use their existing Anthropic client. This avoids instantiating
        # AnthropicProvider unintentionally when no explicit configuration exists.
        try:
            db_provider = await get_app_setting("llm.provider")
        except Exception:
            db_provider = None

        if not db_provider and not Config.LLM_PROVIDER:
            logger.debug(
                "Anthropic provider inferred (not explicitly configured); preserving legacy Anthropic path",
                extra={"model": resolved_model},
            )
            return None, resolved_model

        api_key = await get_effective_setting("llm.anthropic_api_key")
        cfg = ProviderConfig(provider_name=PROVIDER_ANTHROPIC, model_name=resolved_model, api_key=api_key)
        provider = AnthropicProvider(cfg)
        logger.debug("Instantiated AnthropicProvider", extra={"provider": PROVIDER_ANTHROPIC, "model": resolved_model})
        return provider, resolved_model

    # No explicit provider in DB/ENV:
    # - If ANTHROPIC_API_KEY present in environment, prefer to keep legacy analyzer behavior.
    if Config.ANTHROPIC_API_KEY:
        logger.debug("No explicit LLM provider configured; preserving legacy Anthropic path")
        # Return None provider so analyzer keeps using its existing Anthropic client.
        return None, resolved_model

    # Nothing configured
    logger.debug("No LLM provider or API key configured")
    return None, resolved_model