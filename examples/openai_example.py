#!/usr/bin/env python3
"""
Ejemplo completo de uso del LLM Arch SDK con OpenAI

Este script demuestra cómo usar el adapter de OpenAI para:
- Chat completions
- Text completions
- Embeddings
"""

import logging
import os
from dotenv import load_dotenv
from llm_arch_sdk.adapters.open_ai import OpenAIAdapter
from llm_arch_sdk.observability.bootstrap import start_trace, record_generation, record_event, set_active_trace, clear_active_trace

# Configurar logging para ver los logs de Langfuse
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Reducir warnings del wrapper Langfuse
logging.getLogger("langfuse").setLevel(logging.ERROR)

# Cargar variables de entorno desde el archivo .env (forzado)
_env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=_env_path, override=True)

def _endpoint_name(prefix, func):
    return f"{prefix}.{getattr(func, '__name__', 'create')}"


def _safe_dump(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "dict"):
        return obj.dict()
    return obj


def example_chat_completions(client):
    # 1. Probar Chat Completions
    print("\n📝 Probando Chat Completions...")
    trace = start_trace(
        name="client.chat.completions.create",
        input={"endpoint": _endpoint_name("client.chat.completions", client.chat.completions.create)},
        metadata={"flow": example_chat_completions.__name__},
        tags=["example", "chat", "openai_example"],
    )
    set_active_trace(trace)
    try:
        metadata = {
            "flow": example_chat_completions.__name__,
            "endpoint": _endpoint_name("client.chat.completions", client.chat.completions.create),
            "model": "gpt-3.5-turbo",
        }
        chat_response = client.chat.completions.create(
            model="gpt-3.5-turbo",  
            messages=[
                {"role": "system", "content": "Eres un asistente útil."},
                {"role": "user", "content": "Hola, ¿cuál es la capital de Francia?"}
            ],
            max_tokens=100,
            temperature=0.7,
        )
        record_generation(
            trace=trace,
            name="openai.chat.completions.generation",
            input=metadata,
            output=_safe_dump(chat_response),
            model=chat_response.model,
            usage=getattr(chat_response, "usage", None),
        )
        print("✅ Chat completion exitoso:")
        print(f"   Respuesta: {chat_response.choices[0].message.content}")
        print(f"   Modelo usado: {chat_response.model}")
        print(f"   Tokens usados: {chat_response.usage.total_tokens}")
    except Exception as e:
        record_event(trace, name="openai.chat.completions.error", input={"error": str(e)})
        print(f"⚠️  Chat completion falló: {e}")
    finally:
        clear_active_trace()

def example_text_completions(client):
    # 2. Probar Text Completions
    print("\n✍️  Probando Text Completions...")
    trace = start_trace(
        name="client.completions.create",
        input={"endpoint": _endpoint_name("client.completions", client.completions.create)},
        metadata={"flow": example_text_completions.__name__},
        tags=["example", "completions", "openai_example"],
    )
    set_active_trace(trace)
    try:
        metadata = {
            "flow": example_text_completions.__name__,
            "endpoint": _endpoint_name("client.completions", client.completions.create),
            "model": "text-davinci-003",
        }
        completion_response = client.completions.create(
            model="text-davinci-003", 
            prompt="Escribe un poema corto sobre la inteligencia artificial.",
            max_tokens=50,
            temperature=0.7,
        )
        record_generation(
            trace=trace,
            name="openai.completions.generation",
            input=metadata,
            output=_safe_dump(completion_response),
            model=completion_response.model,
            usage=getattr(completion_response, "usage", None),
        )
        print("✅ Text completion exitoso:")
        print(f"   Respuesta: {completion_response.choices[0].text.strip()}")
        print(f"   Modelo usado: {completion_response.model}")
        print(f"   Tokens usados: {completion_response.usage.total_tokens}")
    except Exception as e:
        record_event(trace, name="openai.completions.error", input={"error": str(e)})
        print(f"⚠️  Text completion falló: {e}")
    finally:
        clear_active_trace()
        
def example_embeddings(client):
    # 3. Probar Embeddings
    print("\n🧠 Probando Embeddings...")
    trace = start_trace(
        name="client.embeddings.create",
        input={"endpoint": _endpoint_name("client.embeddings", client.embeddings.create)},
        metadata={"flow": example_embeddings.__name__},
        tags=["example", "embeddings", "openai_example"],
    )
    set_active_trace(trace)
    try:
        metadata = {
            "flow": example_embeddings.__name__,
            "endpoint": _endpoint_name("client.embeddings", client.embeddings.create),
            "model": "text-embedding-ada-002",
        }
        embedding_response = client.embeddings.create(
            model="text-embedding-ada-002", 
            input=["Inteligencia artificial", "Aprendizaje automático"],
        )
        record_generation(
            trace=trace,
            name="openai.embeddings.generation",
            input=metadata,
            output=_safe_dump(embedding_response),
            model=embedding_response.model,
            usage=getattr(embedding_response, "usage", None),
        )
        print("✅ Embeddings exitosos:")
        for i, embedding in enumerate(embedding_response.data):
            print(f"   Embedding {i}: Dimensiones={len(embedding.embedding)}")
    except Exception as e:
        record_event(trace, name="openai.embeddings.error", input={"error": str(e)})
        print(f"⚠️  Embeddings fallaron: {e}")
    finally:
        clear_active_trace()
        

def main():
    print("🚀 Probando LLM Arch SDK con OpenAI - Ejemplo completo")
    try:
        # Crear adapter con parámetros personalizados
        adapter = OpenAIAdapter(
            timeout=60.0,
            use_langfuse=False,
        )
        print("✅ OpenAI Adapter creado")

        # Obtener cliente
        client = adapter.client()
        print("✅ Cliente OpenAI obtenido")
        
        example_chat_completions(client)
        example_text_completions(client)
        example_embeddings(client)
    except Exception as e:
        print(f"❌ Error en la prueba: {e}")
        return 1
    return 0

if __name__ == "__main__":
    exit(main())