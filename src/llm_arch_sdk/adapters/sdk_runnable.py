# llm_arch_sdk/integrations/langchain/runnable.py

from typing import Any, Dict
from langchain_core.runnables import Runnable
from langchain_core.messages import BaseMessage


class SDKRunnable(Runnable):
    def __init__(self, adapter):
        self._adapter = adapter
        
    def _to_openai_messages(messages: list[BaseMessage]):
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
            "session_id": ...
        }
        """
        
        messages = self._to_openai_messages(input["messages"])

        return self._adapter.chat(
            model=input.get("model"),
            messages=messages,
            session_id=input.get("session_id"),
        )
        
