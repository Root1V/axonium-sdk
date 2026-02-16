
import logging
from typing import Optional

from .base_client import BaseClient
from ..config.settings import _sdk_settings
from langfuse import observe


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

        try:
            return self._client._request(
                "POST",
                _sdk_settings.llm.endpoints.embeddings,
                json=payload,
            )
        except Exception as exc:
            raise
        finally:
            pass