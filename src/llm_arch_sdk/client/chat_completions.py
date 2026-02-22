
import logging

from .base_client import BaseClient
from ..models.chat_completion import ChatCompletionResult
from ..config.settings import get_sdk_settings

logger = logging.getLogger("llama.chatcompletions")


class ChatCompletions:
    def __init__(self, client: BaseClient):
        self._client = client
        self._settings = get_sdk_settings()

    def create(
        self,
        model: str,
        messages: list,
        **kwargs,
    ) -> ChatCompletionResult: 
        payload = {
            "model": model,
            "messages": messages,
            **kwargs,
        }

        logger.debug("llama.chatcompletions.create %s", payload)
        
        try:
            raw = self._client._request(
                "POST",
                self._settings.llm.endpoints.chat_completions,
                json=payload,
            )

            logger.debug("llama.chatcompletions.create response %s", raw)

            return ChatCompletionResult.from_dict(raw)
        except Exception as exc:
            logger.error("Error in llama.chatcompletions.create: %s", exc)
            raise
      