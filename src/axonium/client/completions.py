
import logging

from .base_client import BaseClient
from ..models.completion import CompletionResult
from ..config.settings import get_sdk_settings

logger = logging.getLogger("llama.completions")


class Completions:
    def __init__(self, client: BaseClient):
        self._client = client
        self._settings = get_sdk_settings()

    def create(
        self,
        prompt: str,
        temperature: float ,
        n_predict: int = None,
        **kwargs,
    ):
        payload = {
            "prompt": prompt,
            "temperature": temperature,
            "n_predict": n_predict,
            **kwargs,
        }

        logger.debug("llama.completions.create %s", payload)
     
        raw = self._client._request(
            "POST",
            self._settings.llm.endpoints.completions,
            json=payload,
        )

        logger.debug("llama.completions.create response %s", raw)
            
        return CompletionResult.from_dict(raw)
        
