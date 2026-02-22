
import logging
from typing import Optional

from .base_client import BaseClient
from ..models.completion import CompletionResult
from ..config.settings import get_sdk_settings
from ..observability.bootstrap import observe
from llm_arch_sdk.observability.context import obs, build_sdk_metadata, build_sdk_tags

logger = logging.getLogger("llama.completions")


class Completions:
    def __init__(self, client: BaseClient):
        self._client = client
        self._settings = get_sdk_settings()

    @observe(
        name="llama.completions.create",
        as_type="generation"
    )
    def create(
        self,
        prompt: str,
        temperature: float ,
        n_predict: int = None,
        trace_metadata: Optional[dict] = None,
        trace_tags: Optional[list[str]] = None,
        **kwargs,
    ):
        payload = {
            "prompt": prompt,
            "temperature": temperature,
            "n_predict": n_predict,
            **kwargs,
        }

        logger.debug("llama.completions.create %s", payload)
        
        # Construir metadata automáticamente (SDK info + operación + custom)
        sdk_metadata = build_sdk_metadata(
            operation="completion2",
            model=kwargs.get("model"),
            temperature=temperature,
            n_predict=n_predict,
            **(trace_metadata or {})
        )
        
        # Construir tags automáticamente (modelo + filtrado + custom)
        sdk_tags = build_sdk_tags(kwargs.get("model"), *(trace_tags or []))
        
        obs.update(
            input=prompt,
            metadata=sdk_metadata,
            tags=sdk_tags
        )

        raw = self._client._request(
            "POST",
            self._settings.llm.endpoints.completions,
            json=payload,
        )

        logger.debug("llama.completions.create response %s", raw)
            
        result = CompletionResult.from_dict(raw)

        return result
        
