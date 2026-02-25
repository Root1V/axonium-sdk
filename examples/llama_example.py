#!/usr/bin/env python3
"""
Ejemplo de uso del LLM Arch SDK

Este script demuestra cómo usar el SDK para hacer llamadas a un servidor LLM
con autenticación automática y manejo de errores.

NOTA: El SDK carga automáticamente el archivo .env del directorio actual.
Asegúrate de tener un .env con las credenciales necesarias.
"""

import logging
from dotenv import load_dotenv

load_dotenv()  # Carga variables de entorno desde .env

from axonium.adapters.llama_adapter import LlamaAdapter
from axonium.observability.helpers import new_session_id
from langfuse import propagate_attributes

# Configurar logging ANTES de importar el SDK para ver todos los mensajes
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Reducir warnings del wrapper Langfuse
logging.getLogger("langfuse").setLevel(logging.ERROR)


def example_health(adapter: LlamaAdapter):
    print("\n🔍 Probando Health Check...")
    try:
        health_response = adapter.health()
        print("✅ Health check exitoso:", health_response)
        print(f"   Estado: {health_response.status}")
        print(f"   Versión del servidor: {health_response.version}")
    except Exception as e:
        print(f"⚠️  Health check falló: {e}")
        
def example_chat_completions(adapter: LlamaAdapter):
    print("\n📝 Probando Chat Completions...")
    try:
        # El SDK detecta automáticamente adapter/operation/model
        # Solo necesitas pasar metadata/tags de negocio si las necesitas
        messages=[
                {"role": "system", "content": "Eres un asistente útil."},
                {"role": "user", "content": "Hola, ¿cuál es la capital de Francia?"}
        ]
        
        chat_response = adapter.chat(
            messages=messages,
            max_tokens=100,
            temperature=0.7,
            # Opcional: metadata custom de negocio
            # Opcional: tags custom de negocio
            trace_tags=["demo-user"]
        )
        print("✅ Chat completion exitoso:")
        print(f"   Pregunta: {messages[1]['content']}")
        print(f"   Respuesta: {chat_response.choices[0].message.content}")
        print(f"   Modelo usado: {chat_response.model}")
        print(f"   Tokens usados: {chat_response.usage.total_tokens}")
    except Exception as e:
        print(f"⚠️  Chat completion falló: {e}")
        raise e  # Re-raise para que se vea el stack trace en la demo
        
def example_text_completions(adapter: LlamaAdapter):
    print("\n✍️  Probando Text Completions...")
    try:
        # El SDK detecta automáticamente adapter/operation/model
        prompt="Escribe un poema corto sobre la inteligencia artificial."
        
        completion_response = adapter.completions(
            prompt=prompt,
            temperature=0.7,
            n_predict=100,
            # Opcional: metadata custom de negocio
            # Opcional: tags custom de negocio
            trace_tags=["creative"]
        )
        print("✅ Text completion exitoso:")
        print(f"   Prompt: {prompt}")
        print(f"   Respuesta: {completion_response.content.strip()}")
        print(f"   Modelo usado: {completion_response.model}")
        print(f"   Tokens usados: {completion_response.tokens_predicted}")
    except Exception as e:
        print(f"⚠️  Text completion falló: {e}")
        
def example_embeddings(adapter: LlamaAdapter):
    # Probar embeddings
    print("\n🧠 Probando Embeddings...")
    try:
        # El SDK detecta automáticamente adapter/operation/model
        response = adapter.embeddings(
            input=["Inteligencia artificial", "Aprendizaje automático"],
        )
        print("✅ Embeddings exitoso:")
        
        for i, embedding in enumerate(response.data):
            print(f"   Input: {response.input[i]}")
            print(f"   Embedding vector (primeros 5 valores): {embedding.embedding[:5]}...")
        print(f"   Número de embeddings: {len(response.data)}")
        print(f"   Dimensiones: {len(response.data[0].embedding)}")
        print(f"   Modelo usado: {response.model}")
            
        # Mostrar similitud aproximada entre los primeros dos embeddings
        if len(response.data) >= 2:
            emb1 = response.data[0].embedding
            emb2 = response.data[1].embedding
            # Similitud coseno aproximada (simplificada)
            dot_product = sum(a*b for a,b in zip(emb1, emb2))
            similarity = dot_product / (sum(a**2 for a in emb1)**0.5 * sum(b**2 for b in emb2)**0.5)
            print(f"   Similitud aproximada entre 'Hola mundo' y 'Hello world': {similarity:.3f}")
    except Exception as e:
        print(f"⚠️  Embeddings falló: {e}")
        

def main():
    print("🚀 Probando LLM Arch SDK - LLMAdapter")

    try:
        adapter = LlamaAdapter(
            model="Mixtra-7B-Instruct-v0.1.Q4_0.gguf",
            timeout=60.0,
        )
        print("✅ Adapter creado exitosamente")

        # propagate_attributes solo funciona como decorador en Langfuse v3
        @propagate_attributes(session_id=new_session_id(), version="1.0")
        def run():
            example_health(adapter)
            example_chat_completions(adapter)
            example_text_completions(adapter)
            example_embeddings(adapter)

        run()

        print("\n🎉 Prueba completada!")

    except Exception as e:
        print(f"❌ Error en la prueba: {e}")
        raise e


if __name__ == "__main__":
    main()