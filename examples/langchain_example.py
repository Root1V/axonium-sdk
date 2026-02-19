#!/usr/bin/env python3
"""
Ejemplo de uso del LLM Arch SDK con LangChain ChatOpenAI

Este script demuestra cómo usar el adapter de LangChain para:
- Invocar ChatOpenAI
- Usar el cliente con cadenas de LangChain
"""

import logging
import os
from dotenv import load_dotenv
from llm_arch_sdk.adapters.lang import LangChainAdapter
from langchain_core.messages import SystemMessage, HumanMessage

# Configurar logging para ver los logs de Langfuse
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Cargar variables de entorno desde el archivo .env (forzado)
_env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=_env_path, override=True)

# Cargar variables de entorno desde el archivo .env
load_dotenv()

def example_basic_invoke(client):
    """Ejemplo básico de invocación del modelo"""
    print("\n📝 Probando invocación básica...")
    try:
        response = client.invoke("¿Cuál es la capital de Francia?")
        print("✅ Invocación exitosa:")
        print(f"   Respuesta: {response.content}")
    except Exception as e:
        print(f"⚠️  Invocación falló: {e}")

def example_stream(client):
    """Ejemplo de streaming de respuesta"""
    print("\n🔄 Probando streaming...")
    try:
        print("✅ Respuesta en streaming:")
        for chunk in client.stream("Escribe un poema corto sobre IA"):
            print(f"   {chunk.content}", end="", flush=True)
        print()
    except Exception as e:
        print(f"⚠️  Streaming falló: {e}")

def example_with_system_prompt(client):
    """Ejemplo con prompt del sistema"""
    print("\n🎯 Probando con system prompt...")
    try:
        
        messages = [
            SystemMessage(content="Eres un experto en programación Python."),
            HumanMessage(content="¿Cuál es la mejor práctica para manejo de excepciones?")
        ]
        
        response = client.invoke(messages)
        print("✅ Respuesta con system prompt:")
        print(f"   {response.content}")
    except Exception as e:
        print(f"⚠️  Request falló: {e}")

def main():
    print("🚀 Probando LLM Arch SDK - LangChainAdapter")
    
    try:
        adapter = LangChainAdapter(
            model="gpt-3.5-turbo",
            temperature=0.7
        )
        print("✅ LangChain Adapter creado exitosamente")
        
        client = adapter.client()
        print("✅ Cliente ChatOpenAI obtenido")
        
        # Ejecutar ejemplos
        example_basic_invoke(client)
        example_stream(client)
        example_with_system_prompt(client)
        
    except Exception as e:
        print(f"❌ Error al crear adapter: {e}")
    
    print("\n🎉 Prueba completada!")

if __name__ == "__main__":
    main()
