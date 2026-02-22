import httpx
import threading
import logging
from http import HTTPStatus
from typing import Optional

from ..transport.circuit_breaker import CircuitBreaker
from ..transport.http_client_factory import HttpClientFactory
from ..observability.bootstrap import observe
from ..observability.context import obs

from ..config.settings import get_sdk_settings

logger = logging.getLogger("llm.sdk.auth.token_manager")


class AuthError(Exception):
    """Errores relacionados con autenticación contra el gateway LLM."""


class TokenManager(httpx.Auth):
    def __init__(self, timeout: float = None, settings = None):
        self._settings = settings or get_sdk_settings()
        self.s_llm = self._settings.llm
        self.s_auth = self._settings.auth
        self.s_circuit = self._settings.circuit_breaker
        self.s_timeout = timeout or self.s_auth.token_timeout
        
        self._validate()

        self.token: Optional[str] = None
        self._lock = threading.Lock()

        self._login_client = HttpClientFactory.create(timeout=self.s_timeout)
        self._circuit = CircuitBreaker()

    def auth_flow(self, request):
        # NOTE: @observe no puede decorar generadores (usa yield); Langfuse v3
        # envuelve generadores en _ContextPreservedSyncGeneratorWrapper que no
        # implementa .send(), rompiendo el protocolo de httpx.Auth.
        # La trazabilidad de auth se cubre a través de @observe en _login.
        # 1 Asegurar token (thread-safe)
        if not self.token:
            with self._lock:
                if not self.token:
                    logger.info("Token no presente, login inicial")  
                    obs.update(
                        metadata={"auth.reason": "missing_token"}
                    )                  
                    self.token = self._login()
        else:
            obs.update(
                metadata={"auth.reason": "cached_token"}
            )

        # 2 Adjuntar token
        request.headers[self.s_auth.header_token] = f"{self.s_auth.token_prefix} {self.token}"
        obs.update(
            metadata={"auth.token_attached": True}
        )

        # 3️ Enviar request
        response = yield request

        # 4️ Retry UNA vez si token expiró
        if response.status_code == HTTPStatus.UNAUTHORIZED and not request.headers.get(self.s_circuit.retry_header):
            logger.warning("401 recibido, refrescando token")

            obs.update(
                metadata={"auth.reason": "token_expired"}
            )

            with self._lock:
                self.token = self._login()

            request.headers[self.s_auth.header_token] = f"{self.s_auth.token_prefix} {self.token}"
            request.headers[self.s_circuit.retry_header] = self.s_circuit.retry_value

            yield request

    @observe(
        name="llm.auth.login",
    )
    def _login(self) -> str:
        # Circuit breaker: ¿se permite intentar login?
        if not self._circuit.allow_request():
            obs.update(
                metadata={"circuit": self._circuit.state.value, "blocked": True}
            )
            logger.error("Circuit breaker abierto: login bloqueado")
            raise AuthError("Circuit breaker abierto: login bloqueado")

        try:
            obs.update(
                metadata={"login.endpoint": self.s_llm.endpoints.login}
            )
            resp = self._login_client.post(
                f"{self.s_llm.base_url}{self.s_llm.endpoints.login}",
                auth=(self.s_llm.username, self.s_llm.password),
            )
            resp.raise_for_status()

            data = resp.json()
            token = data.get(self.s_auth.name_token)

            if not token:
                logger.error("Login exitoso pero sin token")
                raise AuthError("Login exitoso pero sin token")

            self._circuit.record_success()
            
            return token

        except httpx.TimeoutException as e:
            self._circuit.record_failure()
            logger.error("Timeout durante login")
            obs.update(
                metadata={"circuit": self._circuit.state.value, "error": type(e).__name__}
            )
            raise AuthError("Timeout durante login") from e

        except httpx.RequestError as e:
            self._circuit.record_failure()
            logger.error("Error de conexión durante login")
            obs.update(
                metadata={"circuit": self._circuit.state.value, "error": type(e).__name__}
            )
            raise AuthError(f"Error de conexión durante login: {e}") from e

        except httpx.HTTPStatusError as e:
            self._circuit.record_failure()
            logger.error(
                "Error HTTP durante login",
                extra={"status_code": e.response.status_code},
            )
            obs.update(
                metadata={"circuit": self._circuit.state.value, "error": type(e).__name__}
            )
            raise AuthError(
                f"Error HTTP durante login: {e.response.status_code}"
            ) from e

        except Exception as e:
            self._circuit.record_failure()
            logger.exception("Error inesperado durante login")
            obs.update(
                metadata={"circuit": self._circuit.state.value, "error": type(e).__name__}
            )
            raise AuthError("Error inesperado durante login") from e
    
    def _validate(self):
        if not self.s_llm.base_url:
            raise RuntimeError("LLM_BASE_URL no configurada")
        if not self.s_llm.username:
            raise RuntimeError("LLM_USERNAME no configurado")
        if not self.s_llm.password:
            raise RuntimeError("LLM_PASSWORD no configurado")
