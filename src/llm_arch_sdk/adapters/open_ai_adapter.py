import os
import logging
from typing import Any, Dict, List

from openai import OpenAI
from langfuse import observe

from .base import BaseLLMAdapter
from ..transport.auth_http_client_factory import AuthHttpClientFactory

logger = logging.getLogger("llm.sdk.adapters.openai")


class OpenAIAdapter(BaseLLMAdapter):
    """
    Adapter para OpenAI-compatible APIs usando SDK oficial.
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

        self._client = OpenAI(
            base_url=self.base_url,
            api_key="unused",
            http_client=self._http_client,
            **self.client_kwargs,
        )

    # -------------------------
    # API
    # -------------------------

    @observe(name="adapter.openai.chat")
    def chat(self, model: str, messages: List[Dict[str, Any]], **kwargs):
        return self._client.chat.completions.create(
            model=model,
            messages=messages,
            **kwargs,
        )

    @observe(name="adapter.openai.completions")
    def completions(self, model: str, prompt: str, **kwargs):
        return self._client.completions.create(
            model=model,
            prompt=prompt,
            **kwargs,
        )

    @observe(name="adapter.openai.embeddings")
    def embeddings(self, model: str, input: Any, **kwargs):
        return self._client.embeddings.create(
            model=model,
            input=input,
            **kwargs,
        )

    @observe(name="adapter.openai.health", capture_input=False, capture_output=False)
    def health(self) -> Dict[str, Any]:
        return {"status": "ok", "provider": "openai"}
