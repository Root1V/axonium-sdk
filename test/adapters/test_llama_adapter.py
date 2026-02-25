import pytest
import os
from unittest.mock import Mock, patch
from axonium.adapters.llama_adapter import LlamaAdapter
from axonium.client.llm_client import LlmClient
from axonium.config.settings import SdkSettings, LlmBackendEnv


class TestLlamaAdapter:
    @patch('axonium.adapters.llama_adapter.AuthHttpClientFactory')
    def test_init_with_env_var(self, mock_auth_factory):
        mock_http_client = Mock()
        mock_auth_factory.create.return_value = mock_http_client

        settings = SdkSettings()
        settings.llm = LlmBackendEnv(base_url='http://localhost:8000')
        adapter = LlamaAdapter(model="test-model", settings=settings)

        assert adapter.base_url == 'http://localhost:8000'
        assert adapter.timeout == 60.0
        mock_auth_factory.create.assert_called_once_with(
            timeout=60.0
        )

    def test_init_with_custom_base_url(self):
        with patch('axonium.adapters.llama_adapter.AuthHttpClientFactory') as mock_auth_factory:

            mock_http_client = Mock()
            mock_auth_factory.create.return_value = mock_http_client

            adapter = LlamaAdapter(model="test-model", base_url="http://custom:9000", timeout=30.0)

            assert adapter.base_url == "http://custom:9000"
            assert adapter.timeout == 30.0
            mock_auth_factory.create.assert_called_once_with(
                timeout=30.0
            )

    def test_init_missing_base_url(self):
        settings = SdkSettings()
        settings.llm = LlmBackendEnv(base_url=None)
        with pytest.raises(RuntimeError, match="LLM_BASE_URL no configurada"):
            LlamaAdapter(model="test-model", settings=settings)

    @patch('axonium.adapters.llama_adapter.AuthHttpClientFactory')
    @patch('axonium.adapters.llama_adapter.LlmClient')
    def test_client_lazy_initialization(self, mock_llm_client_class, mock_auth_factory):
        mock_http_client = Mock()
        mock_auth_factory.create.return_value = mock_http_client

        mock_llm_client_instance = Mock(spec=LlmClient)
        mock_llm_client_class.return_value = mock_llm_client_instance

        settings = SdkSettings()
        settings.llm = LlmBackendEnv(base_url='http://localhost:8000')
        adapter = LlamaAdapter(model="test-model", settings=settings)
        client1 = adapter.client()
        client2 = adapter.client()

        # Should return the same instance
        assert client1 is mock_llm_client_instance
        assert client2 is mock_llm_client_instance

        # LlmClient should be instantiated only once
        mock_llm_client_class.assert_called_once_with(
            base_url='http://localhost:8000',
            http_client=mock_http_client
        )

    @patch.dict(os.environ, {'LLM_BASE_URL': 'http://localhost:8000'}, clear=True)
    @patch('axonium.adapters.llama_adapter.AuthHttpClientFactory')
    @patch('axonium.adapters.llama_adapter.LlmClient')
    def test_client_initialization_params(self, mock_llm_client_class, mock_auth_factory):
        mock_http_client = Mock()
        mock_auth_factory.create.return_value = mock_http_client

        adapter = LlamaAdapter(model="test-model", base_url="http://test:8080", timeout=45.0)
        adapter.client()

        mock_llm_client_class.assert_called_once_with(
            base_url="http://test:8080",
            http_client=mock_http_client
        )