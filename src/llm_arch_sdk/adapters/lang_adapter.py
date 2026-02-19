import logging
from typing import Any, Dict, List

from langfuse import observe

from .base_llm_adapter import BaseLLMAdapter

logger = logging.getLogger("llm.sdk.adapters.langchain")


class LangChainAdapter(BaseLLMAdapter):
    """
    Adapter para LangChain / LangGraph.
    El consumidor inyecta el runnable.
    """

    def __init__(self, runnable):
        self._runnable = runnable

    @observe(name="adapter.langchain.chat")
    def chat(self, model: str, messages: List[Dict[str, Any]], **kwargs):
        return self._runnable.invoke(
            {"messages": messages, **kwargs}
        )

    @observe(name="adapter.langchain.completions")
    def completions(self, model: str, prompt: str, **kwargs):
        return self._runnable.invoke(
            {"prompt": prompt, **kwargs}
        )

    @observe(name="adapter.langchain.embeddings")
    def embeddings(self, model: str, input: Any, **kwargs):
        return self._runnable.embed(input)

    @observe(name="adapter.langchain.health", capture_input=False, capture_output=False)
    def health(self) -> Dict[str, Any]:
        raise NotImplementedError("Health check no implementado para LangChainAdapter")
