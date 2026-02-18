import os
import logging
from typing import Any, Dict, List

from langfuse import observe

from .base import BaseLLMAdapter
from ..client.llm_client import LlmClient
from ..transport.auth_http_client_factory import AuthHttpClientFactory

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
        self.base_url = base_url or os.getenv("LLM_BASE_URL")
        self.timeout = timeout
        self.client_kwargs = client_kwargs

        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL no configurada")

        self._http_client = AuthHttpClientFactory.create(timeout=self.timeout)
        self._client = LlmClient(
            base_url=self.base_url,
            http_client=self._http_client,
        )

    # -------------------------
    # API
    # -------------------------

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
