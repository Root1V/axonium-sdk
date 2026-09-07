"""Python SDK for the Prometheus Gateway inference API.

The client surface is assembled here as the implementation lands. See ``spec/prometheus-gateway.md``
in the repository for the API contract this package implements.
"""

from axonium.auth import TokenClaims, TokenSet
from axonium.client import AsyncAxonium, Axonium
from axonium.config import AxoniumConfig, Timeouts
from axonium.errors import (
    APIError,
    AuthTransportError,
    AxoniumError,
    BackendUnavailableError,
    BadRequestError,
    ConfigurationError,
    ContextExceededError,
    ForbiddenError,
    InvalidClientError,
    InvalidScopeError,
    InvalidTokenError,
    MissingCredentialsError,
    ModalityMismatchError,
    ModelNotLoadedError,
    OAuthError,
    RateLimitError,
    RateLimitingUnavailableError,
    ServerError,
    SpendCapExceededError,
    StreamInterruptedError,
    TimeoutError,
    TokenExpiredError,
    TokenRevokedError,
    TransportError,
    UnauthorizedClientError,
    UnauthorizedError,
    UnknownModelError,
    UnsupportedFieldWarning,
    UnsupportedGrantTypeError,
    UpstreamError,
    UsageStoreUnavailableError,
)
from axonium.models.catalog import Model, ModelList
from axonium.models.common import APIObject, RateLimitSnapshot, ResponseMeta, Usage

__version__ = "1.0.0.dev0"

__all__ = [
    "APIError",
    "APIObject",
    "AsyncAxonium",
    "AuthTransportError",
    "Axonium",
    "AxoniumConfig",
    "AxoniumError",
    "BackendUnavailableError",
    "BadRequestError",
    "ConfigurationError",
    "ContextExceededError",
    "ForbiddenError",
    "InvalidClientError",
    "InvalidScopeError",
    "InvalidTokenError",
    "MissingCredentialsError",
    "ModalityMismatchError",
    "Model",
    "ModelList",
    "ModelNotLoadedError",
    "OAuthError",
    "RateLimitError",
    "RateLimitSnapshot",
    "RateLimitingUnavailableError",
    "ResponseMeta",
    "ServerError",
    "SpendCapExceededError",
    "StreamInterruptedError",
    "TimeoutError",
    "Timeouts",
    "TokenClaims",
    "TokenExpiredError",
    "TokenRevokedError",
    "TokenSet",
    "TransportError",
    "UnauthorizedClientError",
    "UnauthorizedError",
    "UnknownModelError",
    "UnsupportedFieldWarning",
    "UnsupportedGrantTypeError",
    "UpstreamError",
    "Usage",
    "UsageStoreUnavailableError",
    "__version__",
]
