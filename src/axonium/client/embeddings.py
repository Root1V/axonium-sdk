
import logging

from .base_client import BaseClient
from ..config.settings import get_sdk_settings


logger = logging.getLogger("llama.embeddings")


class Embeddings:
    def __init__(self, client: BaseClient):
        self._client = client
        self._settings = get_sdk_settings()

    def create(
        self,
        model: str,
        input: list[str],
    ):
        logger.debug("llama.embeddings.create model=%s input=%s", model, input)
        payload = {"model": model, "input": input}

        try:
            return self._client._request(
                "POST",
                self._settings.llm.endpoints.embeddings,
                json=payload,
            )
        except Exception as exc:
            logger.error("Error in llama.embeddings.create: %s", exc)
            raise