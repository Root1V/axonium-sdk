import pytest
from unittest.mock import Mock
from axonium.client.completions import Completions
from axonium.models.completion import CompletionResult


class TestCompletions:
    def test_init(self):
        mock_client = Mock()
        completions = Completions(mock_client)
        assert completions._client == mock_client

    def test_create_basic(self):
        mock_client = Mock()
        mock_response = {
            "index": 0,
            "content": "This is a completion response.",
            "model": "llama-7b",
            "stop": True,
            "tokens_predicted": 5,
            "tokens_evaluated": 5,
            "prompt": "Hello world"
        }
        mock_client._request.return_value = mock_response

        completions = Completions(mock_client)
        result = completions.create(prompt="Hello world", temperature=0.7, n_predict=100)

        assert isinstance(result, CompletionResult)
        assert result.index == 0
        assert result.content == "This is a completion response."
        mock_client._request.assert_called_once_with(
            "POST",
            "/llm/completions",
            json={
                "prompt": "Hello world",
                "temperature": 0.7,
                "n_predict": 100
            }
        )

    def test_create_with_custom_params(self):
        mock_client = Mock()
        mock_response = {
            "index": 0,
            "content": "Custom response",
            "model": "llama-13b",
            "stop": False,
            "tokens_predicted": 25,
            "tokens_evaluated": 8,
            "prompt": "Test prompt"
        }
        mock_client._request.return_value = mock_response

        completions = Completions(mock_client)
        result = completions.create(
            prompt="Test prompt",
            temperature=0.5,
            n_predict=200,
            stop=["\n"]
        )

        assert result.index == 0
        assert result.content == "Custom response"
        assert result.stop == False
        mock_client._request.assert_called_once_with(
            "POST",
            "/llm/completions",
            json={
                "prompt": "Test prompt",
                "temperature": 0.5,
                "n_predict": 200,
                "stop": ["\n"]
            }
        )

    def test_create_empty_prompt(self):
        mock_client = Mock()
        mock_response = {
            "index": 0,
            "content": "",
            "model": "llama-7b",
            "stop": True,
            "tokens_predicted": 0,
            "tokens_evaluated": 0,
            "prompt": ""
        }
        mock_client._request.return_value = mock_response

        completions = Completions(mock_client)
        result = completions.create(prompt="", temperature=0.7)

        assert result.index == 0
        assert result.content == ""

    def test_create_request_error(self):
        mock_client = Mock()
        mock_client._request.side_effect = Exception("Network Error")

        completions = Completions(mock_client)
        with pytest.raises(Exception, match="Network Error"):
            completions.create(prompt="Test", temperature=0.7)