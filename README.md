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
- **Observabilidad con Langfuse**: Integración nativa con Langfuse para trazabilidad distribuida.

## Observabilidad y Trazabilidad

### Propagación automática de trace_id

El SDK incluye integración nativa con **Langfuse** para observabilidad y trazabilidad distribuida. Una característica clave es la **propagación automática del trace_id** a través de headers HTTP.

#### ¿Cómo funciona?

Cuando usas el SDK dentro de un contexto decorado con `@observe()` de Langfuse:

1. El SDK captura automáticamente el `trace_id` del contexto activo de Langfuse
2. Este `trace_id` se agrega como header HTTP `X-Langfuse-Trace-Id` en **todas** las peticiones al backend LLM
3. El backend puede extraer este header y correlacionar sus propias trazas con las del SDK

#### Beneficios

- **Correlación end-to-end**: Conecta las trazas del SDK con las del servidor LLM
- **Debugging facilitado**: Sigue una petición completa desde el cliente hasta el servidor y viceversa
- **Sin configuración adicional**: La propagación es completamente automática
- **Separación de responsabilidades**: Tags para filtrado (environment, model), metadata para detalles técnicos

#### Ejemplo de uso

```python
from langfuse import observe
from llm_arch_sdk.adapters.llama_adapter import LlamaAdapter

@observe()  # El trace_id de esta función se propagará automáticamente
def process_with_llm(prompt: str):
    client = LlamaAdapter().client()
    response = client.completions.create(
        model="llama-7b",
        prompt=prompt,
        max_tokens=100
    )
    return response.content

# El backend LLM recibirá el header: X-Langfuse-Trace-Id: <trace_id>
```

Para más detalles, consulta el ejemplo completo en [`examples/langfuse_example.py`](examples/langfuse_example.py).

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

### Instalación en el cliente desde repositorio local

1. Clona el respoitorio en la version que requieras
```
git fetch --tags && git checkout v0.3.0
```

2. Crea el paquete del sdk
```
uv build
```

3. Copia el sdk compilado a la carpeta de repo (opcional)
```
cp /llm_arch_sdk/dist/llm_arch_sdk-0.3.0* /opt/python-repo/
```

4. Agrega el sdk en tu proyecto y sincroniza las dependencias
```
uv add --find-links /opt/python-repo/ llm-arch-sdk
uv sync --find-links /opt/python-repo/
```

5. Otra alternativa de instalacion usando "pip"
```
pip install --find-links=/opt/python-repo llm_arch_sdk
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

### Ejecutar ejemplos

#### Ejemplo básico con Llama
```bash
uv run python examples/basic_usage.py
```

#### Ejemplo con OpenAI
```bash
uv run python examples/openai_example.py
```

#### Ejemplo con LangChain
```bash
uv run python examples/langchain_example.py
```

Estos ejemplos incluyen manejo de errores y funcionan tanto con servidores reales como con configuraciones de prueba.

## Estructura del Proyecto

```
llm_arch_sdk/
├── src/
│   ├── __init__.py
│   └── llm_arch_sdk/
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── lang_adapter.py
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
│       │   ├── helpers.py
│       │   ├── langfuse_client.py
│       │   └── masking.py
│       └── transport/
│           ├── __init__.py
│           ├── auth_http_client_factory.py
│           ├── circuit_breaker.py
│           └── http_client_factory.py
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
- **models/**: Modelos de datos para respuestas y configuraciones.
- **normalizers/**: Utilidades para normalizar respuestas.
- **transport/**: Manejo de transporte HTTP, circuit breakers y fábricas de clientes.

## Pruebas

Para ejecutar las pruebas:

```bash
uv run pytest test/
```

El proyecto incluye 90 pruebas unitarias organizadas en una estructura que refleja el código fuente, facilitando el mantenimiento y la localización de tests relacionados con módulos específicos.

### Estructura de pruebas

- `test/client/`: Tests para clientes y endpoints (chat, completions, embeddings)
- `test/auth/`: Tests para autenticación y gestión de tokens
- `test/transport/`: Tests para circuit breaker y transporte HTTP
- `test/adapters/`: Tests para adaptadores de proveedores (Llama, OpenAI)
- `test/models/`: Tests para modelos de datos y parsing JSON
- `test/normalizers/`: Tests para normalización de contenido

### Cobertura de pruebas

- **Cobertura de pruebas**: 90 tests unitarios
- **TokenManager**: Autenticación, renovación de tokens, circuit breaker.
- **CircuitBreaker**: Estados CLOSED/OPEN/HALF_OPEN, timeouts.
- **Clientes**: ChatCompletions, Completions, Embeddings.
- **Adaptadores**: LlamaAdapter, OpenAIAdapter, LangChainAdapter.
- **Modelos**: Parsing de respuestas JSON, validación de datos.
- **Normalizadores**: Detección de completitud semántica, limpieza de texto.
- **Transporte**: Manejo de HTTP, errores, timeouts.

## Historial de cambios

### v0.4.0 (En desarrollo)
- 🚀 Nuevo adaptador LangChainAdapter para integración con LangChain
- 📝 Soporte para ChatOpenAI de LangChain
- 🔍 **Propagación automática de trace_id de Langfuse como HTTP header**
  - Captura automática del trace_id desde el contexto de Langfuse
  - Inyección del header `X-Langfuse-Trace-Id` en todas las peticiones HTTP
  - Correlación end-to-end entre trazas del SDK y el backend LLM
- 📊 Separación empresarial: tags para filtrado, metadata para detalles técnicos
- 🏷️ Auto-detección de adapter, operation y model en trazas
- ⚙️ Configuración automática de OTEL_SERVICE_NAME basada en identidad del SDK
- 📦 Actualización de dependencias: `python-dotenv` (antes `dotenv`)
- ✅ 7 nuevos tests unitarios para LangChainAdapter (90 tests totales)
- 🔄 Patrón **kwargs implementado en todos los adaptadores
- 📚 Nuevo ejemplo: `examples/langchain_example.py`
- 📚 Documentación mejorada con ejemplos de observabilidad

### v0.3.0
- ✅ TokenManager ahora es **opcional** en `AuthHttpClientFactory.create()`
- ✅ Se crea automáticamente una instancia si no se proporciona
- ✅ Ejemplos actualizados para usar `.env` con `python-dotenv`
- ✅ Todos los 83 tests pasan correctamente

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
- ✅ 83 tests unitarios
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
