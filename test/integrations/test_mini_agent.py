import pytest
from unittest.mock import Mock, MagicMock, patch
from pydantic import BaseModel, Field
from typing import Dict, Any

from axonium.integrations.agent import MiniAgent
from axonium.adapters.base_llm_adapter import BaseLLMAdapter


class TestOutputModel(BaseModel):
    """Test model for structured output"""
    result: str = Field(description="The result")
    score: int = Field(description="A score")


class TestMiniAgent:
    
    def test_init(self):
        """Test MiniAgent initialization"""
        adapter = Mock(spec=BaseLLMAdapter)
        prompt_builder = Mock(return_value="test prompt")
        
        agent = MiniAgent(
            adapter=adapter,
            name="test_agent",
            output_model=TestOutputModel,
            prompt_builder=prompt_builder,
            temperature=0.7,
            max_tokens=100
        )
        
        assert agent._name == "test_agent"
        assert agent._adapter == adapter
        assert agent._output_model == TestOutputModel
        assert agent._prompt_builder == prompt_builder
        assert agent._llm_params == {"temperature": 0.7, "max_tokens": 100}
    
    def test_prompt_building(self):
        """Test that prompt builder is called with state"""
        adapter = Mock(spec=BaseLLMAdapter)
        prompt_builder = Mock(return_value="Generated prompt")
        prompt_builder.__name__ = "test_prompt_builder"
        
        # Mock the LLMRunnable
        with patch('axonium.integrations.agent.LLMRunnable') as mock_runnable_class:
            mock_runnable = Mock()
            mock_result = Mock()
            mock_result.model_dump.return_value = {"result": "test", "score": 10}
            mock_runnable.invoke.return_value = mock_result
            mock_runnable_class.return_value = mock_runnable
            
            agent = MiniAgent(
                adapter=adapter,
                name="test_agent",
                output_model=TestOutputModel,
                prompt_builder=prompt_builder
            )
            
            state = {"data": "test_data"}
            result = agent(state)
            
            # Verify prompt builder was called with state
            prompt_builder.assert_called_once_with(state)
    
    def test_agent_execution_flow(self):
        """Test complete agent execution flow"""
        adapter = Mock(spec=BaseLLMAdapter)
        
        def build_prompt(state):
            return f"Process: {state['input']}"
        
        # Mock LLMRunnable
        with patch('axonium.integrations.agent.LLMRunnable') as mock_runnable_class:
            mock_runnable = Mock()
            mock_result = Mock()
            mock_result.model_dump.return_value = {"result": "processed", "score": 95}
            mock_runnable.invoke.return_value = mock_result
            mock_runnable_class.return_value = mock_runnable
            
            agent = MiniAgent(
                adapter=adapter,
                name="processor",
                output_model=TestOutputModel,
                prompt_builder=build_prompt,
                temperature=0.5
            )
            
            state = {"input": "test input"}
            result = agent(state)
            
            # Verify result structure
            assert "processor" in result
            assert result["processor"]["result"] == "processed"
            assert result["processor"]["score"] == 95
            
            # Verify LLMRunnable was called correctly
            mock_runnable.invoke.assert_called_once()
            call_args = mock_runnable.invoke.call_args[0][0]
            assert "messages" in call_args
            assert call_args["messages"][0]["role"] == "user"
            assert call_args["messages"][0]["content"] == "Process: test input"
            assert call_args["temperature"] == 0.5
    
    def test_llm_params_passed_correctly(self):
        """Test that LLM params are passed to invoke"""
        adapter = Mock(spec=BaseLLMAdapter)
        prompt_builder = Mock(return_value="test")
        prompt_builder.__name__ = "test_prompt_builder"
        
        with patch('axonium.integrations.agent.LLMRunnable') as mock_runnable_class:
            mock_runnable = Mock()
            mock_result = Mock()
            mock_result.model_dump.return_value = {"result": "ok", "score": 1}
            mock_runnable.invoke.return_value = mock_result
            mock_runnable_class.return_value = mock_runnable
            
            agent = MiniAgent(
                adapter=adapter,
                name="test",
                output_model=TestOutputModel,
                prompt_builder=prompt_builder,
                temperature=0.9,
                max_tokens=500,
                top_p=0.95
            )
            
            agent({"test": "data"})
            
            # Verify all params were passed
            call_args = mock_runnable.invoke.call_args[0][0]
            assert call_args["temperature"] == 0.9
            assert call_args["max_tokens"] == 500
            assert call_args["top_p"] == 0.95
    
    def test_agent_as_callable(self):
        """Test that MiniAgent instances are callable"""
        adapter = Mock(spec=BaseLLMAdapter)
        prompt_builder = Mock(return_value="prompt")
        
        with patch('axonium.integrations.agent.LLMRunnable'):
            agent = MiniAgent(
                adapter=adapter,
                name="test",
                output_model=TestOutputModel,
                prompt_builder=prompt_builder
            )
            
            # Should be callable (for LangGraph compatibility)
            assert callable(agent)
    
    def test_error_handling(self):
        """Test error handling in agent execution"""
        adapter = Mock(spec=BaseLLMAdapter)
        prompt_builder = Mock(return_value="prompt")
        prompt_builder.__name__ = "test_prompt_builder"
        
        with patch('axonium.integrations.agent.LLMRunnable') as mock_runnable_class:
            mock_runnable = Mock()
            mock_runnable.invoke.side_effect = ValueError("LLM error")
            mock_runnable_class.return_value = mock_runnable
            
            agent = MiniAgent(
                adapter=adapter,
                name="test",
                output_model=TestOutputModel,
                prompt_builder=prompt_builder
            )
            
            with pytest.raises(ValueError, match="LLM error"):
                agent({"test": "data"})
    
    def test_state_update_format(self):
        """Test that state update uses agent name as key"""
        adapter = Mock(spec=BaseLLMAdapter)
        prompt_builder = Mock(return_value="prompt")
        prompt_builder.__name__ = "test_prompt_builder"
        
        with patch('axonium.integrations.agent.LLMRunnable') as mock_runnable_class:
            mock_runnable = Mock()
            mock_result = Mock()
            mock_result.model_dump.return_value = {"result": "test", "score": 42}
            mock_runnable.invoke.return_value = mock_result
            mock_runnable_class.return_value = mock_runnable
            
            agent = MiniAgent(
                adapter=adapter,
                name="custom_name",
                output_model=TestOutputModel,
                prompt_builder=prompt_builder
            )
            
            result = agent({"input": "data"})
            
            # Result should have agent name as key
            assert list(result.keys()) == ["custom_name"]
            assert result["custom_name"] == {"result": "test", "score": 42}
