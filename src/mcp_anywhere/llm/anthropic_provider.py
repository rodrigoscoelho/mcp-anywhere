"""Anthropic provider wrapper.

Responsabilidade:
- Encapsular o cliente `anthropic.Anthropic` usado atualmente pelo analisador,
  expondo uma interface assíncrona compatível com `BaseLLMProvider`.
- Converter mensagens OpenAI-like para a chamada que o cliente Anthropic espera.
- Não logar prompts ou chaves sensíveis.
"""

from __future__ import annotations

import asyncio
from typing import List, Dict, Any, Optional

from anthropic import Anthropic, AnthropicError
from anthropic.types import TextBlock

from mcp_anywhere.config import Config
from mcp_anywhere.logging_config import get_logger
from .base import BaseLLMProvider, ProviderConfig

logger = get_logger(__name__)


class AnthropicProvider(BaseLLMProvider):
    """Wrapper assíncrona para o cliente Anthropic.

    Observações:
    - Recebe mensagens no formato OpenAI-like: [{"role": "...", "content": "..."}]
    - Converte para `messages=[{"role": "user", "content": prompt}]` quando necessário,
      mantendo compatibilidade com o fluxo legado do projeto.
    """

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        api_key = config.api_key or Config.ANTHROPIC_API_KEY
        if not api_key:
            raise ValueError("AnthropicProvider requires an API key (config or ENV).")
        # Cliente síncrono — usaremos run_in_executor para chamadas de bloqueio
        self._client = Anthropic(api_key=api_key)

    async def chat(self, messages: List[Dict[str, Any]], model: Optional[str]) -> str:
        """Enviar mensagens ao Anthropic e retornar a resposta como texto.

        - messages: lista OpenAI-like; se for um único user message contendo todo o prompt,
          mantemos o conteúdo como no fluxo legado.
        - model: nome do modelo (pode ser None -> usa comportamento padrão do cliente).
        """
        # Não logar conteúdo das mensagens por segurança; apenas indicar que a chamada ocorreu.
        logger.debug("Calling AnthropicProvider.chat", extra={"model": model})

        # Adapter: se as mensagens representarem multi-turn, podemos concatenar para compatibilidade
        # com o cliente Anthropic usado hoje (que espera uma única mensagem de usuário).
        prompt_parts: List[str] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            # Preserve role markers lightly to keep context if multi-turn
            if role == "system":
                prompt_parts.append(f"[SYSTEM]\n{content}\n")
            elif role == "assistant":
                prompt_parts.append(f"[ASSISTANT]\n{content}\n")
            else:
                prompt_parts.append(content)

        prompt = "\n".join(part for part in prompt_parts if part).strip()

        loop = asyncio.get_event_loop()
        try:
            # Executa a chamada síncrona do cliente num thread pool para não bloquear o loop
            # Ensure we pass a concrete model string to the client (avoid Optional mismatch)
            if model is None and not self.config.model_name:
                raise ValueError("No model specified for AnthropicProvider.chat")
            chosen_model: str = model or self.config.model_name  # type: ignore[assignment]
            message = await loop.run_in_executor(
                None,
                lambda: self._client.messages.create(
                    model=chosen_model,
                    max_tokens=1024,
                    temperature=0.0,
                    messages=[{"role": "user", "content": prompt}],
                ),
            )
        except AnthropicError:
            logger.exception("Anthropic API error")
            raise

        # Extrai texto da primeira content block do retorno (compat com uso atual)
        content_blocks = getattr(message, "content", [])
        if not content_blocks:
            return ""

        for block in content_blocks:
            if isinstance(block, TextBlock):
                return block.text

        return str(content_blocks[0])
