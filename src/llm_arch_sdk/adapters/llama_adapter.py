import os
import logging
from typing import Any, Dict, List

from langfuse import observe

from .base_llm_adapter import BaseLLMAdapter
from ..client.llm_client import LlmClient
from ..transport.auth_http_client_factory import AuthHttpClientFactory
from ..config.settings import _sdk_settings

logger = logging.getLogger("llm.sdk.adapters.llama")


class LlamaAdapter(BaseLLMAdapter):
    """
    Adapter para llama-server (OpenAI-compatible API).
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 60.0,
        **client_kwargs,
    ):
        self.base_url = base_url or _sdk_settings.llm.base_url
        self.timeout = timeout or _sdk_settings.transport.timeout_seconds
        self.client_kwargs = client_kwargs

        self._validate_config()

        self._http_client = AuthHttpClientFactory.create(timeout=self.timeout)
        self._client = LlmClient(
            base_url=self.base_url,
            http_client=self._http_client,
            **self.client_kwargs
        )

    # -------------------------
    # API
    # -------------------------
    
    def client(self) -> LlmClient:
        """
        Devuelve un cliente minimalista para inferencia con llama-server
        completamente configurada.
        """
        return self._client

    @observe(name="adapter.llama.chat")
    def chat(self, model: str, messages: List[Dict[str, Any]], **kwargs):
        return self._client.chat.create(
            model=model,
            messages=messages,
            **kwargs,
        )

    @observe(name="adapter.llama.completions")
    def completions(self, model: str, prompt: str, **kwargs):
        return self._client.completions.create(
            model=model,
            prompt=prompt,
            **kwargs,
        )

    @observe(name="adapter.llama.embeddings")
    def embeddings(self, model: str, input: Any, **kwargs):
        return self._client.embeddings.create(
            model=model,
            input=input,
            **kwargs,
        )

    @observe(name="adapter.llama.health", capture_input=False, capture_output=False)
    def health(self) -> Dict[str, Any]:
        return self._client.health()
    
    def _validate_config(self):
        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL no configurada")
