from typing import Optional, Type, TypeVar
from llm_arch_sdk.adapters.base_llm_adapter import BaseLLMAdapter
from pydantic import BaseModel
import json

T = TypeVar("T", bound=BaseModel)


class LLMRunnable:
    def __init__(
        self,
        adapter: BaseLLMAdapter,
        output_model: Optional[Type[BaseModel]] = None,
    ):
        self._adapter = adapter
        self._output_model = output_model

    # ---------
    # Main API
    # ---------

    def invoke(self, input: dict) -> BaseModel:
        """
        Invoca el adapter con los parámetros proporcionados.
        
        Args:
            input: Dict que DEBE contener:
                - messages: Lista de mensajes (requerido)
                - **kwargs: Cualquier otro parámetro (temperature, max_tokens, etc.)
        """
        # Extraer messages (requerido)
        messages = input.get("messages")
        if not messages:
            raise ValueError("'messages' is required in input")
        
        # Inyectar schema si es structured output
        if self._output_model:
            messages = self._inject_schema_prompt(messages)
        
        # Obtener todos los demás parámetros (temperature, max_tokens, etc.)
        # Excluir 'messages' que ya fue extraído
        extra_params = {k: v for k, v in input.items() if k != "messages"}
        
        # Pasar todo al adapter
        response = self._adapter.chat(
            messages=messages,
            **extra_params  # temperature, max_tokens, top_p, etc.
        )

        if not self._output_model:
            return response

        return self._parse_structured_output(response.choices[0].message)

    # ---------
    # Internals
    # ---------

    def _inject_schema_prompt(self, messages):

        schema = self._output_model.model_json_schema()

        system = {
            "role": "system",
            "content": (
                "You must return ONLY a valid JSON object that matches "
                "the following JSON schema.\n\n"
                f"{json.dumps(schema, indent=2)}"
            ),
        }

        return [system, *messages]

    def _parse_structured_output(self, response):

        # adapta esto a tu modelo real de respuesta
        # ej: response.content / response.text / response.choices[0].message.content
        raw_text = response.content

        try:
            data = json.loads(raw_text)
        except Exception as e:
            raise ValueError(
                "LLM did not return valid JSON for structured output"
            ) from e

        return self._output_model.model_validate(data)