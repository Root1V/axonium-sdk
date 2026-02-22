
import logging
from typing import Optional

from .base_client import BaseClient
from ..models.chat_completion import ChatCompletionResult
from ..config.settings import get_sdk_settings
from ..observability.bootstrap import observe
from llm_arch_sdk.observability.context import obs, build_sdk_metadata, build_sdk_tags

logger = logging.getLogger("llama.chatcompletions")


class ChatCompletions:
    def __init__(self, client: BaseClient):
        self._client = client
        self._settings = get_sdk_settings()

    @observe(
        name="llama.chatcompletions.create",
        as_type="generation"
    )
    def create(
        self,
        model: str,
        messages: list,
        trace_metadata: Optional[dict] = None,
        trace_tags: Optional[list[str]] = None,
        **kwargs,
    ) -> ChatCompletionResult: 
        payload = {
            "model": model,
            "messages": messages,
            **kwargs,
        }

        logger.debug("llama.chatcompletions.create %s", payload)
        
        # Construir metadata automáticamente (SDK info + operación + custom)
        sdk_metadata = build_sdk_metadata(
            adapter="llama2",
            operation="chat",
            model=model,
            **(trace_metadata or {})
        )
        
        # Construir tags automáticamente (modelo + filtrado + custom)
        sdk_tags = build_sdk_tags(model, *(trace_tags or []))
        
        obs.update(
            input=messages,
            metadata=sdk_metadata,
            tags=sdk_tags
        )
               
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
        finally:
            pass