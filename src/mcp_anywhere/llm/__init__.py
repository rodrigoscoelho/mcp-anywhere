"""Pacote de provedores LLM.

Este pacote agrupa abstrações e implementações de provedores LLM (Anthropic, OpenRouter)
e a fábrica utilizada para resolver o provedor e modelo efetivos com precedência
DB > ENV.
"""
from .base import BaseLLMProvider, ProviderConfig
from .factory import get_provider_and_model, PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER
from .anthropic_provider import AnthropicProvider
from .openrouter_provider import OpenRouterProvider

__all__ = [
    "BaseLLMProvider",
    "ProviderConfig",
    "get_provider_and_model",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_OPENROUTER",
    "AnthropicProvider",
    "OpenRouterProvider",
]