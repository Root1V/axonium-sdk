import threading
import time
import logging
from enum import Enum

from ..config.settings import get_sdk_settings

logger = logging.getLogger("llm.sdk.transport.circuit_breaker")

class CircuitState(Enum):
    CLOSED = "closed"  # operación normal
    OPEN = "open"  # llamadas bloqueadas
    HALF_OPEN = "half_open" # se permite UNA llamada de prueba


class CircuitBreakerOpen(Exception):
    pass


class CircuitBreaker:
    """
    Circuit Breaker con estados:
    """

    def __init__(self, failure_threshold: int = None, reset_timeout: int = None, half_open_success: int = None):

        cfg = get_sdk_settings().circuit_breaker
        
        self.failure_threshold = failure_threshold or cfg.failure_threshold
        self.reset_timeout = reset_timeout or cfg.reset_timeout
        self.half_open_success = half_open_success or cfg.half_open_success
        
        
        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._open_until: float = None
        
        self._lock = threading.Lock()
        
    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def allow_request(self) -> bool:
        now = time.monotonic()
        
        with self._lock:
            if self._state == CircuitState.OPEN:
                if now >= self._open_until:
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logger.warning("Circuit breaker pasando a HALF_OPEN")
                    return True
                return False
            return True
    

    def record_success(self):
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.half_open_success:
                    logger.info("Circuit breaker cerrado tras éxito en HALF_OPEN")
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    self._open_until = None
            else:
                self._failure_count = 0

        
    def record_failure(self):
        with self._lock:
            self._failure_count += 1
            logger.warning("Fallo, registrado en circuit breaker:  %s/%s", self._failure_count, self.failure_threshold)

            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._open_until = time.monotonic() + self.reset_timeout
                logger.error(
                    "Circuit breaker ABIERTO",
                    extra={"open_until": self._open_until},
                )
