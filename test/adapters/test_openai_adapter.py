import pytest
import os
import httpx
from unittest.mock import Mock, patch, ANY, MagicMock
from openai import OpenAI
from llm_arch_sdk.adapters.open_ai_adapter import OpenAIAdapter
from llm_arch_sdk.config.settings import SdkSettings, LlmBackendEnv


class TestOpenAIAdapter:
    @patch('llm_arch_sdk.adapters.open_ai_adapter.AuthHttpClientFactory')
    @patch('llm_arch_sdk.adapters.open_ai_adapter.OpenAI')
    def test_init_with_env_var(self, mock_openai_class, mock_auth_factory):
        mock_http_client = MagicMock(spec=httpx.Client)
        mock_auth_factory.create.return_value = mock_http_client

        settings = SdkSettings()
        settings.llm = LlmBackendEnv(base_url='https://api.openai.com')
        adapter = OpenAIAdapter(model="test-model", settings=settings)

        assert adapter.base_url == 'https://api.openai.com'
        assert adapter.timeout == 60.0
        mock_auth_factory.create.assert_called_once_with(
            timeout=60.0
        )

    @patch('llm_arch_sdk.adapters.open_ai_adapter.OpenAI')
    def test_init_with_custom_base_url(self, mock_openai_class):
        with patch('llm_arch_sdk.adapters.open_ai_adapter.AuthHttpClientFactory') as mock_auth_factory:

            mock_http_client = MagicMock(spec=httpx.Client)
            mock_auth_factory.create.return_value = mock_http_client

            adapter = OpenAIAdapter(model="test-model", base_url="https://custom.openai.com", timeout=30.0)

            assert adapter.base_url == "https://custom.openai.com"
            assert adapter.timeout == 30.0
            mock_auth_factory.create.assert_called_once_with(
                timeout=30.0
            )

    def test_init_missing_base_url(self):
        settings = SdkSettings()
        settings.llm = LlmBackendEnv(base_url=None)
        with pytest.raises(RuntimeError, match="LLM_BASE_URL no está configurado"):
            OpenAIAdapter(model="test-model", settings=settings)

    def test_init_invalid_base_url(self):
        settings = SdkSettings()
        settings.llm = LlmBackendEnv(base_url='invalid-url')
        with pytest.raises(RuntimeError, match="LLM_BASE_URL inválida"):
            OpenAIAdapter(model="test-model", settings=settings)

    def test_init_non_http_base_url(self):
        settings = SdkSettings()
        settings.llm = LlmBackendEnv(base_url='ftp://invalid.com')
        with pytest.raises(RuntimeError, match="LLM_BASE_URL inválida"):
            OpenAIAdapter(model="test-model", settings=settings)

    @patch('llm_arch_sdk.adapters.open_ai_adapter.AuthHttpClientFactory')
    @patch('llm_arch_sdk.adapters.open_ai_adapter.OpenAI')
    def test_client_lazy_initialization(self, mock_openai_class, mock_auth_factory):
        mock_http_client = MagicMock(spec=httpx.Client)
        mock_auth_factory.create.return_value = mock_http_client
        mock_auth_factory._default_headers.return_value = {"User-Agent": "test"}

        mock_openai_instance = Mock(spec=OpenAI)
        mock_openai_class.return_value = mock_openai_instance

        settings = SdkSettings()
        settings.llm = LlmBackendEnv(base_url='https://api.openai.com')
        adapter = OpenAIAdapter(model="test-model", settings=settings)
        client1 = adapter.client()
        client2 = adapter.client()

        # Should return the same instance
        assert client1 is mock_openai_instance
        assert client2 is mock_openai_instance

        # OpenAI should be instantiated only once (in __init__)
        mock_openai_class.assert_called_once()

    @patch('llm_arch_sdk.adapters.open_ai_adapter.AuthHttpClientFactory')
    @patch('llm_arch_sdk.adapters.open_ai_adapter.OpenAI')
    def test_client_initialization_params(self, mock_openai_class, mock_auth_factory):
        mock_http_client = MagicMock(spec=httpx.Client)
        mock_auth_factory.create.return_value = mock_http_client
        mock_auth_factory._default_headers.return_value = {"User-Agent": "test"}

        adapter = OpenAIAdapter(model="test-model", base_url="https://test.api.com", timeout=45.0)
        adapter.client()

        # Verify OpenAI was called with correct base_url
        args, kwargs = mock_openai_class.call_args
        assert kwargs['base_url'] == "https://test.api.com"