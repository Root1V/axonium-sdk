import pytest
import httpx
from unittest.mock import Mock
from axonium.client.llm_client import LlmClient, LlmAPIError
from axonium.transport.circuit_breaker import CircuitBreakerOpen

@pytest.fixture
def mock_http_client():
    return Mock()

@pytest.fixture
def llm_client(mock_http_client):
    return LlmClient(base_url="http://localhost:8000", http_client=mock_http_client)


class TestLlmClientRequest:
    def test_request_success(self, llm_client, mock_http_client):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        mock_http_client.request.return_value = mock_response

        result = llm_client._request("GET", "/health")

        assert result == {"status": "ok"}
        mock_http_client.request.assert_called_once()

    def test_request_circuit_breaker_open(self, llm_client):
        llm_client._circuit.allow_request = Mock(return_value=False)

        with pytest.raises(CircuitBreakerOpen):
            llm_client._request("GET", "/health")

    def test_request_server_error(self, llm_client, mock_http_client):
        mock_response = Mock()
        mock_response.status_code = 500
        mock_http_client.request.return_value = mock_response

        with pytest.raises(LlmAPIError, match="Error 500"):
            llm_client._request("GET", "/health")

    def test_request_timeout_exception(self, llm_client, mock_http_client):
        mock_http_client.request.side_effect = httpx.TimeoutException("Timeout")

        with pytest.raises(LlmAPIError, match="Timeout"):
            llm_client._request("GET", "/health")

    def test_request_http_error(self, llm_client, mock_http_client):
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError("Not found", request=Mock(), response=mock_response)
        mock_http_client.request.return_value = mock_response

        with pytest.raises(LlmAPIError):
            llm_client._request("GET", "/health")

    def test_request_url_construction(self, llm_client, mock_http_client):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_http_client.request.return_value = mock_response

        llm_client._request("POST", "/chat", json={"message": "test"})

        mock_http_client.request.assert_called_once_with(
            "POST",
            "http://localhost:8000/chat",
            json={"message": "test"}
        )