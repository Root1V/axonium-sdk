# Axonium

SDK Python para integración multi-provider de LLMs con autenticación gestionada, circuit breaker, observabilidad y abstracciones para agentes.

## Descripción

Axonium es una biblioteca Python diseñada para simplificar la integración con múltiples proveedores de LLM (OpenAI, Llama y otros compatibles). Proporciona una interfaz unificada con las siguientes capacidades:

- **Circuit Breaker**: Protección automática contra fallos en cascada con estados CLOSED/OPEN/HALF_OPEN
- **Autenticación gestionada**: Renovación automática de tokens con retry logic y circuit breaking en endpoints de auth
- **Enmascaramiento de PII**: Sistema configurable para proteger datos sensibles (PII, tarjetas de crédito, emails)
- **Trazabilidad completa**: Integración opcional con Langfuse para tracking de invocaciones con metadata (duration_ms, token_usage, model, operation)
- **Logging estructurado**: Métricas de performance y contexto completo en cada invocación
- **Validación de outputs**: Structured outputs con validación automática mediante Pydantic models
- **Resiliencia**: Retry logic, timeouts configurables y manejo robusto de errores

**Ideal para:** Aplicaciones que requieren integración con múltiples LLMs, trazabilidad de invocaciones, manejo robusto de errores y validación de respuestas estructuradas.

## Características principales

- **Multi-Provider Adapters**: Interfaz unificada para OpenAI, Llama y APIs compatibles con OpenAI
- **Structured Output Validation**: Validación automática de respuestas JSON mediante Pydantic models
- **Automatic Authentication**: Gestión de tokens con renovación automática y retry logic
- **Circuit Breaker Pattern**: Protección contra fallos en cascada con estados CLOSED/OPEN/HALF_OPEN
- **Observability Integration**: Soporte opcional para Langfuse con trazas automáticas, métricas de performance y logging estructurado
- **PII Masking**: Sistema configurable de enmascaramiento para datos sensibles (PII, tarjetas de crédito, emails)
- **Response Normalization**: Estandarización de respuestas entre diferentes proveedores
- **HTTP Client Factory**: Cliente HTTP basado en httpx con retry, timeout y connection pooling
- **Configuration Management**: Sistema centralizado via variables de entorno o custom settings
- **Type Safety**: Type hints completos en toda la biblioteca
- **Testing**: 103 tests unitarios con 100% de aprobación

## Ventajas clave

- **Abstracción multi-provider**: Interfaz unificada que permite cambiar entre proveedores (OpenAI, Llama, etc.) sin reescribir código
- **Validación automática**: Structured outputs con validación mediante Pydantic models y JSON parsing robusto
- **Observabilidad integrada**: Soporte opcional para Langfuse con trazas automáticas, o logging estructurado como fallback
- **Patrones de resiliencia**: Circuit breaker, retry logic, timeouts configurables y error handling robusto
- **Seguridad**: Enmascaramiento automático de PII en logs y trazas para cumplimiento normativo
- **Trazabilidad**: Metadata automática en cada invocación (duration_ms, token_usage, model, operation)
- **Type safety**: Type hints completos en toda la biblioteca para mejor soporte de IDEs
- **Testing robusto**: 103 tests unitarios con 100% de aprobación
- **Configuración centralizada**: Sistema basado en variables de entorno o custom settings
- **Compatible**: Funciona standalone o integrado con frameworks como LangChain/LangGraph

---

## Arquitectura

### C4 - Context

Diagrama de contexto del sistema (C4 Nivel 1) en ASCII:

```text
                  +-------------------------------------+
                  | Aplicación Consumidora              |
                  | (sistema externo)                   |
                  +-----------------+-------------------+
                                    | usa
                                    v
+-------------------+---------------------------------------------------------+
|                    Axonium SDK (biblioteca Python)                          |
+-------------------+---------------------------------------+-----------------+
          | autenticación           |                       | inferencia
          v                         |                       v
   +-----------+----------------+   |      +----------------+---------+
   | LLM-Security Server        |   |      | Llama-server             |
   | login + emisión de token   |   |      | inferencia de modelos    |
   +----------------------------+   |      +--------------------------+
                                    | 
                                    v trazas, métricas, metadata
                        +-----------+----------------+
                        | Langfuse Server            |
                        | trazabilidad y observación |
                        +----------------------------+
```

### C4 - Container

