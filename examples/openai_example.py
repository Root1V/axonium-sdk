#!/usr/bin/env python3
"""
Ejemplo completo de uso del LLM Arch SDK con OpenAI

Este script demuestra cómo usar el adapter de OpenAI para:
- Chat completions
- Text completions
- Embeddings
"""

import logging
from dotenv import load_dotenv

load_dotenv()  # Carga variables de entorno desde .env

from llm_arch_sdk.adapters.open_ai_adapter import OpenAIAdapter
from llm_arch_sdk.observability.helpers import new_session_id
from langfuse import propagate_attributes


# Configurar logging para ver los logs de Langfuse
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Reducir warnings del wrapper Langfuse
logging.getLogger("langfuse").setLevel(logging.ERROR)


def example_chat_completions(adapter: OpenAIAdapter):
    # 1. Probar Chat Completions
    print("\n📝 Probando Chat Completions...")
    try:
        messages=[
                {"role": "system", "content": "Eres un asistente útil."},
                {"role": "user", "content": "Hola, ¿cuál es la capital de Perú?"}
            ]
        chat_response = adapter.chat(
            messages=messages,
            max_tokens=100,
            temperature=0.7,
        )
        
        print("✅ Chat completion exitoso:")
        print(f"   Pregunta: {messages[1]['content']}")
        print(f"   Respuesta: {chat_response.choices[0].message.content}")
        print(f"   Modelo usado: {chat_response.model}")
        print(f"   Tokens usados: {chat_response.usage.total_tokens}")
    except Exception as e:
        print(f"⚠️  Chat completion falló: {e}")
    

def example_text_completions(adapter: OpenAIAdapter):
    # 2. Probar Text Completions
    print("\n✍️  Probando Text Completions...")
    try:
        prompt="Escribe un poema corto sobre la inteligencia artificial."
        completion_response = adapter.completions(
            prompt=prompt,
            max_tokens=50,
            temperature=0.7,
        )
    
        print("✅ Text completion exitoso:")
        print(f"Prompt: {prompt}")
        print(f"   Respuesta: {completion_response.choices[0].text.strip()}")
        print(f"   Modelo usado: {completion_response.model}")
        print(f"   Tokens usados: {completion_response.usage.total_tokens}")
    except Exception as e:
        print(f"⚠️  Text completion falló: {e}")
   
        
def example_embeddings(adapter: OpenAIAdapter):
    # 3. Probar Embeddings
    print("\n🧠 Probando Embeddings...")
    
    try:
        embedding_response = adapter.embeddings(
            input=["Inteligencia artificial", "Aprendizaje automático"],
        )
        
        print("✅ Embeddings exitosos:")
        for i, embedding in enumerate(embedding_response.data):
            print(f"   Embedding {i}: Dimensiones={len(embedding.embedding)}")
    except Exception as e:
        print(f"⚠️  Embeddings fallaron: {e}")
   
@propagate_attributes(session_id=new_session_id(), version="2.0")
def main():
    print("🚀 Probando LLM Arch SDK con OpenAI - Ejemplo completo")
    try:
        # Crear adapter con parámetros personalizados
        adapter = OpenAIAdapter(
            model="gpt-5.4-mini",  # Especificar modelo por defecto para todas las operaciones
            timeout=50.0,
        )
        print("✅ OpenAI Adapter creado")
        example_chat_completions(adapter)
        example_text_completions(adapter)
        example_embeddings(adapter)
        
    except Exception as e:
        print(f"❌ Error en la prueba: {e}")
        return 1
    return 0

if __name__ == "__main__":
    exit(main())