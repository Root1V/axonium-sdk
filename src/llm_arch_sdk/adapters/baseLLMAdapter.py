from abc import ABC, abstractmethod


class BaseLLMAdapter(ABC):

    @abstractmethod
    def chat(
        self,
        *,
        model: str,
        messages: list,
        session_id: str | None = None,
        job_id: str | None = None,
        agent_id: str | None = None,
        **kwargs
    ) -> ChatCompletionResult:
        ...
