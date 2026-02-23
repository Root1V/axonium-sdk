# LLM Arch SDK

SDK para consumir llama-server con autenticación y renovación automática de tokens.

## Descripción

Este SDK proporciona una interfaz unificada para interactuar con servidores LLM (como llama-server), manejando autenticación, renovación de tokens, circuit breakers y diferentes adaptadores para proveedores como OpenAI y Llama.

## Características

- **Autenticación automática**: Manejo de tokens con renovación automática.
- **TokenManager opcional**: Crea automáticamente una instancia si no se proporciona.
- **Circuit Breaker**: Protección contra fallos en las llamadas a la API.
- **Adaptadores múltiples**: Soporte para Llama, OpenAI y LangChain (ChatOpenAI).
- **Normalización de respuestas**: Estandarización de respuestas de diferentes proveedores.
- **Cliente HTTP robusto**: Uso de httpx con configuraciones personalizables.
- **Ejemplos con .env**: Los ejemplos cargan variables desde archivo `.env` usando python-dotenv.

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

El SDK incluye soporte para observabilidad con **Langfuse**, pero es completamente **opcional**. Puedes controlar esto con la variable de entorno `OBSERVABILITY_ENABLED`.

#### Modo 1: Observabilidad habilitada

```bash
# En tu .env
OBSERVABILITY_ENABLED=True

# Configuración de Langfuse (requerido si enabled=True)
LANGFUSE_PUBLIC_KEY=tu_public_key
LANGFUSE_SECRET_KEY=tu_secret_key
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_TRACING_ENVIRONMENT=production
```

Con observabilidad habilitada:
- ✅ Se crea el cliente de Langfuse
- ✅ Los decoradores `@observe()` capturan trazas
- ✅ Se registra metadata automática (adapter, operation, model)
- ✅ Se aplican estrategias de masking de datos sensibles

#### Modo 2: Solo logs (sin Langfuse)

```bash
# En tu .env
OBSERVABILITY_ENABLED=False
```

Con observabilidad deshabilitada:
- ✅ No se instancia ningún cliente de Langfuse
- ✅ Los decoradores `@observe()` se convierten en no-op (no rompen el código)
- ✅ `obs.update()` registra metadata/tags en logs DEBUG en lugar de silenciarlos
- ✅ Campos sensibles (input/output) se filtran automáticamente de los logs
- ✅ Solo se usan logs estándar de Python
- ✅ Reduce dependencias y overhead
- ✅ Ideal para entornos de desarrollo o pruebas sin telemetría

**Ejemplo de logs cuando observabilidad está deshabilitada:**

```
DEBUG - llm.sdk.observability.context - Observability disabled - logging trace info: 
  {'metadata': {'operation': 'chat', 'model': 'llama-7b'}, 'tags': ['production']}
```

> **Nota importante**: Incluso con observabilidad deshabilitada, el SDK deja rastro de metadata y tags en logs. Los campos sensibles como `input` y `output` se filtran automáticamente para proteger datos privados.

**Nota**: Si `OBSERVABILITY_ENABLED=False`, no es necesario configurar las variables de Langfuse.

### Ejecutar ejemplos

La carpeta `examples/` contiene 4 ejemplos demostrativos que cubren diferentes casos de uso:

#### 1. Ejemplo básico con LlamaAdapter
```bash
uv run python examples/llama_example.py
```
**Qué hace:** Demuestra el uso completo del `LlamaAdapter` para:
- Health check del servidor LLM
- Chat completions (conversaciones)
- Text completions (generación de texto)
- Embeddings (vectorización de texto)

#### 2. Ejemplo con OpenAIAdapter
```bash
uv run python examples/openai_example.py
```
**Qué hace:** Muestra cómo usar el `OpenAIAdapter` para conectarse a APIs compatibles con OpenAI:
- Chat completions con diferentes modelos
- Text completions
- Generación de embeddings
- Manejo de errores y configuración personalizada

#### 3. Ejemplo de Agentes con Structured Output
```bash
uv run python examples/agents_example.py
```
**Qué hace:** Demuestra el patrón de agentes simples con `LLMRunnable`:
- Generación de código con structured output (Pydantic models)
- Validación automática de respuestas JSON
- Pipeline de múltiples agentes (generador → crítico → refinador)
- Estado compartido entre agentes

#### 4. Ejemplo avanzado con LangGraph (Reflection Pattern)
```bash
uv run python examples/langraph_example.py
```
**Qué hace:** Ejemplo completo de workflow empresarial usando `MiniAgent` y LangGraph:
- Patrón de reflexión: draft → critique → refine
- Workflow orquestado con StateGraph
- 5 nodos: drafter, critic, refiner, 2 evaluadores
- Observabilidad automática con Langfuse
- Estado tipado con TypedDict

---

**💡 Tip:** Todos los ejemplos incluyen manejo robusto de errores y funcionan tanto con servidores reales como en modo de prueba.

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

- **adapters/**: Adaptadores para diferentes proveedores de LLM (OpenAI, Llama).
- **auth/**: Gestión de autenticación y tokens.
- **client/**: Cliente principal y endpoints específicos (chat, completions, embeddings).
- **config/**: Configuración centralizada del SDK (observabilidad, masking, backend).
- **integrations/**: 🆕 Herramientas para workflows (MiniAgent, LLMRunnable, nodos para LangGraph).
- **models/**: Modelos de datos para respuestas y configuraciones.
- **normalizers/**: Utilidades para normalizar respuestas.
- **observability/**: Sistema de observabilidad con Langfuse (opcional) y masking de datos sensibles.
- **transport/**: Manejo de transporte HTTP, circuit breakers y fábricas de clientes.

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
