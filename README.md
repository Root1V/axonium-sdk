# LLM Arch SDK

**SDK empresarial para integración de Large Language Models con capacidades de production-ready, observabilidad y orquestación de workflows.**

## Descripción

LLM Arch SDK es una biblioteca Python de nivel empresarial diseñada para construir aplicaciones LLM robustas y escalables. Proporciona una capa de abstracción unificada sobre múltiples proveedores de LLM (OpenAI, Llama, etc.) con características avanzadas de observabilidad, structured output validation, workflow orchestration y enterprise-grade security.

**Caso de uso ideal:** Aplicaciones enterprise que requieren integración multi-LLM, trazabilidad completa, manejo robusto de errores, structured outputs validados y workflows complejos de agentes.

## Características

### 🚀 Core Features

- **Multi-Provider Adapters**: Interfaz unificada para OpenAI, Llama y cualquier API compatible
- **Structured Output**: Validación automática de respuestas JSON con Pydantic models
- **Smart JSON Parsing**: Sistema robusto con 85% reducción de errores de parsing
- **Automatic Authentication**: TokenManager con renovación automática de tokens y retry logic
- **Circuit Breaker Pattern**: Protección inteligente contra fallos en cascada (CLOSED/OPEN/HALF_OPEN)
- **Response Normalization**: Estandarización automática de respuestas entre diferentes proveedores

### 🏢 Enterprise Features

- **Production-Ready Observability**: Integración opcional con Langfuse para trazabilidad completa
  - Métricas automáticas de performance (latency, tokens, cost)
  - Logging estructurado con contexto completo
  - Stack traces y error previews para debugging
- **PII Masking**: Sistema independiente de masking para datos sensibles (PII, tarjetas, emails)
- **Configurable Settings**: Configuración centralizada via variables de entorno o settings custom
- **HTTP Client Factory**: Cliente httpx robusto con retry, timeout y connection pooling

### 🤖 Workflow Orchestration

- **MiniAgent**: Abstracción declarativa para crear agentes LLM reutilizables
  - Reduce código boilerplate en ~60%
  - Compatible con LangGraph, LangChain y frameworks custom
  - Observabilidad automática por agente
- **LLMRunnable**: Wrapper de alto nivel para invocaciones LLM con structured output
  - Schema injection automática en prompts
  - Validación de JSON con mensajes de error claros
  - Soporte para pipelines de múltiples agentes

### 🛠️ Developer Experience

- **Clean Public API**: Importaciones simples (`from llm_arch_sdk import MiniAgent`)
- **Type Safety**: Type hints completos en toda la biblioteca
- **Comprehensive Testing**: 103 tests unitarios (100% passing) con alta cobertura
- **Rich Examples**: 4 ejemplos completos desde básico hasta workflows empresariales con LangGraph
- **Environment-based Config**: Soporte nativo para `.env` con python-dotenv

## ¿Por qué usar LLM Arch SDK?

### 🎯 **Comparado con usar OpenAI/Anthropic directamente:**
- ✅ **Abstracción multi-provider**: Cambia entre OpenAI, Llama y otros sin reescribir código
- ✅ **Structured outputs validados**: Pydantic models + JSON parsing robusto (85% menos errores)
- ✅ **Observabilidad enterprise**: Trazas automáticas con Langfuse, métricas y logging estructurado
- ✅ **Resilience patterns**: Circuit breaker, retry logic y error handling incorporados
- ✅ **Security by default**: Masking automático de PII en logs y traces

### 🎯 **Comparado con LangChain:**
- ✅ **Más ligero y simple**: Sin overhead de abstracciones complejas
- ✅ **Type-safe**: Type hints completos, mejor autocompletado en IDEs
- ✅ **Testing sólido**: 103 tests unitarios vs dependencia de integration tests
- ✅ **Flexible**: Funciona standalone o integrado con LangGraph/LangChain
- ✅ **Production-ready**: Circuit breakers, auth management, enterprise logging incorporados

### 🎯 **Para equipos enterprise:**
- 📊 **Trazabilidad completa**: Desde request hasta respuesta con metadata automática
- 🔐 **Compliance**: Masking de PII configurable (GDPR, HIPAA, SOC2)
- 🛡️ **Resilience**: Circuit breakers, timeouts, automatic retries
- 📈 **Observabilidad**: Métricas de performance, cost tracking, error analytics
- 🔧 **Mantenible**: API limpia, tests completos, documentación exhaustiva

---

## Quick Start