Diagrama de contenedores (C4 Nivel 2):

```text
+----------------------------------------------------------------------------------+
|                         Aplicación Consumidora                                   |
|                   (código de negocio: workflows/APIs/jobs)                       |
+-------------------------------------------+--------------------------------------+
                                            |
                                            |
+-------------------------------------------+--------------------------------------+
|  Axonium SDK                              |                                      |
|                                           v                                      |
|  +------------------------+      +--------+---------------+                      |
|  | Models/Normalizers     |<-----| Adapters               |                      |
|  | DTOs + validación.     |      | OpenAIAdapter/Llama    |                      |
|  +------------------------+      +-----------+------------+                      |
|                                              |                                   |
|                                              v                                   |
|                                  +-----------+------------+                      |
|                                  | Client + Transport     |-----+                |
|                                  | LlmClient/httpx/retry  |     |                |
|                                  | circuit breaker        |     |                |
|                                  +-----------+------------+     |                |
|                                              |                  |                |
|                                              v                  |                |
|                                  +-----------+------------+     |                |
|                                  | Auth                   |     |                |
|                                  | TokenManager           |     |                |
|                                  +------------------------+     |                |
|                                                                 |                |
|  +------------------------+       +---------------------+       |                |
|  | Config                 |<------| Observability       |<-+----+                |
|  | settings/env           |       | context/masking     |                        |
|  +------------------------+       +---------------------+                        |
+----------------------------------------------------------------------------------+
                 |                             |                           |              
                 v                             v                           v
   +-------------+-------------+    +----------+-----------+    +----------+--------+
   | LLM-Security              |    | LLM Gateway          |    | Langfuse Server   |
   | (login/token)             |    | Local Inference      |    | Trace/Span        |
   +---------------------------+    +----------------------+    +----------+--------+
                                                    
                                             
```

### Componentes principales

**1. Adapters (Capa de abstracción)**
- Interfaz unificada para diferentes proveedores LLM
- Normalización de requests/responses entre providers
- Implementaciones: `OpenAIAdapter`, `LlamaAdapter`

**2. Transport (Capa de comunicación)**
- Cliente HTTP basado en httpx con connection pooling
- Circuit breaker para protección contra fallos
- Retry logic configurable con backoff exponencial
- Factories para crear clientes con/sin autenticación

**3. Auth (Gestión de autenticación)**
- `TokenManager`: Gestión automática de tokens de acceso
- Renovación automática con circuit breaking en endpoints de auth
- Thread-safe para uso concurrente

**4. Observability (Trazabilidad y monitoreo)**
- Integración opcional con Langfuse para traces distribuidos
- Logging estructurado con metadata completa
- Sistema de masking para PII (correos, tarjetas, etc.)
- Contexto global para injection de metadata custom

**5. Models (Validación de datos)**
- Modelos Pydantic para parsing robusto de respuestas JSON
- Validación automática de tipos y constraints
- Modelos: `ChatCompletion`, `Completion`, `Usage`, `Timings`

**6. Config (Configuración)**
- Sistema centralizado basado en dataclasses
- Soporte para variables de entorno y custom settings
- Control de observabilidad, masking, circuit breaker, endpoints

**7. Integrations (Abstracciones de alto nivel)**
- `MiniAgent`: Building block para workflows de agentes
- `LLMRunnable`: Wrapper para invocaciones con structured output
- Compatible con LangGraph, LangChain y frameworks custom

---

## Quick Start

```python
from axonium import OpenAIAdapter

# 1. Crear un adapter para tu proveedor LLM
adapter = OpenAIAdapter(
    model="gpt-4",
    base_url="https://api.openai.com"
)

# 2. Usar chat directamente desde el adapter
response = adapter.chat(
    messages=[
        {"role": "system", "content": "Eres un asistente útil."},
        {"role": "user", "content": "¿Qué es Python?"}
    ],
    temperature=0.7
)

print(response.choices[0].message.content)
```

**Para ejemplos más avanzados:** Ver carpeta [examples/](examples/)

---

## Instalación

### Requisitos

- Python >= 3.13

### Instalación desde código fuente

1. Clona el repositorio:
   ```bash
   git clone https://github.com/Root1V/llm-arch-sdk.git
   cd axonium
   ```

2. Instala las dependencias:
   ```bash
   uv sync
   uv add --dev pytest 
   ```

