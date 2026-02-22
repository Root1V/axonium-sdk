"""
MiniAgent - Abstracción para crear nodos/agentes LLM reutilizables en workflows (ej: LangGraph).

Encapsula el patrón común:
    1. Extraer datos del state
    2. Construir prompt
    3. Invocar LLM con structured output
    4. Retornar actualización del state
"""

import logging
import time
from typing import Callable, Type, Any, Dict
from pydantic import BaseModel

from ..adapters.base_llm_adapter import BaseLLMAdapter
from .llm_runnable import LLMRunnable
from ..observability.bootstrap import observe
from ..observability.context import obs

logger = logging.getLogger("llm.integrations.agent")



class MiniAgent:
    """
    Un agente LLM configurable que puede usarse como nodo en workflows (ej: LangGraph).
    
    Simplifica la creación de nodos eliminando código repetitivo y aplicando
    trazabilidad automática con el nombre del agente.
    
    Ejemplo:
        def build_prompt(state):
            return f"Analiza: {state['text']}"
        
        agent = MiniAgent(
            adapter=BaseLLMAdapter,
            name="analyzer",
            output_model=AnalysisResult,
            prompt_builder=build_prompt,
            temperature=0.7
        )
        
        # Usar como nodo en LangGraph
        graph.add_node("analyzer", agent)
    """
    
    def __init__(
        self,
        adapter: BaseLLMAdapter,
        name: str,
        output_model: Type[BaseModel],
        prompt_builder: Callable[[Dict[str, Any]], str],
        **llm_params
    ):
        """
        Args:
            adapter: Adapter del LLM (ej: LlamaAdapter)
            name: Nombre del nodo/agente (aparecerá en Langfuse como "agent.{name}")
            output_model: Modelo Pydantic para structured output
            prompt_builder: Función que toma el state y retorna el prompt (str)
            **llm_params: Parámetros del LLM (temperature, max_tokens, etc.)
        """
        self._adapter = adapter
        self._name = name
        self._output_model = output_model
        self._prompt_builder = prompt_builder
        self._llm_params = llm_params
        
        logger.debug(
            "MiniAgent initialized [name=%s, adapter=%s, output_model=%s, llm_params=%s]",
            name,
            adapter.__class__.__name__,
            output_model.__name__,
            list(llm_params.keys())
        )
        
        # Crear el callable decorado que será invocado
        self._decorated_execute = observe(name=f"agent.{self._name}")(self._execute)
    
    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Hace que la instancia sea callable - requerido para LangGraph.
        Delega al método decorado con observabilidad.
        """
        logger.debug(
            "MiniAgent invoked [name=%s, state_keys=%s]",
            self._name,
            list(state.keys())
        )
        return self._decorated_execute(state)
    
    def _execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta el agente: construye prompt, invoca LLM, retorna actualización del state.
        
        Args:
            state: Estado del workflow (dict)
            
        Returns:
            Dict con una clave (self._name) conteniendo el resultado
        """
        start_time = time.time()
        
        try:
            # 1. Construir prompt usando state
            logger.debug(
                "Building prompt [agent=%s, prompt_builder=%s]",
                self._name,
                self._prompt_builder.__name__
            )
            prompt = self._prompt_builder(state)
            prompt_length = len(prompt)
            
            logger.debug(
                "Prompt built successfully [agent=%s, prompt_length=%d]",
                self._name,
                prompt_length
            )
            
            # 2. Crear runnable y ejecutar
            llm = LLMRunnable(adapter=self._adapter, output_model=self._output_model)
            result = llm.invoke({
                "messages": [{"role": "user", "content": prompt}],
                **self._llm_params
            })
            
            # 3. Retornar actualización del state
            state_update = {self._name: result.model_dump()}
            
            elapsed_ms = (time.time() - start_time) * 1000
            logger.info(
                "Agent execution completed [agent=%s, duration_ms=%.2f, output_fields=%s]",
                self._name,
                elapsed_ms,
                list(result.model_dump().keys())
            )
            
            return state_update
            
        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            logger.error(
                "Agent execution failed [agent=%s, duration_ms=%.2f, error_type=%s, error=%s]",
                self._name,
                elapsed_ms,
                type(e).__name__,
                str(e)
            )
            raise
