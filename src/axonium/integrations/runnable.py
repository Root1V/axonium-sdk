# axonium/integrations/langchain/runnable.py

from typing import Any, Dict, List, Optional, Union
from langchain_core.runnables import Runnable
from langchain_core.messages import BaseMessage
from ..adapters.base_llm_adapter import BaseLLMAdapter


class SDKChatRunnable(Runnable):
    def __init__(self, adapter: BaseLLMAdapter):
        self._adapter = adapter
        
    def _to_openai_messages(self, messages: list[BaseMessage]):
        out = []
        for m in messages:
            role = "user"
            if m.type == "ai":
                role = "assistant"
            elif m.type == "system":
                role = "system"

            out.append({"role": role, "content": m.content})
        return out

    def invoke(self, input: Dict[str, Any], config=None) -> Any:
        """
        input esperado:
        {
            "messages": [...]
            "model": ...
        }
        """
        
        messages = self._to_openai_messages(input["messages"])
        model=input.get("model"),

        return self._adapter.chat(
            model=model,
            messages=messages,        
        )
        


class SDKCompletionsRunnable(Runnable):
    """
    Bridge entre LangChain y Axonium para text completions.

    Responsabilidad única:
    - normalizar prompt
    - delegar en adapter.completions(...)
    """

    def __init__(
        self,
        adapter: BaseLLMAdapter,
        default_model: Optional[str] = None,
    ):
        self._adapter = adapter
        self._default_model = default_model

    def invoke(
        self,
        input: Union[str, Dict[str, Any]],
        config: Optional[dict] = None,
    ) -> Any:

        prompt, model, session_id, params = self._normalize_input(input)

        return self._adapter.completions(
            prompt=prompt,
            model=model,
            session_id=session_id,
            **params,
        )

    # -------------------------
    # Internal
    # -------------------------

    def _normalize_input(
        self,
        input: Union[str, Dict[str, Any]],
    ) -> tuple[str, Optional[str], Optional[str], Dict[str, Any]]:

        # runnable.invoke("hola")
        if isinstance(input, str):
            return input, self._default_model, None, {}

        if not isinstance(input, dict):
            raise ValueError("Invalid input type for completions runnable")

        prompt = input.get("prompt")
        if not prompt:
            raise ValueError("Completions runnable expects 'prompt'")

        model = input.get("model") or self._default_model
        session_id = input.get("session_id")

        # parámetros opcionales del completion
        params = dict(input.get("params") or {})

        return prompt, model, session_id, params
    
    

class SDKEmbeddingsRunnable(Runnable):
    """
    Bridge entre LangChain y Axonium para embeddings.

    Responsabilidad única:
    - normalizar input
    - delegar en adapter.embeddings(...)
    """

    def __init__(
        self,
        adapter: BaseLLMAdapter,
        default_model: Optional[str] = None,
    ):
        self._adapter = adapter
        self._default_model = default_model

    def invoke(
        self,
        input: Union[str, List[str], Dict[str, Any]],
        config: Optional[dict] = None,
    ) -> Any:

        texts, model, session_id = self._normalize_input(input)

        return self._adapter.embeddings(
            input=texts,
            model=model,
            session_id=session_id,
        )

    # -------------------------
    # Internal
    # -------------------------

    def _normalize_input(
        self,
        input: Union[str, List[str], Dict[str, Any]],
    ) -> tuple[list[str], Optional[str], Optional[str]]:

        # Caso simple: runnable.invoke("hola")
        if isinstance(input, str):
            return [input], self._default_model, None

        # runnable.invoke(["a", "b"])
        if isinstance(input, list):
            return input, self._default_model, None

        # runnable.invoke({ ... })
        if isinstance(input, dict):
            texts = input.get("input") or input.get("texts")
            if texts is None:
                raise ValueError("Embeddings runnable expects 'input' or 'texts'")

            if isinstance(texts, str):
                texts = [texts]

            model = input.get("model") or self._default_model
            session_id = input.get("session_id")

            return texts, model, session_id

        raise ValueError("Invalid input type for embeddings runnable")