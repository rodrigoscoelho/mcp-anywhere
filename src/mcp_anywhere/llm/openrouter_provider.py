"""OpenRouter provider implementation.

Responsabilidade:
- Implementar um wrapper assíncrono compatível com BaseLLMProvider que chama o
  endpoint OpenRouter (OpenAI-like) em https://openrouter.ai/api/v1/chat/completions.
- Usar httpx.AsyncClient com timeout e tratar erros HTTP de maneira clara.
- Não logar prompts ou chaves sensíveis.
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional

import httpx

from mcp_anywhere.config import Config
from mcp_anywhere.logging_config import get_logger
from .base import BaseLLMProvider, ProviderConfig

logger = get_logger(__name__)

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT = 15.0  # segundos


class OpenRouterProvider(BaseLLMProvider):
    """Provider para OpenRouter (estilo OpenAI).

    Aceita mensagens no formato OpenAI-like:
      [{"role": "system"|"user"|"assistant", "content": "..."}]

    Retorna o texto do primeiro choice: choices[0].message.content (normalizado).
    """

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self.api_key = config.api_key or Config.OPENROUTER_API_KEY
        if not self.api_key:
            raise ValueError("OpenRouterProvider requires an API key (config or ENV).")

    async def chat(self, messages: List[Dict[str, Any]], model: Optional[str]) -> str:
        """Enviar mensagens ao OpenRouter e retornar o texto da primeira escolha.

        Args:
            messages: Lista OpenAI-like de mensagens.
            model: Nome do modelo a ser usado. Se None, usa config.model_name.

        Returns:
            Texto retornado pelo modelo (string).

        Raises:
            httpx.HTTPError em caso de falha de rede/HTTP.
            ValueError se a resposta não contiver o campo esperado.
        """
        resolved_model = model or self.config.model_name
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {"model": resolved_model, "messages": messages}

        # Não logar mensagens nem a chave; apenas logar o provedor/model resolvido
        logger.debug("Calling OpenRouterProvider.chat", extra={"model": resolved_model})

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                resp = await client.post(OPENROUTER_ENDPOINT, json=payload, headers=headers)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                # Log sem expor corpo (que pode conter dados sensíveis)
                logger.exception("OpenRouter HTTP error when calling chat endpoint")
                raise
            except httpx.RequestError as e:
                logger.exception("OpenRouter request failed")
                raise

        data = resp.json()
        # Expecting OpenAI-like response: {"choices": [{"message": {"content": "..."}}], ...}
        try:
            choices = data.get("choices", [])
            if not choices or not choices[0]:
                raise ValueError("OpenRouter response has no choices")
            message_obj = choices[0].get("message") or choices[0].get("text") or choices[0]
            # message_obj may be a dict with 'content' or a plain string
            if isinstance(message_obj, dict):
                content = message_obj.get("content") or message_obj.get("text")
            else:
                content = str(message_obj)
            if content is None:
                raise ValueError("OpenRouter response choice missing text content")
            return str(content)
        except (KeyError, IndexError, ValueError) as e:
            logger.exception("Failed to parse OpenRouter response")
            raise