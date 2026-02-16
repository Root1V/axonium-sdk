
import logging
from typing import Optional

from .base_client import BaseClient
from ..models.chat_completion import ChatCompletionResult
from ..config.settings import _sdk_settings
from langfuse import observe
from llm_arch_sdk.observability.context import obs, build_sdk_metadata, build_sdk_tags

logger = logging.getLogger("llm.client.chatcompletions")


class ChatCompletions:
    def __init__(self, client: BaseClient):
        self._client = client

    @observe(
        name="llama.client.chatcompletions.create",
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
        
        # Construir metadata automáticamente (SDK info + operación + custom)
        sdk_metadata = build_sdk_metadata(
            adapter=self._client.adapter_type,
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
                _sdk_settings.llm.endpoints.chat_completions,
                json=payload,
            )

            logger.debug("llm.client.chatcompletions.create response %s", raw)

            result = ChatCompletionResult.from_dict(raw)
            
            # Actualizar trace con model, usage y output para cálculo de tokens/costos en Langfuse
            obs.update(
                model=result.model,
                usage={
                    "input": result.usage.prompt_tokens if result.usage else 0,
                    "output": result.usage.completion_tokens if result.usage else 0,
                    "total": result.usage.total_tokens if result.usage else 0,
                },
                output=result.choices[0].message.content if result.choices else None
            )
            
            return result
        except Exception as exc:
            logger.error("Error in chat completions: %s", exc)
            raise
        finally:
            pass