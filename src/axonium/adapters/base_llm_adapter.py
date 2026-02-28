from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List


class LLMAdapterType(Enum):
    LLAMA = "llama"  
    OPENAI = "openai"
    ANTHROPIC = "anthropic" 


class LLMOperation(Enum):
    CHAT = "chat"
    COMPLETIONS = "completions"
    EMBEDDINGS = "embeddings"
    HEALTH = "health"
    

class BaseLLMAdapter(ABC):
    """
    Contrato base para todos los adapters LLM del SDK.

    Un adapter:
    - encapsula un backend LLM
    - expone una API homogénea
    - no contiene lógica de decisión
    """

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, Any]],
        **kwargs,
    ) -> Any:
        """Chat-style completion (messages)."""
        raise NotImplementedError

    @abstractmethod
    async def async_chat(
        self,
        messages: List[Dict[str, Any]],
        **kwargs,
    ) -> Any:
        """Async chat-style completion (messages)."""
        raise NotImplementedError

    @abstractmethod
    def completions(
        self,
        prompt: str,
        **kwargs,
    ) -> Any:
        """Text completion (prompt)."""
        raise NotImplementedError

    @abstractmethod
    def embeddings(
        self,
        input: Any,
        **kwargs,
    ) -> Any:
        """Embeddings generation."""
        raise NotImplementedError

    @abstractmethod
    def health(self) -> Dict[str, Any]:
        """Health check del backend."""
        raise NotImplementedError
