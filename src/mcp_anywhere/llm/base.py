"""Abstrações básicas para provedores LLM.

Responsabilidade:
- Definir a interface assíncrona que todos os provedores LLM devem implementar.
- Fornecer uma pequena dataclass de configuração do provedor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
import abc


@dataclass
class ProviderConfig:
    """Configuração leve para instanciar um provedor LLM.

    Campos:
    - provider_name: nome lógico do provedor (ex.: "anthropic", "openrouter")
    - model_name: nome do modelo a ser usado (ex.: "claude-sonnet-4", "openai/gpt-5")
    - api_key: chave de API (opcional quando o provider puder usar ENV/config global)
    """
    provider_name: str
    model_name: Optional[str] = None
    api_key: Optional[str] = None


class BaseLLMProvider(abc.ABC):
    """Interface assíncrona para provedores LLM.

    Implementações concretas devem fornecer o método `chat` que recebe uma lista
    de mensagens no formato OpenAI-like:
      [{"role": "system"|"user"|"assistant", "content": "..."}, ...]
    e retorna a resposta textual do modelo.
    """

    provider_name: str

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.provider_name = config.provider_name

    @abc.abstractmethod
    async def chat(self, messages: List[Dict[str, Any]], model: Optional[str]) -> str:
        """Enviar mensagens ao provedor e retornar o texto da resposta.

        Args:
            messages: Lista de mensagens no formato OpenAI-like.
            model: Nome do modelo a ser usado (pode ser None para implementar o default).

        Returns:
            Texto da resposta do modelo (string).
        """
        raise NotImplementedError