import os
import logging
from typing import Any, Dict, List

from ..observability.bootstrap import observe
from llm_arch_sdk.observability.context import obs, build_sdk_metadata, build_sdk_tags

from .base_llm_adapter import BaseLLMAdapter, LLMAdapterType, LLMOperation
from ..client.llm_client import LlmClient
from ..transport.auth_http_client_factory import AuthHttpClientFactory
from ..config.settings import get_sdk_settings

logger = logging.getLogger("llm.sdk.adapters.llama")


class LlamaAdapter(BaseLLMAdapter):
    """
    Adapter para llama-server (OpenAI-compatible API).
    """

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        timeout: float = None,
        settings = None,
        **client_kwargs,
    ):
        self._model = model
        self._settings = settings or get_sdk_settings()
        self.base_url = base_url or self._settings.llm.base_url
        self.timeout = timeout or self._settings.transport.timeout_seconds
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
    def chat(self, messages: List[Dict[str, Any]], **kwargs):
        
        sdk_metadata = build_sdk_metadata(
            adapter=LLMAdapterType.LLAMA,
            operation=LLMOperation.CHAT,
            model=self._model
        )
        sdk_tags = build_sdk_tags(self._model)
        obs.update(
            input=messages,
            metadata=sdk_metadata,
            tags=sdk_tags
        )
        
        return self._client.chat.create(
            model=self._model,
            messages=messages,
            **kwargs,
        )

    @observe(name="adapter.llama.completions")
    def completions(self, prompt: str, **kwargs):
        sdk_metadata = build_sdk_metadata(
            adapter=LLMAdapterType.LLAMA,
            operation=LLMOperation.COMPLETIONS,
            model=self._model
        )
        sdk_tags = build_sdk_tags(self._model)
        obs.update(
            input=prompt,
            metadata=sdk_metadata,
            tags=sdk_tags
        )   
        return self._client.completions.create(
            model=self._model,
            prompt=prompt,
            **kwargs,
        )

    @observe(name="adapter.llama.embeddings")
    def embeddings(self, input: Any, **kwargs):
        sdk_metadata = build_sdk_metadata(
            adapter=LLMAdapterType.LLAMA,
            operation=LLMOperation.EMBEDDINGS,
            model=self._model
        )
        sdk_tags = build_sdk_tags(self._model)
        obs.update(
            input=input,
            metadata=sdk_metadata,
            tags=sdk_tags
        )
        return self._client.embeddings.create(
            model=self._model,
            input=input,
            **kwargs,
        )

    @observe(name="adapter.llama.health", capture_input=False, capture_output=False)
    def health(self) -> Dict[str, Any]:
        sdk_metadata = build_sdk_metadata(
            adapter=LLMAdapterType.LLAMA,
            operation=LLMOperation.HEALTH,
        )
        obs.update(
            metadata=sdk_metadata,
        )
        return self._client.health()
    
    def _validate_config(self):
        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL no configurada")