3. Activa el entorno virtual:
   ```bash
   source .venv/bin/activate
   ```

4. Instala el paquete en modo editable (para que ejecuten los test)
   ```bash
   pip install -e .
   ```

5. Build del paquete
   ```bash
   uv build
   ```

### Instalación desde versión específica (para proyectos que consumen el SDK)

Si necesitas instalar una versión específica del SDK en tu proyecto:

1. Clona el repositorio en la versión que requieras
```bash
git fetch --tags && git checkout v0.4.6
```

2. Crea el paquete del SDK
```bash
uv build
```

3. Copia el SDK compilado a la carpeta de repositorio (opcional)
```bash
cp /axonium/dist/axonium-0.4.6* /opt/python-repo/
```

4. Agrega el SDK en tu proyecto y sincroniza las dependencias
```bash
uv add --find-links /opt/python-repo/ axonium
uv sync --find-links /opt/python-repo/
```

5. Alternativa usando `pip`
```bash
pip install --find-links=/opt/python-repo axonium
```

## Ejemplos de uso

La carpeta `examples/` contiene scripts demostrativos para probar las funcionalidades del SDK.

**Nota importante**: Los ejemplos cargan las variables de entorno desde un archivo `.env`. El SDK automatiza la carga usando `python-dotenv`, así que no necesitas escribir las credenciales en el código.

### Configuración de autenticación

Crea un archivo `.env` en la carpeta `examples/`:

```
LLM_BASE_URL=http://localhost:8080
LLM_USERNAME=tu_usuario
LLM_PASSWORD=tu_contraseña
```

### Configuración de observabilidad (opcional)

El SDK incluye integración opcional con Langfuse para observabilidad y trazabilidad de invocaciones LLM.

**Modo con Langfuse (recomendado para producción):**

```bash
# En tu .env
OBSERVABILITY_ENABLED=True

# Configuración de Langfuse (requerido si enabled=True)
LANGFUSE_PUBLIC_KEY=tu_public_key
LANGFUSE_SECRET_KEY=tu_secret_key
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_TRACING_ENVIRONMENT=production
```

Características:
- Trazas completas de cada invocación LLM con jerarquía
- Metadata automática (adapter, operation, model, duration_ms, token_usage)
- Análisis de errores con stack traces completos
- Masking de PII aplicado automáticamente en traces
- Dashboard en Langfuse para análisis y debugging

**Modo sin Langfuse (desarrollo/testing):**

```bash
# En tu .env
OBSERVABILITY_ENABLED=False
```

Características:
- No se instancia cliente de Langfuse
- Logs estructurados con metadata en stdout
- Masking de PII aplicado en logs
- Métricas de performance en logs

**Nota**: Si `OBSERVABILITY_ENABLED=False`, no es necesario configurar las variables de Langfuse.

### Ejecutar ejemplos

La carpeta `examples/` contiene 4 ejemplos que demuestran diferentes capacidades del SDK:

#### 1. Ejemplo básico con LlamaAdapter
```bash
uv run python examples/llama_example.py
```
Demuestra el uso fundamental del SDK:
- Health check del servidor LLM
- Chat completions
- Text completions
- Embeddings

#### 2. Ejemplo con OpenAIAdapter  
```bash
uv run python examples/openai_example.py
```
Muestra cómo cambiar de provider:
- Uso de OpenAIAdapter (compatible con cualquier API OpenAI-like)
- Chat y text completions
- Generación de embeddings
- Configuración personalizada

#### 3. Ejemplo de Agentes con Structured Output
```bash
uv run python examples/agents_example.py
```
Demuestra structured outputs y pipelines:
- Validación automática de respuestas JSON con Pydantic
- Pipeline de múltiples agentes
- Estado compartido entre agentes

#### 4. Ejemplo avanzado con LangGraph
```bash
uv run python examples/langraph_example.py
```
Workflow complejo con orquestación:
- Integración con LangGraph StateGraph
- Patrón de reflexión (draft → critique → refine)
- Múltiples nodos coordinados
- Observabilidad automática

## Estructura del Proyecto

