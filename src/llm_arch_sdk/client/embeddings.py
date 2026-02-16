
import logging
from typing import Optional

from .base_client import BaseClient
from ..config.settings import _sdk_settings
from langfuse import observe
from llm_arch_sdk.observability.context import obs, build_sdk_metadata, build_sdk_tags


logger = logging.getLogger("llm.client.embeddings")


class Embeddings:
    def __init__(self, client: BaseClient):
        self._client = client

    @observe(
        name="llama.client.embeddings.create",
        as_type="generation"
    )
    def create(
        self,
        model: str,
        input: list[str],
        trace_metadata: Optional[dict] = None,
        trace_tags: Optional[list[str]] = None,
    ):
        logger.debug("llm.client.embeddings.create model=%s input=%s", model, input)
        payload = {"model": model, "input": input}
        
        # Construir metadata automáticamente (SDK info + operación + custom)
        sdk_metadata = build_sdk_metadata(
            adapter=self._client.adapter_type,
            operation="embedding",
            model=model,
            **(trace_metadata or {})
        )
        
        # Construir tags automáticamente (modelo + filtrado + custom)
        sdk_tags = build_sdk_tags(model, *(trace_tags or []))
        
        obs.update(
            input=input,
            metadata=sdk_metadata,
            tags=sdk_tags
        )

        try:
            return self._client._request(
                "POST",
                _sdk_settings.llm.endpoints.embeddings,
                json=payload,
            )
        except Exception as exc:
            raise