#!/usr/bin/env python3
"""
Ejemplo de uso del método async_chat del LLM Arch SDK

Este script demuestra cómo usar async_chat para:
- Llamadas asíncronas individuales (LlamaAdapter y OpenAIAdapter)
- Llamadas concurrentes con asyncio.gather para mayor rendimiento
"""

import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()  # Carga variables de entorno desde .env

from axonium.adapters.llama_adapter import LlamaAdapter
from axonium.adapters.open_ai_adapter import OpenAIAdapter
from axonium.observability.helpers import new_session_id
from langfuse import propagate_attributes

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger("langfuse").setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# Ejemplos individuales
# ---------------------------------------------------------------------------

async def example_async_chat_llama(adapter: LlamaAdapter):
    """Llamada asíncrona simple usando LlamaAdapter."""
    print("\n📝 [Llama] Probando async_chat...")
    try:
        messages = [
            {"role": "system", "content": "Eres un asistente útil."},
            {"role": "user", "content": "¿Cuál es la capital de Japón?"},
        ]

        response = await adapter.async_chat(
            messages=messages,
            max_tokens=100,
            temperature=0.7,
        )

        print("✅ [Llama] async_chat exitoso:")
        print(f"   Pregunta:  {messages[1]['content']}")
        print(f"   Respuesta: {response.choices[0].message.content}")
        print(f"   Modelo:    {response.model}")
        print(f"   Tokens:    {response.usage.total_tokens}")
    except Exception as e:
        print(f"⚠️  [Llama] async_chat falló: {e}")
        raise


async def example_async_chat_openai(adapter: OpenAIAdapter):
    """Llamada asíncrona simple usando OpenAIAdapter."""
    print("\n📝 [OpenAI] Probando async_chat...")
    try:
        messages = [
            {"role": "system", "content": "Eres un asistente útil."},
            {"role": "user", "content": "¿Cuál es la capital de Alemania?"},
        ]

        response = await adapter.async_chat(
            messages=messages,
            max_tokens=100,
            temperature=0.7,
        )

        print("✅ [OpenAI] async_chat exitoso:")
        print(f"   Pregunta:  {messages[1]['content']}")
        print(f"   Respuesta: {response.choices[0].message.content}")
        print(f"   Modelo:    {response.model}")
        print(f"   Tokens:    {response.usage.total_tokens}")
    except Exception as e:
        print(f"⚠️  [OpenAI] async_chat falló: {e}")
        raise


# ---------------------------------------------------------------------------
# Ejemplo de llamadas concurrentes
# ---------------------------------------------------------------------------

async def example_concurrent_async_chat(adapter: LlamaAdapter):
    """
    Demuestra la ventaja principal del método asíncrono:
    múltiples llamadas ejecutadas en paralelo con asyncio.gather,
    en lugar de ejecutarse de forma secuencial.
    """
    print("\n⚡ Probando llamadas concurrentes con asyncio.gather...")

    preguntas = [
        "¿Cuál es la capital de Francia?",
        "¿Cuál es la capital de Perú?",
        "¿Cuál es la capital de España?",
        "¿Cuál es la capital de China?",
    ]

    tasks = [
        adapter.async_chat(
            messages=[
                {"role": "system", "content": "Responde en una sola oración."},
                {"role": "user", "content": pregunta},
            ],
            max_tokens=60,
            temperature=0.7,
        )
        for pregunta in preguntas
    ]

    try:
        resultados = await asyncio.gather(*tasks)

        print(f"✅ {len(resultados)} llamadas completadas en paralelo:")
        for pregunta, resultado in zip(preguntas, resultados):
            respuesta = resultado.choices[0].message.content.strip()
            print(f"   Q: {pregunta}")
            print(f"   A: {respuesta}")
            print()
    except Exception as e:
        print(f"⚠️  Llamadas concurrentes fallaron: {e}")
        raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

@propagate_attributes(session_id=new_session_id(), version="1.0")
async def main():
    print("🚀 Probando async_chat - LLM Arch SDK")

    # --- LlamaAdapter ---
    try:
        llama_adapter = LlamaAdapter(model="llama3.2", timeout=60.0)
        print("✅ LlamaAdapter creado")
        await example_async_chat_llama(llama_adapter)
        await example_concurrent_async_chat(llama_adapter)
    except Exception as e:
        print(f"❌ Error con LlamaAdapter: {e}")

    # --- OpenAIAdapter ---
    try:
        openai_adapter = OpenAIAdapter(model="gpt-4o-mini", timeout=60.0)
        print("\n✅ OpenAIAdapter creado")
        await example_async_chat_openai(openai_adapter)
    except Exception as e:
        print(f"❌ Error con OpenAIAdapter: {e}")


if __name__ == "__main__":
    asyncio.run(main())