```
axonium/
├── src/
│   ├── __init__.py
│   └── axonium/
│       ├── __init__.py              # Public API exports
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── base_llm_adapter.py
│       │   ├── llama_adapter.py
│       │   └── open_ai_adapter.py
│       ├── auth/
│       │   ├── __init__.py
│       │   └── token_manager.py
│       ├── client/
│       │   ├── __init__.py
│       │   ├── base_client.py
│       │   ├── chat_completions.py
│       │   ├── completions.py
│       │   ├── embeddings.py
│       │   └── llm_client.py
│       ├── config/
│       │   ├── __init__.py
│       │   └── settings.py
│       ├── integrations/              # Workflow abstractions
│       │   ├── agent.py               # MiniAgent
│       │   ├── llm_runnable.py        # LLMRunnable
│       │   ├── node.py
│       │   └── runnable.py
│       ├── models/
│       │   ├── __init__.py
│       │   ├── chat_completion.py
│       │   ├── completion.py
│       │   ├── generation_settings.py
│       │   ├── llm_response.py
│       │   ├── stop_type.py
│       │   ├── timings.py
│       │   └── usage.py
│       ├── normalizers/
│       │   ├── __init__.py
│       │   ├── completion_detector.py
│       │   └── content_normalizer.py
│       ├── observability/
│       │   ├── __init__.py
│       │   ├── bootstrap.py
│       │   ├── context.py
│       │   ├── helpers.py
│       │   └── masking.py
│       └── transport/
│           ├── __init__.py
│           ├── auth_http_client_factory.py
│           ├── circuit_breaker.py
│           └── http_client_factory.py
├── examples/
│   ├── .env                         # Variables de entorno
│   ├── agents_example.py            # Pipeline de agentes con LLMRunnable
│   ├── langraph_example.py          # ✨ LangGraph workflow con MiniAgent
│   ├── llama_example.py             # Uso completo de LlamaAdapter
│   └── openai_example.py            # Uso de OpenAIAdapter
├── test/
│   ├── adapters/
│   ├── auth/
│   ├── client/
│   ├── integrations/              # ✨ Tests para MiniAgent y LLMRunnable
│   ├── models/
│   ├── normalizers/
│   └── transport/
├── main.py
├── pyproject.toml
├── uv.lock
├── .gitignore
├── LICENSE
└── README.md
```

### Descripción de módulos

