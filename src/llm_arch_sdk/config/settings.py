from dataclasses import dataclass, field
from typing import List, Optional
import os
from importlib.metadata import version

# -------------------------
# Constantes compartidas
# -------------------------

_SDK_NAME = "llm-arch-sdk"

# -------------------------
# Observability
# -------------------------

@dataclass
class ObservabilitySettings:
    enabled: bool = field(
        default_factory=lambda: os.getenv("OBSERVABILITY_ENABLED", "false").lower() in ("true", "1", "yes")
    )

    provider: str = "langfuse"

    capture_input: bool = False
    capture_output: bool = False

    # hooks de masking registrados por nombre
    masking_strategies: List[str] = field(default_factory=lambda: [
        "mask_pii",
        "mask_credit_card",
        "mask_email_and_phone",
    ])

# -------------------------
# LLM backend
# -------------------------

@dataclass
class LlmEndpoints:
    """Endpoints del API LLM"""
    login: str = "/llm/login"
    completions: str = "/llm/completions"
    chat_completions: str = "/llm/chat/completions"
    embeddings: str = "/v1/embeddings"
    health: str = "/health"


@dataclass
class LlmBackendEnv:
    base_url: str = os.getenv("LLM_BASE_URL")
    username: str = os.getenv("LLM_USERNAME")
    password: str = os.getenv("LLM_PASSWORD")
    endpoints: LlmEndpoints = field(default_factory=LlmEndpoints)
    openai_api_key: str = "internal-gateway"

    

# -------------------------
# Langfuse
# -------------------------

@dataclass
class LangfuseEnv:
    public_key: Optional[str] = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key: Optional[str] = os.getenv("LANGFUSE_SECRET_KEY")
    base_url: Optional[str] = os.getenv("LANGFUSE_BASE_URL")
    environment: Optional[str] = os.getenv("LANGFUSE_TRACING_ENVIRONMENT")
    release: str = field(default_factory=lambda: version(_SDK_NAME))


# -------------------------
# OpenTelemetry
# -------------------------

@dataclass
class OtelEnv:
    service_name: str = field(default_factory=lambda: f"{_SDK_NAME}:{version(_SDK_NAME)}")
    env_name : str = "OTEL_SERVICE_NAME"



# -------------------------
# Transport / HTTP
# -------------------------

@dataclass
class TransportSettings:
    timeout_seconds: float = 60.0


@dataclass
class AuthSettings:
    token_timeout: float = 10.0
    name_token: str = "token"
    header_token: str = "Authorization"
    token_prefix: str = "Bearer"

# -------------------------
# Circuit breaker
# -------------------------

@dataclass
class CircuitBreakerSettings:
    failure_threshold: int = 3
    reset_timeout: int = 30
    half_open_success: int = 1
    retry_header: str = "X-Retry"
    retry_value: int = 1


# -------------------------
# SDK identity
# -------------------------

@dataclass
class SdkIdentitySettings:
    name: str = _SDK_NAME
    mversion: str = field(default_factory=lambda: version(_SDK_NAME))
    accept: str = "application/json"
    
    @property
    def user_agent(self) -> str:
        return f"SDK-Arch-PE/LLM-Client/v{self.mversion}"
    

# -------------------------
# Root SDK settings
# -------------------------

@dataclass
class SdkSettings:
    observability: ObservabilitySettings = field(default_factory=ObservabilitySettings)
    transport: TransportSettings = field(default_factory=TransportSettings)
    circuit_breaker: CircuitBreakerSettings = field(default_factory=CircuitBreakerSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    identity: SdkIdentitySettings = field(default_factory=SdkIdentitySettings)
    llm: LlmBackendEnv = field(default_factory=LlmBackendEnv)
    langfuse: LangfuseEnv = field(default_factory=LangfuseEnv)
    otel: OtelEnv = field(default_factory=OtelEnv)


# singleton
_sdk_settings = SdkSettings()


def get_sdk_settings() -> SdkSettings:
    return _sdk_settings

