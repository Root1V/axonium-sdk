import logging
import time
from typing import Optional, Type, TypeVar
from pydantic import BaseModel, ValidationError
import json

from ..adapters.base_llm_adapter import BaseLLMAdapter


logger = logging.getLogger("llm.integrations.runnable")

T = TypeVar("T", bound=BaseModel)

class LLMRunnable:
    def __init__(
        self,
        adapter: BaseLLMAdapter,
        output_model: Optional[Type[BaseModel]] = None,
    ):
        self._adapter = adapter
        self._output_model = output_model
        
        logger.debug(
            "LLMRunnable initialized [adapter=%s, structured_output=%s]",
            adapter.__class__.__name__,
            output_model.__name__ if output_model else "disabled"
        )

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
            logger.error("Invoke failed: 'messages' parameter is required")
            raise ValueError("'messages' is required in input")
        
        # Obtener todos los demás parámetros (temperature, max_tokens, etc.)
        extra_params = {k: v for k, v in input.items() if k != "messages"}
        
        logger.debug(
            "LLMRunnable.invoke [messages=%d, params=%s, structured_output=%s]",
            len(messages),
            list(extra_params.keys()),
            "enabled" if self._output_model else "disabled"
        )
        
        # Inyectar schema si es structured output
        if self._output_model:
            messages = self._inject_schema_prompt(messages)
        
        # Medir tiempo de respuesta del LLM
        start_time = time.time()
        
        try:
            # Pasar todo al adapter
            response = self._adapter.chat(
                messages=messages,
                **extra_params  # temperature, max_tokens, top_p, etc.
            )
            
            elapsed_ms = (time.time() - start_time) * 1000
            logger.info(
                "LLM response received [duration_ms=%.2f, adapter=%s]",
                elapsed_ms,
                self._adapter.__class__.__name__
            )
            
        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            logger.error(
                "LLM invocation failed [duration_ms=%.2f, adapter=%s, error=%s]",
                elapsed_ms,
                self._adapter.__class__.__name__,
                str(e)
            )
            raise

        if not self._output_model:
            logger.debug("Returning raw LLM response (no structured output)")
            return response

        return self._parse_structured_output(response.choices[0].message)

    # ---------
    # Internals
    # ---------

    def _inject_schema_prompt(self, messages):

        schema = self._output_model.model_json_schema()
        
        logger.debug(
            "Injecting JSON schema for structured output [model=%s, schema_properties=%s]",
            self._output_model.__name__,
            list(schema.get("properties", {}).keys())
        )

        system = {
            "role": "system",
            "content": (
                "You must return ONLY a valid JSON object that matches the following JSON schema.\n\n"
                "CRITICAL RULES:\n"
                "- Return ONLY the raw JSON object in compact format\n"
                "- DO NOT wrap the JSON in markdown code blocks (```json or ```)\n"
                "- DO NOT add any text before or after the JSON\n"
                "- DO NOT add explanations or comments\n"
                "- DO NOT add unnecessary indentation or formatting\n"
                "- Start your response with { and end with }\n"
                "- Use compact JSON format (minimal whitespace between properties)\n"
                r'- ESCAPE all double quotes inside string values using backslash (\")' + "\n"
                r'- If code contains docstrings with triple quotes ("""), escape them as (\"\"\")' + "\n\n"
                "CORRECT examples:\n"
                r'{"field": "value", "number": 42}' + "\n"
                r'{"code": "def foo():\n    \"\"\"Docstring\"\"\"\n    return 1"}' + "\n\n"
                "INCORRECT examples:\n"
                r'```json' + "\n" + r'{...}' + "\n" + r'```  ❌ NO markdown blocks' + "\n"
                r'Here is the JSON: {...}  ❌ NO additional text' + "\n"
                r'{\n  "field": "value"\n}  ❌ NO pretty-printing' + "\n"
                r'{"code": "def foo():\n    """Docstring"""\n    return 1"}  ❌ Quotes must be escaped' + "\n\n"
                "JSON Schema:\n"
                f"{json.dumps(schema, indent=2)}"
            ),
        }

        return [system, *messages]

    def _parse_structured_output(self, response):

        # adapta esto a tu modelo real de respuesta
        # ej: response.content / response.text / response.choices[0].message.content
        raw_text = response.content
        
        logger.debug(
            "Parsing structured output [model=%s, response_length=%d]",
            self._output_model.__name__,
            len(raw_text) if raw_text else 0
        )

        try:
            data = json.loads(raw_text)
            logger.debug("JSON parsing successful [fields=%s]", list(data.keys()) if isinstance(data, dict) else "non-dict")
            
        except json.JSONDecodeError as e:
            logger.error(
                "JSON parsing failed [model=%s, error=%s, position=%d, response_preview=%s]",
                self._output_model.__name__,
                str(e),
                e.pos if hasattr(e, 'pos') else -1,
                raw_text[:200] if raw_text else "empty"
            )
            raise ValueError(
                f"LLM did not return valid JSON for structured output: {str(e)}"
            ) from e
        except Exception as e:
            logger.error(
                "Unexpected error during JSON parsing [model=%s, error=%s]",
                self._output_model.__name__,
                str(e)
            )
            raise ValueError(
                "LLM did not return valid JSON for structured output"
            ) from e

        try:
            validated_model = self._output_model.model_validate(data)
            logger.info(
                "Structured output validated successfully [model=%s]",
                self._output_model.__name__
            )
            return validated_model
            
        except ValidationError as e:
            logger.error(
                "Pydantic validation failed [model=%s, errors=%d, details=%s]",
                self._output_model.__name__,
                len(e.errors()),
                e.errors()
            )
            raise ValueError(
                f"LLM response does not match expected schema: {e}"
            ) from e