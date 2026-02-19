import os
import logging
from typing import Any, Dict, List

from openai import OpenAI
from langfuse import observe

from .base_llm_adapter import BaseLLMAdapter
from ..transport.auth_http_client_factory import AuthHttpClientFactory
from ..config.settings import _sdk_settings


logger = logging.getLogger("llm.sdk.adapters.openai")


class OpenAIAdapter(BaseLLMAdapter):
    """
    Adapter para OpenAI-compatible APIs usando SDK oficial.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = None,
        **client_kwargs,
    ):
        self.base_url = base_url or _sdk_settings.llm.base_url
        self.timeout = timeout or _sdk_settings.transport.timeout_seconds
        self.client_kwargs = client_kwargs
        
        self._validate_config()

        self._http_client = AuthHttpClientFactory.create(timeout=self.timeout)

        self._client = OpenAI(
            base_url=self.base_url,
            api_key=_sdk_settings.llm.openai_api_key,
            http_client=self._http_client,
            default_headers=AuthHttpClientFactory._default_headers(),
            **self.client_kwargs,
        )

    # -------------------------
    # API
    # -------------------------
    def client(self) -> OpenAI:
        """
        Devuelve una instancia singleton de OpenAI
        completamente configurada.
        """
        return self._client
    

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
        raise NotImplementedError("OpenAI API no tiene endpoint de health check estándar") 
    
    def _validate_config(self):
        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL no está configurado")

        if not self.base_url.startswith("http"):
            raise RuntimeError(f"LLM_BASE_URL inválida: {self.base_url}")
    
    