- **adapters/**: Implementaciones de adapters para diferentes proveedores LLM (OpenAI, Llama). Cada adapter implementa la interfaz base y maneja autenticación, retry logic y normalización específica del proveedor.

- **auth/**: Sistema de autenticación con TokenManager que gestiona tokens de acceso, renovación automática y circuit breaking para endpoints de autenticación. Thread-safe para uso concurrente.

- **client/**: Cliente HTTP con endpoints especializados (chat, completions, embeddings) construidos sobre httpx. Incluye manejo de errores, timeouts y connection pooling.

- **config/**: Sistema de configuración centralizado basado en dataclasses. Soporta variables de entorno y settings custom. Controla observabilidad, masking, circuit breaker y endpoints.

- **integrations/**: Abstracciones adicionales para workflows y structured outputs:
  - `agent.py`: MiniAgent para integración con frameworks de workflows
  - `llm_runnable.py`: Wrapper para invocaciones con validación Pydantic
  - `node.py`, `runnable.py`: Building blocks reutilizables

- **models/**: Modelos Pydantic para parsing y validación de respuestas JSON. Incluye ChatCompletion, Completion, Usage, Timings y GenerationSettings.

- **normalizers/**: Utilidades para procesamiento de texto:
  - `CompletionDetector`: Detecta si una respuesta está semánticamente completa
  - `ContentNormalizer`: Limpia artefactos de formato (asteriscos, whitespace, etc.)

- **observability/**: Sistema de observabilidad con integración opcional a Langfuse:
  - Bootstrap y contexto global para metadata injection
  - Sistema de masking configurable para PII
  - Fallback a logs estructurados cuando observability está deshabilitada

- **transport/**: Capa de transporte HTTP:
  - `CircuitBreaker`: Implementación del patrón circuit breaker con estados (CLOSED/OPEN/HALF_OPEN)
  - `AuthHttpClientFactory`: Factory para clientes httpx con autenticación
  - `HttpClientFactory`: Factory base para clientes sin auth

## Pruebas

Para ejecutar las pruebas:

```bash
uv run pytest test/
```

**Estado actual: 103/103 tests aprobados**

El proyecto incluye 103 pruebas unitarias organizadas por módulos:

- `test/client/`: Tests para clientes y endpoints (chat, completions, embeddings)
- `test/auth/`: Tests para autenticación y gestión de tokens
- `test/transport/`: Tests para circuit breaker y transporte HTTP
- `test/adapters/`: Tests para adaptadores de proveedores (Llama, OpenAI)
- `test/integrations/`: Tests para abstracciones de workflows
- `test/models/`: Tests para modelos de datos y parsing JSON
- `test/normalizers/`: Tests para normalización de contenido

Cobertura de funcionalidades:
- Autenticación y renovación de tokens
- Circuit breaker con estados CLOSED/OPEN/HALF_OPEN
- Clientes HTTP y endpoints especializados
- Adaptadores multi-provider
- Parsing y validación de respuestas JSON
- Normalización de contenido
- Manejo de errores y timeouts

## Historial de cambios

### v0.4.6 (2026-02-22)

**Nuevas funcionalidades:**
- Abstracciones para workflows: MiniAgent y LLMRunnable para simplificar integraciones
- Masking independiente: Nueva variable `MASKING_ENABLED` separada de `OBSERVABILITY_ENABLED`
- API pública mejorada: Nuevo `__init__.py` raíz para importaciones simplificadas
  ```python
  # Antes
   from axonium.integrations.agent import MiniAgent
  
  # Ahora
   from axonium import MiniAgent
  ```

**Mejoras:**
- JSON parsing mejorado con prompts optimizados y manejo de edge cases
- Sistema de logging estructurado con métricas de performance (duration_ms)
- Trazabilidad de errores con stack traces y previews de respuestas

**Correcciones:**
- Fix: Alineación de claves de estado entre nombres de agentes y prompt builders
- Fix: Compatibilidad con Langfuse v3 (decorador @observe en generadores)

**Documentación:**
- Nuevo ejemplo: `examples/langraph_example.py` con patrón de reflexión
- Documentación de observabilidad actualizada

---

### v0.4.0

- 🚀 Nuevo adaptador LangChainAdapter para integración con LangChain
- 📝 Soporte para ChatOpenAI de LangChain
- ✅ 7 nuevos tests unitarios para LangChainAdapter
- 🔄 Patrón **kwargs implementado en todos los adaptadores
- 📚 Nuevo ejemplo: `examples/langchain_example.py`

### v0.3.0
- ✅ TokenManager ahora es **opcional** en `AuthHttpClientFactory.create()`
- ✅ Se crea automáticamente una instancia si no se proporciona
- ✅ Ejemplos actualizados para usar `.env` con `python-dotenv`
- ✅ Tests verificados y funcionando correctamente

### v0.2.0
- 🔧 Refactor: Consolidación de manejo de headers y mejora de herencia en HTTP client factories
- Mejora de la arquitectura del transporte

### v0.1.0
- 🎉 Release inicial del LLM Arch SDK
- ✅ Autenticación automática con TokenManager
- ✅ Circuit Breaker para protección contra fallos
- ✅ Adaptadores para Llama y OpenAI
- ✅ Cliente HTTP robusto con httpx
- ✅ Normalización de respuestas
- ✅ Suite de tests unitarios completa
- ✅ Documentación y ejemplos de uso

## 🤝 How to Contribute

Contributions are what make the open-source community such an amazing place to learn, inspire, and create. Any contributions you make are greatly appreciated.

1. **Fork the repository**
2. **Create a new branch** for your feature or bug fix:
   ```bash
   git checkout -b feature/new-architecture
   # or
   git checkout -b bugfix/fix-typo
   ```
3. **Make your changes**: Please ensure the code is well-commented and follows the project's code style
4. **Run the tests** to ensure everything works:
   ```bash
   uv run pytest test/
   ```
5. **Submit a pull request** with a detailed description of your changes

You can also open an issue to:
- Report a bug
- Suggest an enhancement
- Propose a new feature or architecture improvement

## Reconocimiento

Si encuentras útil Axonium en tu trabajo, considera:
- ⭐ Dar una estrella al repositorio
- 📢 Compartir el proyecto con tu equipo
- 🤝 Contribuir mejoras o reportar issues

Para referencias en documentación técnica:
- **Proyecto**: Axonium
- **Repositorio**: https://github.com/Root1V/llm-arch-sdk
- **Autor**: Emeric Espiritu Santiago
- **Licencia**: MIT

## Licencia

Este proyecto está bajo la Licencia MIT. Ver el archivo [LICENSE](LICENSE) para más detalles.

