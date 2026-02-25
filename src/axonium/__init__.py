"""
Axonium - Enterprise LLM Integration Framework

A powerful SDK for consuming LLM servers with automatic authentication,
token renewal, circuit breakers, and observability.

Basic Usage:
    >>> from axonium import LlamaAdapter, MiniAgent
    >>> 
    >>> # Create adapter
    >>> adapter = LlamaAdapter(model="llama-7b")
    >>> 
    >>> # Make a chat request
    >>> response = adapter.chat(
    ...     messages=[{"role": "user", "content": "Hello!"}],
    ...     temperature=0.7
    ... )

Environment Variables:
    - LLM_BASE_URL: Base URL for the LLM server
    - LLM_USERNAME: Authentication username
    - LLM_PASSWORD: Authentication password
    - OBSERVABILITY_ENABLED: Enable/disable Langfuse observability (default: false)
    - MASKING_ENABLED: Enable/disable PII masking (default: true)
    - LANGFUSE_PUBLIC_KEY: Langfuse public key (if observability enabled)
    - LANGFUSE_SECRET_KEY: Langfuse secret key (if observability enabled)
    - LANGFUSE_BASE_URL: Langfuse server URL (if observability enabled)

For more information, see: https://github.com/Root1V/llm-arch-sdk
"""

__version__ = "0.4.6"

# ==========================================
# Public API - Adapters
# ==========================================

from .adapters.base_llm_adapter import BaseLLMAdapter, LLMAdapterType, LLMOperation
from .adapters.llama_adapter import LlamaAdapter
from .adapters.open_ai_adapter import OpenAIAdapter

# ==========================================
# Public API - Integrations (Workflows)
# ==========================================

from .integrations.agent import MiniAgent
from .integrations.llm_runnable import LLMRunnable

# ==========================================
# Public API - Configuration
# ==========================================

from .config.settings import (
    get_sdk_settings,
    SdkSettings,
    ObservabilitySettings,
    LlmBackendEnv,
    LangfuseEnv,
)

# ==========================================
# Public API - Models (Data Structures)
# ==========================================

from .models.chat_completion import ChatCompletionResult
from .models.completion import CompletionResult
from .models.llm_response import LLMResponse
from .models.generation_settings import GenerationSettings

# ==========================================
# Public API - Client (Low-level HTTP)
# ==========================================

from .client.llm_client import LlmClient

# ==========================================
# Public Exports
# ==========================================

__all__ = [
    # Version
    "__version__",
    
    # Adapters - High-level LLM interfaces
    "BaseLLMAdapter",
    "LlamaAdapter",
    "OpenAIAdapter",
    "LLMAdapterType",
    "LLMOperation",
    
    # Integrations - Workflow tools
    "MiniAgent",
    "LLMRunnable",
    
    # Configuration
    "get_sdk_settings",
    "SdkSettings",
    "ObservabilitySettings",
    "LlmBackendEnv",
    "LangfuseEnv",
    
    # Models - Data structures
    "ChatCompletionResult",
    "CompletionResult",
    "LLMResponse",
    "GenerationSettings",
    
    # Client - Low-level HTTP client
    "LlmClient",
]
