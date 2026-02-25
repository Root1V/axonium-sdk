from .runnable import SDKChatRunnable, SDKCompletionsRunnable, SDKEmbeddingsRunnable


class ChatNode:
    def __init__(self, runnable: SDKChatRunnable):
        self._runnable = runnable

    def __call__(self, state: dict) -> dict:
        """
        Espera en el state:
          - messages
          - model (opcional)
          - session_id (opcional)
        """

        result = self._runnable.invoke({
            "messages": state["messages"],
            "model": state.get("model"),
            "session_id": state.get("session_id"),
        })

        return {
            **state,
            "chat_result": result,
        }
        
class CompletionNode:

    def __init__(self, runnable: SDKCompletionsRunnable):
        self._runnable = runnable

    def __call__(self, state: dict):
        result = self._runnable.invoke({
            "prompt": state["prompt"],
            "model": state.get("model"),
            "session_id": state.get("session_id"),
        })

        return {
            **state,
            "completion": result.text,
        }
        
class EmbeddingNode:
    def __init__(self, runnable: SDKEmbeddingsRunnable):
        self._runnable = runnable

    def __call__(self, state: dict) -> dict:
        """
        Espera en el state:
          - input (str o list[str])
          - model (opcional)
          - session_id (opcional)
        """

        result = self._runnable.invoke({
            "input": state["input"],
            "model": state.get("model"),
            "session_id": state.get("session_id"),
        })

        return {
            **state,
            "embedding_result": result,
        }