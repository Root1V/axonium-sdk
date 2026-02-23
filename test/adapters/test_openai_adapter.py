import pytest
import os
from unittest.mock import Mock, patch, ANY
from openai import OpenAI
from llm_arch_sdk.adapters.open_ai_adapter import OpenAIAdapter


class TestOpenAIAdapter:
    @patch.dict(os.environ, {'LLM_BASE_URL': 'https://api.openai.com'}, clear=True)
    @patch('llm_arch_sdk.adapters.open_ai_adapter.AuthHttpClientFactory')
    def test_init_with_env_var(self, mock_auth_factory):
        mock_http_client = Mock()
        mock_auth_factory.create.return_value = mock_http_client

        adapter = OpenAIAdapter(use_langfuse=False)

        assert adapter.base_url == 'https://api.openai.com'
        assert adapter.timeout == 60.0
        mock_auth_factory.create.assert_called_once_with(
            timeout=60.0
        )

    def test_init_with_custom_base_url(self):
        with patch('llm_arch_sdk.adapters.open_ai_adapter.AuthHttpClientFactory') as mock_auth_factory:

            mock_http_client = Mock()
            mock_auth_factory.create.return_value = mock_http_client

            adapter = OpenAIAdapter(base_url="https://custom.openai.com", timeout=30.0, use_langfuse=False)

            assert adapter.base_url == "https://custom.openai.com"
            assert adapter.timeout == 30.0
            mock_auth_factory.create.assert_called_once_with(
                timeout=30.0
            )

    @patch.dict(os.environ, {}, clear=True)
    def test_init_missing_base_url(self):
        with pytest.raises(RuntimeError, match="LLM_BASE_URL no está configurado"):
            OpenAIAdapter()

    @patch.dict(os.environ, {'LLM_BASE_URL': 'invalid-url'}, clear=True)
    def test_init_invalid_base_url(self):
        with pytest.raises(RuntimeError, match="LLM_BASE_URL inválida: invalid-url"):
            OpenAIAdapter()

    @patch.dict(os.environ, {'LLM_BASE_URL': 'ftp://invalid.com'}, clear=True)
    def test_init_non_http_base_url(self):
        with pytest.raises(RuntimeError, match="LLM_BASE_URL inválida: ftp://invalid.com"):
            OpenAIAdapter()

    @patch.dict(os.environ, {'LLM_BASE_URL': 'https://api.openai.com'}, clear=True)
    @patch('llm_arch_sdk.adapters.open_ai_adapter.AuthHttpClientFactory')
    @patch('llm_arch_sdk.adapters.open_ai_adapter.OpenAI')
    def test_client_lazy_initialization(self, mock_openai_class, mock_auth_factory):
        mock_http_client = Mock()
        mock_auth_factory.create.return_value = mock_http_client

        mock_openai_instance = Mock(spec=OpenAI)
        mock_openai_class.return_value = mock_openai_instance

        adapter = OpenAIAdapter(use_langfuse=False)
        client1 = adapter.client()
        client2 = adapter.client()

        # Should return the same instance
        assert client1 is mock_openai_instance
        assert client2 is mock_openai_instance

        # OpenAI should be instantiated only once
        mock_openai_class.assert_called_once_with(
            base_url='https://api.openai.com',
            api_key='unused',
            http_client=mock_http_client,
            default_headers=ANY,
        )

    @patch.dict(os.environ, {'LLM_BASE_URL': 'https://custom.api.com'}, clear=True)
    @patch('llm_arch_sdk.adapters.open_ai_adapter.AuthHttpClientFactory')
    @patch('llm_arch_sdk.adapters.open_ai_adapter.OpenAI')
    def test_client_initialization_params(self, mock_openai_class, mock_auth_factory):
        mock_http_client = Mock()
        mock_auth_factory.create.return_value = mock_http_client

        adapter = OpenAIAdapter(base_url="https://test.api.com", timeout=45.0, use_langfuse=False)
        adapter.client()

        mock_openai_class.assert_called_once_with(
            base_url="https://test.api.com",
            api_key='unused',
            http_client=mock_http_client,
            default_headers=ANY,
        )