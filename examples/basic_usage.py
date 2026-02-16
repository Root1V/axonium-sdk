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

from llm_arch_sdk.adapters.llama_adapter import LlamaAdapter
from llm_arch_sdk.observability.helpers import new_session_id
from langfuse import propagate_attributes


# Configurar logging para ver los logs del SDK
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def example_health(client):
    print("\n🔍 Probando Health Check...")
    try:
        health_response = client.health()
        print("✅ Health check exitoso:")
        print(f"   Estado: {health_response.status}")
        print(f"   Versión del servidor: {health_response.version}")
    except Exception as e:
        print(f"⚠️  Health check falló: {e}")
        
def example_chat_completions(client):
    print("\n📝 Probando Chat Completions...")
    try:
        # El SDK detecta automáticamente adapter/operation/model
        # Solo necesitas pasar metadata/tags de negocio si las necesitas
        chat_response = client.chat.create(
            model="llama-7b",  
            messages=[
                {"role": "system", "content": "Eres un asistente útil."},
                {"role": "user", "content": "Hola, ¿cuál es la capital de Francia?"}
            ],
            max_tokens=100,
            temperature=0.7,
            # Opcional: metadata custom de negocio
            # Opcional: tags custom de negocio
            trace_tags=["demo-user"]
        )
        print("✅ Chat completion exitoso:")
        print(f"   Respuesta: {chat_response.choices[0].message.content}")
        print(f"   Modelo usado: {chat_response.model}")
        print(f"   Tokens usados: {chat_response.usage.total_tokens}")
    except Exception as e:
        print(f"⚠️  Chat completion falló: {e}")
        
def example_text_completions(client):
    print("\n✍️  Probando Text Completions...")
    try:
        # El SDK detecta automáticamente adapter/operation/model
        completion_response = client.completions.create(
            prompt="Escribe un poema corto sobre la inteligencia artificial.",
            temperature=0.7,
            n_predict=50,
            # Opcional: metadata custom de negocio
            # Opcional: tags custom de negocio
            trace_tags=["creative"]
        )
        print("✅ Text completion exitoso:")
        print(f"   Respuesta: {completion_response.content.strip()}")
        print(f"   Modelo usado: {completion_response.model}")
        print(f"   Tokens usados: {completion_response.tokens_predicted}")
    except Exception as e:
        print(f"⚠️  Text completion falló: {e}")
        
def example_embeddings(client):
    # Probar embeddings
    print("\n🧠 Probando Embeddings...")
    try:
        # El SDK detecta automáticamente adapter/operation/model
        response = client.embeddings.create(
            model="llama-embedding-7b",
            input=["Inteligencia artificial", "Aprendizaje automático"],
            # Opcional: metadata custom de negocio
            # Opcional: tags custom de negocio
            trace_tags=["explore"]
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
        # Crear adapter con parámetros personalizados
        with propagate_attributes(session_id=new_session_id(), version="1.0"):
            adapter = LlamaAdapter(
                timeout=60.0
            )
            print("✅ Adapter creado exitosamente")

            # Obtener cliente
            client = adapter.client()
            print("✅ Cliente LLM obtenido")
            
            example_health(client)
            example_chat_completions(client)
            example_text_completions(client)
            example_embeddings(client)
        
        print("\n🎉 Prueba completada!")

    except Exception as e:
        print(f"❌ Error en la prueba: {e}")
        return 1

    return 0

if __name__ == "__main__":
    exit(main())