import pytest
import json
from unittest.mock import Mock, MagicMock
from pydantic import BaseModel, Field, ValidationError

from llm_arch_sdk.integrations.llm_runnable import LLMRunnable
from llm_arch_sdk.adapters.base_llm_adapter import BaseLLMAdapter


class TestModel(BaseModel):
    """Test model for structured output"""
    name: str = Field(description="Name field")
    value: int = Field(description="Value field")


class TestLLMRunnable:
    
    def test_init_without_output_model(self):
        """Test initialization without structured output"""
        adapter = Mock(spec=BaseLLMAdapter)
        
        runnable = LLMRunnable(adapter=adapter)
        
        assert runnable._adapter == adapter
        assert runnable._output_model is None
    
    def test_init_with_output_model(self):
        """Test initialization with structured output"""
        adapter = Mock(spec=BaseLLMAdapter)
        
        runnable = LLMRunnable(adapter=adapter, output_model=TestModel)
        
        assert runnable._adapter == adapter
        assert runnable._output_model == TestModel
    
    def test_invoke_without_messages_raises_error(self):
        """Test that invoke fails if messages are missing"""
        adapter = Mock(spec=BaseLLMAdapter)
        runnable = LLMRunnable(adapter=adapter)
        
        with pytest.raises(ValueError, match="'messages' is required"):
            runnable.invoke({})
    
    def test_invoke_without_structured_output(self):
        """Test invoke returns raw response when no output_model"""
        adapter = Mock(spec=BaseLLMAdapter)
        mock_response = Mock()
        adapter.chat.return_value = mock_response
        
        runnable = LLMRunnable(adapter=adapter)
        
        messages = [{"role": "user", "content": "Hello"}]
        result = runnable.invoke({"messages": messages})
        
        assert result == mock_response
        adapter.chat.assert_called_once_with(messages=messages)
    
    def test_invoke_with_extra_params(self):
        """Test that extra parameters are passed to adapter"""
        adapter = Mock(spec=BaseLLMAdapter)
        mock_response = Mock()
        adapter.chat.return_value = mock_response
        
        runnable = LLMRunnable(adapter=adapter)
        
        messages = [{"role": "user", "content": "Test"}]
        runnable.invoke({
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 100,
            "top_p": 0.95
        })
        
        adapter.chat.assert_called_once_with(
            messages=messages,
            temperature=0.7,
            max_tokens=100,
            top_p=0.95
        )
    
    def test_schema_injection(self):
        """Test that JSON schema is injected for structured output"""
        adapter = Mock(spec=BaseLLMAdapter)
        mock_response = Mock()
        mock_message = Mock()
        mock_message.content = '{"name": "test", "value": 42}'
        mock_response.choices = [Mock(message=mock_message)]
        adapter.chat.return_value = mock_response
        
        runnable = LLMRunnable(adapter=adapter, output_model=TestModel)
        
        messages = [{"role": "user", "content": "Generate data"}]
        runnable.invoke({"messages": messages})
        
        # Verify system message with schema was injected
        call_args = adapter.chat.call_args
        called_messages = call_args[1]["messages"]
        
        assert len(called_messages) == 2  # system + user
        assert called_messages[0]["role"] == "system"
        assert "JSON" in called_messages[0]["content"]
        assert "schema" in called_messages[0]["content"].lower()
    
    def test_structured_output_parsing_success(self):
        """Test successful parsing of structured output"""
        adapter = Mock(spec=BaseLLMAdapter)
        mock_response = Mock()
        mock_message = Mock()
        mock_message.content = '{"name": "John", "value": 123}'
        mock_response.choices = [Mock(message=mock_message)]
        adapter.chat.return_value = mock_response
        
        runnable = LLMRunnable(adapter=adapter, output_model=TestModel)
        
        messages = [{"role": "user", "content": "Test"}]
        result = runnable.invoke({"messages": messages})
        
        assert isinstance(result, TestModel)
        assert result.name == "John"
        assert result.value == 123
    
    def test_structured_output_invalid_json(self):
        """Test error handling for invalid JSON""" 
        adapter = Mock(spec=BaseLLMAdapter)
        mock_response = Mock()
        mock_message = Mock()
        mock_message.content = 'This is not JSON'
        mock_response.choices = [Mock(message=mock_message)]
        adapter.chat.return_value = mock_response
        
        runnable = LLMRunnable(adapter=adapter, output_model=TestModel)
        
        messages = [{"role": "user", "content": "Test"}]
        
        with pytest.raises(ValueError, match="LLM did not return valid JSON"):
            runnable.invoke({"messages": messages})
    
    def test_structured_output_validation_error(self):
        """Test error handling for schema validation failure"""
        adapter = Mock(spec=BaseLLMAdapter)
        mock_response = Mock()
        mock_message = Mock()
        # Missing required 'value' field
        mock_message.content = '{"name": "test"}'
        mock_response.choices = [Mock(message=mock_message)]
        adapter.chat.return_value = mock_response
        
        runnable = LLMRunnable(adapter=adapter, output_model=TestModel)
        
        messages = [{"role": "user", "content": "Test"}]
        
        with pytest.raises(ValueError, match="LLM response does not match expected schema"):
            runnable.invoke({"messages": messages})
    
    def test_system_prompt_format_instructions(self):
        """Test that system prompt includes formatting instructions"""
        adapter = Mock(spec=BaseLLMAdapter)
        mock_response = Mock()
        mock_message = Mock()
        mock_message.content = '{"name": "test", "value": 1}'
        mock_response.choices = [Mock(message=mock_message)]
        adapter.chat.return_value = mock_response
        
        runnable = LLMRunnable(adapter=adapter, output_model=TestModel)
        
        messages = [{"role": "user", "content": "Test"}]
        runnable.invoke({"messages": messages})
        
        # Check system prompt includes critical formatting rules
        call_args = adapter.chat.call_args
        system_content = call_args[1]["messages"][0]["content"]
        
        assert "CRITICAL RULES" in system_content
        assert "compact format" in system_content
        assert "markdown code blocks" in system_content
        assert "ESCAPE" in system_content
    
    def test_json_with_code_escaping(self):
        """Test parsing JSON with escaped code (like docstrings)"""
        adapter = Mock(spec=BaseLLMAdapter)
        mock_response = Mock()
        mock_message = Mock()
        # JSON with escaped quotes (as if from Python code with docstrings)
        mock_message.content = '{"name": "def foo():\\n    \\"\\"\\"Doc\\"\\"\\"\\n    pass", "value": 99}'
        mock_response.choices = [Mock(message=mock_message)]
        adapter.chat.return_value = mock_response
        
        runnable = LLMRunnable(adapter=adapter, output_model=TestModel)
        
        messages = [{"role": "user", "content": "Test"}]
        result = runnable.invoke({"messages": messages})
        
        assert isinstance(result, TestModel)
        assert '"""' in result.name  # Docstring quotes should be unescaped
        assert result.value == 99
    
    def test_adapter_error_propagation(self):
        """Test that adapter errors are propagated"""
        adapter = Mock(spec=BaseLLMAdapter)
        adapter.chat.side_effect = RuntimeError("LLM API error")
        
        runnable = LLMRunnable(adapter=adapter)
        
        messages = [{"role": "user", "content": "Test"}]
        
        with pytest.raises(RuntimeError, match="LLM API error"):
            runnable.invoke({"messages": messages})
    
    def test_multiple_invocations(self):
        """Test that runnable can be used multiple times"""
        adapter = Mock(spec=BaseLLMAdapter)
        
        call_counter = [0]  # Use list to allow mutation in nested function
        
        def mock_chat(**kwargs):
            mock_response = Mock()
            mock_message = Mock()
            # Return different values based on call counter
            if call_counter[0] == 0:
                mock_message.content = '{"name": "first", "value": 1}'
            else:
                mock_message.content = '{"name": "second", "value": 2}'
            call_counter[0] += 1
            mock_response.choices = [Mock(message=mock_message)]
            return mock_response
        
        adapter.chat = Mock(side_effect=mock_chat)
        
        runnable = LLMRunnable(adapter=adapter, output_model=TestModel)
        
        # First invocation
        result1 = runnable.invoke({"messages": [{"role": "user", "content": "First"}]})
        assert result1.name == "first"
        assert result1.value == 1
        
        # Second invocation
        result2 = runnable.invoke({"messages": [{"role": "user", "content": "Second"}]})
        assert result2.name == "second"
        assert result2.value == 2
