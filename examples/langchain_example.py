#!/usr/bin/env python3
"""
Ejemplo de uso del LLM Arch SDK con LangChain ChatOpenAI

Este script demuestra cómo usar el adapter de LangChain para:
- Chat completions con ChatOpenAI
- Text completions
- Embeddings con OpenAIEmbeddings
- Uso directo del cliente LangChain
"""

import logging
import os
from dotenv import load_dotenv
from llm_arch_sdk.adapters.lang_adapter import LangChainAdapter

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Cargar variables de entorno
load_dotenv()

def example_chat_completions(adapter):
    """Ejemplo de chat completions usando el adapter"""
    print("\n💬 Probando Chat Completions con LangChain...")
    try:
        messages = [
            {"role": "system", "content": "Eres un asistente experto en tecnología."},
            {"role": "user", "content": "¿Qué es LangChain?"}
        ]
        
        response = adapter.chat(messages=messages, temperature=0.7, max_tokens=100)
        
        print("✅ Chat completion exitoso:")
        print(f"   Respuesta: {response.content}")
    except Exception as e:
        print(f"⚠️  Chat completion falló: {e}")


def example_text_completions(adapter):
    """Ejemplo de text completions usando el adapter"""
    print("\n📝 Probando Text Completions con LangChain...")
    try:
        response = adapter.completions(
            prompt="Escribe un haiku sobre la inteligencia artificial",
            temperature=0.8,
            max_tokens=50
        )
        
        print("✅ Text completion exitoso:")
        print(f"   Respuesta: {response.content}")
    except Exception as e:
        print(f"⚠️  Text completion falló: {e}")


def example_embeddings(adapter):
    """Ejemplo de embeddings usando el adapter"""
    print("\n🧠 Probando Embeddings con LangChain...")
    try:
        # Embedding de un solo texto
        single_embedding = adapter.embeddings("Machine learning es genial")
        print(f"✅ Embedding single exitoso: {len(single_embedding)} dimensiones")
        
        # Embeddings de múltiples textos
        texts = [
            "LangChain es un framework para LLMs",
            "Python es un lenguaje de programación",
            "Los embeddings representan texto como vectores"
        ]
        multi_embeddings = adapter.embeddings(texts)
        print(f"✅ Embeddings múltiples exitosos: {len(multi_embeddings)} vectores")
        
    except Exception as e:
        print(f"⚠️  Embeddings falló: {e}")


def example_direct_client_usage(adapter):
    """Ejemplo de uso directo del cliente LangChain"""
    print("\n🔧 Probando uso directo del cliente ChatOpenAI...")
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        
        # Obtener el cliente directo
        chat_client = adapter.client()
        
        # Usar directamente métodos de LangChain
        messages = [
            SystemMessage(content="Eres un poeta creativo."),
            HumanMessage(content="Escribe 2 líneas sobre el océano")
        ]
        
        response = chat_client.invoke(messages)
        print("✅ Invocación directa exitosa:")
        print(f"   Respuesta: {response.content}")
        
        # También se puede hacer streaming
        print("\n🔄 Probando streaming directo...")
        print("   ", end="")
        for chunk in chat_client.stream(messages):
            print(chunk.content, end="", flush=True)
        print()
        
    except Exception as e:
        print(f"⚠️  Uso directo falló: {e}")


def main():
    """Función principal que ejecuta todos los ejemplos"""
    print("🚀 Probando LLM Arch SDK - LangChainAdapter")
    print("=" * 60)
    
    try:
        # Crear adapter (no requiere runnable ni model en el constructor)
        adapter = LangChainAdapter(
            temperature=0.7  # Configuración por defecto
        )
        print("✅ LangChain Adapter creado exitosamente")
        
        # Ejecutar ejemplos
        example_chat_completions(adapter)
        example_text_completions(adapter)
        example_embeddings(adapter)
        example_direct_client_usage(adapter)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("🎉 Prueba completada!")


if __name__ == "__main__":
    main()
