import pytest
from unittest.mock import Mock
from axonium.client.chat_completions import ChatCompletions
from axonium.models.chat_completion import ChatCompletionResult


class TestChatCompletions:
    def test_init(self):
        mock_client = Mock()
        chat = ChatCompletions(mock_client)
        assert chat._client == mock_client

    def test_create_basic(self):
        mock_client = Mock()
        mock_response = {
            "id": "chat_123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-3.5-turbo",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello, how can I help you?"
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30
            }
        }
        mock_client._request.return_value = mock_response

        chat = ChatCompletions(mock_client)
        result = chat.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}]
        )

        assert isinstance(result, ChatCompletionResult)
        assert result.id == "chat_123"
        mock_client._request.assert_called_once_with(
            "POST",
            "/llm/chat/completions",
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "Hello"}]
            }
        )

    def test_create_with_kwargs(self):
        mock_client = Mock()
        mock_response = {
            "id": "chat_456",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-4",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Response"
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 15,
                "completion_tokens": 25,
                "total_tokens": 40
            }
        }
        mock_client._request.return_value = mock_response

        chat = ChatCompletions(mock_client)
        result = chat.create(
            model="gpt-4",
            messages=[{"role": "user", "content": "Test"}],
            temperature=0.7,
            max_tokens=100
        )

        assert result.id == "chat_456"
        mock_client._request.assert_called_once_with(
            "POST",
            "/llm/chat/completions",
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Test"}],
                "temperature": 0.7,
                "max_tokens": 100
            }
        )

    def test_create_empty_messages(self):
        mock_client = Mock()
        mock_response = {
            "id": "chat_empty",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-3.5-turbo",
            "choices": [],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
        }
        mock_client._request.return_value = mock_response

        chat = ChatCompletions(mock_client)
        result = chat.create(model="gpt-3.5-turbo", messages=[])

        assert result.id == "chat_empty"
        assert len(result.choices) == 0

    def test_create_request_error(self):
        mock_client = Mock()
        mock_client._request.side_effect = Exception("API Error")

        chat = ChatCompletions(mock_client)
        with pytest.raises(Exception, match="API Error"):
            chat.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": "Hello"}]
            )