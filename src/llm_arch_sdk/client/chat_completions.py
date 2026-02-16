
import logging
from typing import Optional

from .base_client import BaseClient
from ..models.chat_completion import ChatCompletionResult
from ..config.settings import _sdk_settings
from langfuse import observe

logger = logging.getLogger("llm.client.chatcompletions")


class ChatCompletions:
    def __init__(self, client: BaseClient):
        self._client = client

    @observe(
        name="llama.client.chatcompletions",
        as_type="generation"
    )
    def create(
        self,
        model: str,
        messages: list,
        trace_metadata: Optional[dict] = None,
        trace_tags: Optional[list[str]] = None,
        **kwargs,
    ):
        payload = {
            "model": model,
            "messages": messages,
            **kwargs,
        }

        logger.debug("llm.client.chatcompletions.create %s", payload)
               
        try:
            raw = self._client._request(
                "POST",
                _sdk_settings.llm.endpoints.chat_completions,
                json=payload,
            )

            logger.debug("llm.client.chatcompletions.create response %s", raw)

            return ChatCompletionResult.from_dict(raw)
        except Exception as exc:
            logger.error("Error in chat completions: %s", exc)
            raise
        finally:
            pass