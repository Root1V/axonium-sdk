
import logging
from typing import Optional

from .base_client import BaseClient
from ..models.completion import CompletionResult
from ..config.settings import _sdk_settings
from langfuse import observe
from llm_arch_sdk.observability.context import obs

logger = logging.getLogger("llm.client.completions")


class Completions:
    def __init__(self, client: BaseClient):
        self._client = client

    @observe(
        name="llama.client.completions.create",
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

        logger.debug("llm.client.completions.create %s", payload)
        obs.update(
            input=prompt,
            metadata={
                "temperature": temperature,
                "n_predict": n_predict,
            }
        )

        raw = self._client._request(
            "POST",
            _sdk_settings.llm.endpoints.completions,
            json=payload,
        )

        logger.debug("llm.client.completions.create response %s", raw)
            
        result = CompletionResult.from_dict(raw)

        return result
        
