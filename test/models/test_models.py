from axonium.models.chat_completion import ChatMessage, ChatChoice, ChatCompletionResult
from axonium.models.completion import CompletionResult
from axonium.models.usage import Usage
from axonium.models.timings import Timings
from axonium.models.stop_type import StopType
from axonium.models.generation_settings import GenerationSettings


class TestChatCompletionModels:
    def test_chat_message_from_dict(self):
        data = {"role": "user", "content": "Hello"}
        msg = ChatMessage.from_dict(data)
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_chat_message_from_dict_missing_content(self):
        data = {"role": "assistant"}
        msg = ChatMessage.from_dict(data)
        assert msg.role == "assistant"
        assert msg.content == ""

    def test_chat_choice_from_dict(self):
        data = {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "Response"}
        }
        choice = ChatChoice.from_dict(data)
        assert choice.index == 0
        assert choice.finish_reason == "stop"
        assert choice.message.role == "assistant"
        assert choice.message.content == "Response"

    def test_chat_choice_from_dict_missing_index(self):
        data = {
            "finish_reason": "length",
            "message": {"role": "assistant", "content": "Response"}
        }
        choice = ChatChoice.from_dict(data)
        assert choice.index == 0
        assert choice.finish_reason == "length"

    def test_chat_completion_result_from_dict(self):
        data = {
            "id": "chat_123",
            "model": "gpt-3.5-turbo",
            "created": 1234567890,
            "object": "chat.completion",
            "system_fingerprint": "fp_123",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Hello"}
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30
            },
            "timings": {
                "prompt_ms": 100,
                "predicted_ms": 200,
                "predicted_n": 50,
                "predicted_per_token_ms": 4.0,
                "predicted_per_second": 250.0
            }
        }
        result = ChatCompletionResult.from_dict(data)
        assert result.id == "chat_123"
        assert result.model == "gpt-3.5-turbo"
        assert result.created == 1234567890
        assert result.object == "chat.completion"
        assert result.system_fingerprint == "fp_123"
        assert len(result.choices) == 1
        assert result.choices[0].message.content == "Hello"
        assert result.usage.prompt_tokens == 10
        assert result.timings.prompt_ms == 100

    def test_chat_completion_result_from_dict_minimal(self):
        data = {
            "id": "chat_min",
            "model": "gpt-4",
            "created": 1234567890,
            "choices": []
        }
        result = ChatCompletionResult.from_dict(data)
        assert result.id == "chat_min"
        assert result.choices == []
        assert result.usage is None
        assert result.timings is None


class TestCompletionModels:
    def test_completion_result_from_dict(self):
        data = {
            "index": 0,
            "content": "Response text",
            "model": "llama-7b",
            "stop": True,
            "tokens_predicted": 15,
            "tokens_evaluated": 5,
            "prompt": "Test prompt"
        }
        result = CompletionResult.from_dict(data)
        assert result.index == 0
        assert result.content == "Response text"
        assert result.model == "llama-7b"
        assert result.tokens_predicted == 15


class TestUsage:
    def test_usage_from_dict(self):
        data = {
            "prompt_tokens": 100,
            "completion_tokens": 200,
            "total_tokens": 300
        }
        usage = Usage.from_dict(data)
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 200
        assert usage.total_tokens == 300


class TestTimings:
    def test_timings_from_dict(self):
        data = {
            "prompt_ms": 150,
            "predicted_ms": 300,
            "predicted_n": 75,
            "predicted_per_token_ms": 4.0,
            "predicted_per_second": 250.0
        }
        timings = Timings.from_dict(data)
        assert timings.prompt_ms == 150
        assert timings.predicted_ms == 300
        assert timings.predicted_n == 75
        assert timings.predicted_per_token_ms == 4.0
        assert timings.predicted_per_second == 250.0


class TestStopType:
    def test_stop_type_values(self):
        assert StopType.EOS == "eos"
        assert StopType.LIMIT == "limit"
        assert StopType.STOP_WORD == "stop"


class TestGenerationSettings:
    def test_generation_settings_from_dict(self):
        data = {
            "temperature": 0.8,
            "top_p": 0.9,
            "top_k": 50,
            "max_tokens": 100,
            "repeat_penalty": 1.1,
            "repeat_last_n": 64,
            "seed": 42
        }
        settings = GenerationSettings.from_dict(data)
        assert settings.temperature == 0.8
        assert settings.top_p == 0.9
        assert settings.top_k == 50
        assert settings.max_tokens == 100
        assert settings.repeat_penalty == 1.1
        assert settings.repeat_last_n == 64
        assert settings.seed == 42

    def test_generation_settings_from_dict_defaults(self):
        data = {}
        settings = GenerationSettings.from_dict(data)
        assert settings.temperature is None
        assert settings.top_p is None
        assert settings.top_k is None
        assert settings.max_tokens is None
        assert settings.seed is None
        assert settings.repeat_last_n is None