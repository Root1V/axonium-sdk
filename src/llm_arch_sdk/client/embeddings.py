
import logging
from typing import Optional

from .base_client import BaseClient
from ..config.settings import get_sdk_settings
from ..observability.bootstrap import observe
from llm_arch_sdk.observability.context import obs, build_sdk_metadata, build_sdk_tags


logger = logging.getLogger("llama.embeddings")


class Embeddings:
    def __init__(self, client: BaseClient):
        self._client = client
        self._settings = get_sdk_settings()

    @observe(
        name="llama.embeddings.create",
        as_type="generation"
    )
    def create(
        self,
        model: str,
        input: list[str],
        trace_metadata: Optional[dict] = None,
        trace_tags: Optional[list[str]] = None,
    ):
        logger.debug("llama.embeddings.create model=%s input=%s", model, input)
        payload = {"model": model, "input": input}
        
        # Construir metadata automáticamente (SDK info + operación + custom)
        sdk_metadata = build_sdk_metadata(
            operation="embedding2",
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
                self._settings.llm.endpoints.embeddings,
                json=payload,
            )
        except Exception as exc:
            logger.error("Error in llama.embeddings.create: %s", exc)
            raise