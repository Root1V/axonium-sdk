from abc import ABC, abstractmethod

from llm_arch_sdk.models.chat_completion import ChatCompletionResult


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
        pass
    
    @abstractmethod
    def completion(
        self,
        *,
        model: str,
        messages: list,
        session_id: str | None = None,
        job_id: str | None = None,
        agent_id: str | None = None,
        **kwargs
    ) -> ChatCompletionResult:
        pass
    
    @abstractmethod
    def embed(
        self,
        *,
        model: str,
        input: list[str],
        session_id: str | None = None,
        job_id: str | None = None,
        agent_id: str | None = None,
        **kwargs
    ) -> ChatCompletionResult:
        pass
    
    
    @abstractmethod
    def health(
        self,
        *,
        model: str,
        input: list[str],
        session_id: str | None = None,
        job_id: str | None = None,
        agent_id: str | None = None,
        **kwargs
    ) -> ChatCompletionResult:
        pass
    
        
    
