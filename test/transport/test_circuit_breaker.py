import time
from unittest.mock import patch
from axonium.transport.circuit_breaker import CircuitBreaker, CircuitBreakerOpen, CircuitState


class TestCircuitBreaker:
    def test_init_default_values(self):
        cb = CircuitBreaker()
        assert cb.failure_threshold == 3
        assert cb.reset_timeout == 30
        assert cb.half_open_success == 1
        assert cb._state == CircuitState.CLOSED
        assert cb._failure_count == 0
        assert cb._success_count == 0
        assert cb._open_until is None

    def test_init_custom_values(self):
        cb = CircuitBreaker(failure_threshold=5, reset_timeout=60, half_open_success=2)
        assert cb.failure_threshold == 5
        assert cb.reset_timeout == 60
        assert cb.half_open_success == 2

    def test_allow_request_closed_state(self):
        cb = CircuitBreaker()
        assert cb.allow_request() is True

    def test_allow_request_open_state_before_timeout(self):
        cb = CircuitBreaker()
        cb._state = CircuitState.OPEN
        cb._open_until = time.time() + 10  # 10 seconds from now
        assert cb.allow_request() is False

    @patch('time.time')
    def test_allow_request_open_state_after_timeout(self, mock_time):
        cb = CircuitBreaker()
        cb._state = CircuitState.OPEN
        cb._open_until = 100
        mock_time.return_value = 101  # After timeout

        assert cb.allow_request() is True
        assert cb._state == CircuitState.HALF_OPEN
        assert cb._success_count == 0

    def test_allow_request_half_open_state(self):
        cb = CircuitBreaker()
        cb._state = CircuitState.HALF_OPEN
        assert cb.allow_request() is True

    def test_record_success_closed_state(self):
        cb = CircuitBreaker()
        cb._failure_count = 2
        cb.record_success()
        assert cb._failure_count == 0
        assert cb._state == CircuitState.CLOSED

    def test_record_success_half_open_single_success(self):
        cb = CircuitBreaker(half_open_success=1)
        cb._state = CircuitState.HALF_OPEN
        cb.record_success()
        assert cb._success_count == 0  # Reset when closed
        assert cb._state == CircuitState.CLOSED
        assert cb._failure_count == 0
        assert cb._open_until is None

    def test_record_success_half_open_multiple_successes(self):
        cb = CircuitBreaker(half_open_success=3)
        cb._state = CircuitState.HALF_OPEN
        cb.record_success()
        assert cb._success_count == 1
        assert cb._state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb._success_count == 2
        assert cb._state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb._success_count == 0
        assert cb._state == CircuitState.CLOSED

    def test_record_failure_below_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        assert cb._failure_count == 1
        assert cb._state == CircuitState.CLOSED

        cb.record_failure()
        assert cb._failure_count == 2
        assert cb._state == CircuitState.CLOSED

    @patch('axonium.transport.circuit_breaker.time.monotonic')
    def test_record_failure_at_threshold(self, mock_time):
        mock_time.return_value = 100
        cb = CircuitBreaker(failure_threshold=3, reset_timeout=30)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()

        assert cb._failure_count == 3
        assert cb._state == CircuitState.OPEN
        assert cb._open_until == 130  # 100 + 30

    def test_record_failure_already_open(self):
        cb = CircuitBreaker()
        cb._state = CircuitState.OPEN
        cb._failure_count = 3
        cb.record_failure()
        # Should not change state, but count may increment
        assert cb._state == CircuitState.OPEN
        assert cb._failure_count == 4  # Incremented

    def test_record_failure_half_open(self):
        cb = CircuitBreaker()
        cb._state = CircuitState.HALF_OPEN
        cb.record_failure()
        assert cb._failure_count == 1
        assert cb._state == CircuitState.HALF_OPEN