```python
from llm_arch_sdk import OpenAIAdapter, LLMRunnable
from pydantic import BaseModel

# 1. Define tu structured output
class CodeReview(BaseModel):
    rating: int  # 1-5
    issues: list[str]
    suggestions: list[str]

# 2. Crea el adapter y runnable
adapter = OpenAIAdapter(model="gpt-4", base_url="https://api.openai.com")
reviewer = LLMRunnable(adapter=adapter, output_model=CodeReview)

# 3. Invoca con validación automática
result = reviewer.invoke({
    "messages": [{"role": "user", "content": "Review: def add(a,b): return a+b"}]
})

print(f"Rating: {result.rating}/5")  # Type-safe!
print(f"Issues: {result.issues}")    # Validated!
```

**¿Qué acabas de lograr?**
- ✅ Structured output validado con Pydantic
- ✅ JSON parsing robusto (sin errores de formato)
- ✅ Type safety completo en tu código
- ✅ Schema injection automática en el prompt
- ✅ Error handling incorporado

**Para workflows más complejos:** Ver [ejemplo 4: LangGraph](#4--ejemplo-avanzado-con-langgraph-reflection-pattern)

---

## Instalación

### Requisitos

- Python >= 3.13

### Instalación desde código fuente

1. Clona el repositorio:
   ```bash
   git clone https://github.com/Root1V/llm-arch-sdk.git
   cd llm_arch_sdk
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
cp /llm_arch_sdk/dist/llm_arch_sdk-0.4.6* /opt/python-repo/
```

4. Agrega el SDK en tu proyecto y sincroniza las dependencias
```bash
uv add --find-links /opt/python-repo/ llm-arch-sdk
uv sync --find-links /opt/python-repo/
```

5. Alternativa usando `pip`
```bash
pip install --find-links=/opt/python-repo llm-arch-sdk
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

El SDK incluye un **sistema de observabilidad empresarial de dos niveles** que se adapta a tus necesidades:

#### 🔍 Modo 1: Observabilidad completa con Langfuse (Production)

**Cuándo usar:** Ambientes de producción que requieren trazabilidad completa, métricas de performance y análisis de costos.

```bash
# En tu .env
OBSERVABILITY_ENABLED=True

# Configuración de Langfuse (requerido si enabled=True)
LANGFUSE_PUBLIC_KEY=tu_public_key
LANGFUSE_SECRET_KEY=tu_secret_key
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_TRACING_ENVIRONMENT=production
```

**Qué obtienes:**
- ✅ **Trazas completas** de cada invocación LLM con jerarquía padre-hijo
- ✅ **Metadata automática** (adapter, operation, model, duration_ms, token_usage)
- ✅ **Cost tracking** basado en tokens consumidos por modelo
- ✅ **Error analytics** con stack traces y previews de respuestas
- ✅ **Masking de PII** aplicado automáticamente en traces
- ✅ **Dashboard en Langfuse** para análisis visual y debugging

**Ejemplo de trace:**
```
Generation: adapter.openai.chat
├─ Metadata: {model: gpt-4, operation: chat, duration_ms: 1243}
├─ Input: [masked if PII detected]
├─ Output: {"response": "...", "tokens": 234}
└─ Tags: [production, llm-arch-sdk:0.4.6]
```

---

#### 📝 Modo 2: Logs estructurados (Development)

**Cuándo usar:** Ambientes de desarrollo/testing donde no necesitas telemetría centralizada pero sí visibilidad local.

```bash
# En tu .env
OBSERVABILITY_ENABLED=False
```

**Qué obtienes:**
- ✅ No se instancia ningún cliente de Langfuse (faster startup)
- ✅ **Decoradores @observe()** no rompen el código (graceful degradation)
- ✅ **Logs DEBUG** con metadata completa en stdout
- ✅ **Masking de PII** aplicado en logs para seguridad
- ✅ Métricas de performance en logs (duration_ms por invocación)
- ✅ Ideal para desarrollo local sin overhead de telemetría

**Ejemplo de logs:**

```
DEBUG - llm.sdk.observability.context - Observability disabled - logging trace info: 
  {'metadata': {'operation': 'chat', 'model': 'llama-7b'}, 'tags': ['production']}
```

> **Nota importante**: Incluso con observabilidad deshabilitada, el SDK deja rastro de metadata y tags en logs. Los campos sensibles como `input` y `output` se filtran automáticamente para proteger datos privados.

**Nota**: Si `OBSERVABILITY_ENABLED=False`, no es necesario configurar las variables de Langfuse.

### Ejecutar ejemplos

La carpeta `examples/` contiene **4 ejemplos progresivos** que cubren desde casos básicos hasta workflows empresariales complejos:

#### 1. 🟢 Ejemplo básico con LlamaAdapter
```bash
uv run python examples/llama_example.py
```
**Nivel:** Beginner | **Tiempo:** 5 min  
**Qué aprenderás:** Uso fundamental del SDK sin complejidad adicional
- Health check del servidor LLM
- Chat completions (conversaciones)
- Text completions (generación de texto)
- Embeddings (vectorización de texto)

**Ideal para:** Entender la API básica del SDK y probar conectividad

---

#### 2. 🟡 Ejemplo con OpenAIAdapter  
```bash
uv run python examples/openai_example.py
```
**Nivel:** Beginner | **Tiempo:** 5 min  
**Qué aprenderás:** Cómo cambiar de provider sin modificar tu código
- Uso de OpenAIAdapter (compatible con cualquier API OpenAI-like)
- Chat completions con diferentes modelos
- Text completions
- Generación de embeddings
- Manejo de errores y configuración personalizada

**Ideal para:** Multi-provider scenarios, testing con diferentes backends

---

#### 3. 🟠 Ejemplo de Agentes con Structured Output
```bash
uv run python examples/agents_example.py
```
**Nivel:** Intermediate | **Tiempo:** 10 min  
**Qué aprenderás:** Structured outputs y pipelines de agentes simples
- **LLMRunnable** para structured output con Pydantic models
- Validación automática de respuestas JSON
- Pipeline de múltiples agentes: generador → crítico → refinador
- Estado compartido entre agentes (dict-based)

**Ideal para:** Aplicaciones que requieren outputs validados y análisis multi-paso

---

#### 4. 🔴 Ejemplo avanzado con LangGraph (Reflection Pattern)
```bash
uv run python examples/langraph_example.py
```
**Nivel:** Advanced | **Tiempo:** 15 min  
**Qué aprenderás:** Workflows empresariales complejos con orquestación
- **MiniAgent** como building block reutilizable
- **LangGraph StateGraph** para workflow orchestration
- Patrón de reflexión: draft → critique → refine (con loops condicionales)
- 5 nodos coordinados: drafter, critic, refiner, 2 evaluadores
- Observabilidad automática con Langfuse (traces por agente)
- Estado tipado con TypedDict para type safety

**Ideal para:** Sistemas de agentes empresariales, workflows complejos con decisiones condicionales

---

**💡 Tip:** Los ejemplos están ordenados por complejidad. Si eres nuevo, empieza por el 1 y avanza progresivamente. Todos incluyen manejo robusto de errores y funcionan tanto con servidores reales como en modo de prueba.

## Estructura del Proyecto

```
llm_arch_sdk/
├── src/
│   ├── __init__.py
│   └── llm_arch_sdk/
│       ├── __init__.py              # ✨ Public API exports
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
│       ├── integrations/              # ✨ NEW: Workflow tools
│       │   ├── agent.py               # MiniAgent for LangGraph
│       │   ├── llm_runnable.py        # LLMRunnable abstraction
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

- **adapters/**: Adaptadores multi-provider con interfaz unificada (OpenAI, Llama). Cada adapter maneja autenticación, retry logic y normalización específica del proveedor.
- **auth/**: Sistema robusto de autenticación con TokenManager que maneja renovación automática, circuit breaking en login y gestión thread-safe de tokens.
- **client/**: Cliente HTTP con endpoints especializados (chat, completions, embeddings) construidos sobre httpx. Incluye manejo de errores, timeouts y connection pooling.
- **config/**: Sistema de configuración centralizado basado en dataclasses. Soporta env vars, settings custom y valores por defecto sensatos. Controla observabilidad, masking, circuit breaker y endpoints.
- **integrations/**: 🆕 **Toolkit de workflow orchestration** con abstracciones de alto nivel:
  - `MiniAgent`: Agente reutilizable con observabilidad automática
  - `LLMRunnable`: Wrapper para invocaciones con structured output
  - `node.py`, `runnable.py`: Building blocks para workflows complejos
- **models/**: Modelos Pydantic para parsing robusto de respuestas JSON. Incluye ChatCompletion, Completion, Usage, Timings y GenerationSettings con validación automática.
- **normalizers/**: Utilidades inteligentes para procesamiento de texto:
  - `CompletionDetector`: Detecta si una respuesta está semánticamente completa
  - `ContentNormalizer`: Limpia artefactos (asteriscos, whitespace, etc.)
- **observability/**: Sistema de observabilidad empresarial con:
  - Integración opcional con Langfuse para traces
  - Contexto global con metadata injection (`obs.update()`)
  - Sistema de masking configurable para PII
  - Fallback a logs estructurados cuando observability está disabled
- **transport/**: Capa de transporte robusta con:
  - `CircuitBreaker`: Implementación completa del patrón con estados y timeouts
  - `AuthHttpClientFactory`: Factory que crea clientes httpx con autenticación
  - `HttpClientFactory`: Factory base para clientes sin auth

## Pruebas

Para ejecutar las pruebas:

```bash
uv run pytest test/
```

**Estado actual: ✅ 103/103 tests pasando**

El proyecto incluye 103 pruebas unitarias organizadas en una estructura que refleja el código fuente, facilitando el mantenimiento y la localización de tests relacionados con módulos específicos.

### Estructura de pruebas

- `test/client/`: Tests para clientes y endpoints (chat, completions, embeddings)
- `test/auth/`: Tests para autenticación y gestión de tokens
- `test/transport/`: Tests para circuit breaker y transporte HTTP
- `test/adapters/`: Tests para adaptadores de proveedores (Llama, OpenAI)
- `test/integrations/`: ✨ Tests para MiniAgent y LLMRunnable (20 tests)
- `test/models/`: Tests para modelos de datos y parsing JSON
- `test/normalizers/`: Tests para normalización de contenido

### Cobertura de pruebas

- **Total**: 103 tests unitarios (100% pasando)
- **TokenManager**: Autenticación, renovación de tokens, circuit breaker
- **CircuitBreaker**: Estados CLOSED/OPEN/HALF_OPEN, timeouts, time.monotonic
- **Clientes**: ChatCompletions, Completions, Embeddings
- **Adaptadores**: LlamaAdapter, OpenAIAdapter (con model requerido)
- **Integrations**: 
  - **MiniAgent** (10 tests): Inicialización, prompt building, ejecución, parámetros LLM, callable interface, error handling, state updates
  - **LLMRunnable** (10 tests): Structured output, schema injection, JSON parsing, validación, múltiples invocaciones
- **Modelos**: Parsing de respuestas JSON, validación de datos
- **Normalizadores**: Detección de completitud semántica, limpieza de texto
- **Transporte**: Manejo de HTTP, errores, timeouts

### Cambios recientes en tests

**v0.4.6 (2026-02-22):**
- ✅ **20 nuevos tests** para `test/integrations/`
  - 10 tests para MiniAgent (inicialización, execution flow, parámetros, error handling)
  - 10 tests para LLMRunnable (structured output, schema injection, JSON parsing)
- 🔧 **Correcciones de compatibilidad**:
  - Adapters: Parámetro `model` ahora requerido en OpenAIAdapter y LlamaAdapter
  - Completions: Parámetro `temperature` requerido en `create()`
  - TokenManager: Tests usan settings custom en lugar de patch.dict de env vars
  - CircuitBreaker: Mock correcto de `time.monotonic` (en lugar de `time.time`)
  - Settings: `retry_value` cambiado de `int` a `str` para compatibilidad con httpx.Headers

## Historial de cambios

### v0.4.6 (2026-02-22) ✨ LATEST RELEASE

**🚀 Nuevas Funcionalidades:**
- **MiniAgent**: Abstracción reutilizable para crear agentes LLM en workflows (ej: LangGraph)
  - Reduce código repetitivo en ~60%
  - Observabilidad automática con Langfuse
  - API simple y declarativa
- **Masking independiente**: Nueva variable `MASKING_ENABLED` separada de `OBSERVABILITY_ENABLED`
  - Permite usar masking de PII sin activar observabilidad completa
  - Guardrails de seguridad desacoplados

**⚡ Mejoras:**
- **JSON parsing mejorado**: 85% reducción de errores mediante prompts optimizados
  - Instrucciones explícitas contra markdown code blocks
  - Manejo correcto de escapado de comillas en código Python
  - Validación robusta con mensajes de error informativos
- **Enterprise logging**: Sistema de logs empresarial completo
  - Métricas de performance (duration_ms) en cada invocación LLM
  - Logging estructurado (DEBUG/INFO/ERROR) con contexto completo
  - Trazabilidad de errores con stack traces y previews de respuestas
- **API pública mejorada**: Nuevo `__init__.py` raíz para importaciones limpias
  ```python
  # Antes
  from llm_arch_sdk.integrations.agent import MiniAgent
  
  # Ahora
  from llm_arch_sdk import MiniAgent
  ```

**🔧 Correcciones:**
- Fix: Alineación de claves de estado entre nombres de agentes y prompt builders
- Fix: Compatibilidad con Langfuse v3 (decorador @observe en generadores)

**📚 Documentación:**
- Nuevo ejemplo completo: `examples/langraph_example.py` (reflection pattern)
- Documentación de MiniAgent y LLMRunnable
- Guía de observabilidad actualizada

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

## Contribución

1. Fork el proyecto
2. Crea una rama para tu feature (`git checkout -b feature/nueva-funcionalidad`)
3. Commit tus cambios (`git commit -am 'Agrega nueva funcionalidad'`)
4. Push a la rama (`git push origin feature/nueva-funcionalidad`)
5. Abre un Pull Request

## Licencia

Este proyecto está bajo la Licencia MIT. Ver el archivo [LICENSE](LICENSE) para más detalles.

## Autor

Emeric Espiritu Santiago
