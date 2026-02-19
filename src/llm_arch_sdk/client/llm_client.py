import httpx
import logging
from http import HTTPStatus

from .base_client import BaseClient
from .chat_completions import ChatCompletions
from .completions import Completions
from .embeddings import Embeddings
from ..transport.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from langfuse import observe
from ..config.settings import _sdk_settings
from llm_arch_sdk.observability.context import obs

logger = logging.getLogger("llm.sdk.client")

class LlmAPIError(Exception):
    pass


class LlmClient(BaseClient):
    """
    Cliente liviano para llama-server compatible con OpenAI-style APIs
    """

    def __init__(self, base_url: str, http_client: httpx.Client ):
        self.base_url = base_url.rstrip("/")
        self._http_client = http_client
        self._circuit = CircuitBreaker()
        self.adapter_type = "llama"

        self.completions = Completions(self)
        self.chat = ChatCompletions(self)
        self.embeddings = Embeddings(self)
    
    @observe(
        name="llama.request",
    )
    def _request(self, method: str, endpoint: str, **kwargs):
        if not self._circuit.allow_request():
            obs.update(
                metadata={"circuit": self._circuit._state.value, "blocked": True}
            )
            
            raise CircuitBreakerOpen("Circuit abierto para llama-server")
        
        try:
            
            resp = self._http_client.request(
                method,
                f"{self.base_url}{endpoint}",
                **kwargs,
            )
            if resp.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
                self._circuit.record_failure()
                raise LlmAPIError(f"Error {resp.status_code}, {method} {endpoint}: {resp.text}")

            self._circuit.record_success()
            resp.raise_for_status()
            
            obs.update(
                metadata={
                    "status_code": resp.status_code,
                    "endpoint": endpoint,
                    "method": method,
                }
            )
            
            return resp.json()
            
        except httpx.HTTPStatusError as e:
            self._circuit.record_failure()
            
            obs.update(
                metadata={
                    "status_code": e.response.status_code,
                    "endpoint": endpoint,
                }
            )
            raise LlmAPIError(f"HTTP {e.response.status_code}: {e.response.text}") from e
        
        except (httpx.TimeoutException, httpx.RequestError) as e:
            self._circuit.record_failure()
            obs.update(
                metadata={
                    "endpoint": endpoint,
                    "error_type": type(e).__name__,
                }
            )
            raise LlmAPIError(str(e)) from e
    
    
    @observe(
        name="llama.client.health",
    )
    def health(self):
        return self._request("GET", _sdk_settings.llm.endpoints.health)
    
