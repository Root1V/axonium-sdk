# Observabilidad - Mejores Prácticas Enterprise

## Criterio: Tags vs Metadata

### Tags
**Propósito**: Filtrado rápido e indexación. 

**Características**:
- Strings simples, planos
- Inmutables por naturaleza
- Indexados para búsqueda rápida
- Limitados en cantidad (< 10 por trace recomendado)
- Solo información de **clasificación de alto nivel**

**Qué SÍ incluir en tags**:
- Environment: `environment:production`, `environment:staging`
- Model: `model:gpt-4`, `model:llama-7b` (para comparar modelos)
- Criticidad: `critical`, `high-priority`, `low-priority`
- Categorías de negocio: `premium-user`, `free-tier`, `enterprise-customer`
- Features: `beta-feature`, `experimental`

**Qué NO incluir en tags**:
- Información técnica detallada (versiones, URLs, timeouts)
- Valores de parámetros (temperature, n_predict)
- Identificadores técnicos internos (adapter type, operation type)
- Datos estructurados

### Metadata
**Propósito**: Contexto técnico completo y debugging.

**Características**:
- Objetos estructurados (JSON)
- Valores de cualquier tipo (string, number, boolean, object)
- No indexados (más lento para búsqueda)
- Sin límite práctico de cantidad
- Información técnica **detallada**

**Qué SÍ incluir en metadata**:
- Información del SDK: `sdk.name`, `sdk.version`, `llm.base_url`, `llm.timeout`
- Información de operación: `adapter.type`, `operation.type`, `operation.model`
- Parámetros de configuración: `temperature`, `n_predict`, `max_tokens`
- IDs de negocio: `user_id`, `tenant_id`, `job_id`
- Contexto de flujo: `flow`, `step`, `retry_count`

**Qué NO incluir en metadata**:
- Información que ya está en tags (duplicación innecesaria)

## Implementación en el SDK

### Detección Automática

El SDK **detecta automáticamente** la siguiente información:
- `adapter.type`: Detectado desde el adapter que crea el cliente (`llama`, `openai`, `langchain`)
- `operation.type`: Detectado desde el método llamado (`completion`, `chat`, `embedding`)
- `operation.model`: Extraído del parámetro `model` de la petición
- `sdk.name`, `sdk.version`: Información del SDK
- `llm.base_url`, `llm.timeout`: Configuración del cliente

**El consumidor NO necesita pasar esta información.**

### Uso Simple

```python
from axonium.adapters.llama_adapter import LlamaAdapter
from langfuse import propagate_attributes

# 1. Establecer contexto global (session, user, etc)
propagate_attributes(
    session_id="usr-123-session-456",
    user_id="usr-123"
)

# 2. Crear cliente
client = LlamaAdapter.client()

# 3. Usar el cliente - metadata técnica es automática
response = client.chat.create(
    model="llama-7b",
    messages=[{"role": "user", "content": "Hola"}],
    # Opcional: solo metadata de negocio custom
    trace_metadata={"tenant_id": "acme-corp", "flow": "customer-support"},
    # Opcional: solo tags de negocio custom
    trace_tags=["premium-user", "critical-flow"]
)
```

### Metadata Final en Langfuse

```json
{
  "sdk.name": "axonium",
  "sdk.version": "0.4.0-dev",
  "llm.base_url": "https://api.example.com",
  "llm.timeout": 30,
  "adapter.type": "llama",
  "operation.type": "chat",
  "operation.model": "llama-7b",
  "temperature": 0.7,
  "max_tokens": 100,
  "tenant_id": "acme-corp",
  "flow": "customer-support"
}
```

### Tags Finales en Langfuse

```json
[
  "environment:production",
  "model:llama-7b",
  "premium-user",
  "critical-flow"
]
```

## Beneficios

1. **Sin Duplicación**: Información técnica solo en metadata, clasificación solo en tags
2. **Automático**: El consumidor no necesita conocer detalles internos del SDK
3. **Eficiente**: Indexación en tags es rápida, metadata es completa pero no necesita índices
4. **Mantenible**: El SDK controla su propia metadata, consumidor solo añade contexto de negocio
5. **Escalable**: Búsquedas rápidas por tags, análisis detallado por metadata

## Anti-patrones a Evitar

❌ **Duplicar información**
```python
# MAL: Información técnica en ambos lugares
trace_metadata={"adapter": "llama", "operation": "chat"}
trace_tags=["adapter:llama", "operation:chat"]
```

✅ **Separación clara**
```python
# BIEN: SDK maneja lo técnico, consumidor añade negocio
trace_metadata={"customer_tier": "enterprise"}
trace_tags=["high-priority"]
```

❌ **Tags con información variable**
```python
# MAL: Tags no son para datos variables
trace_tags=[f"temperature:{temperature}", f"tokens:{n_predict}"]
```

✅ **Metadata para valores**
```python
# BIEN: Metadata para configuración
trace_metadata={"temperature": temperature, "n_predict": n_predict}
```

❌ **Pedir al consumidor info que el SDK ya tiene**
```python
# MAL: Consumidor tiene que saber detalles internos
trace_metadata=build_sdk_metadata(adapter="llama", operation="chat", model="llama-7b")
```

✅ **SDK detecta automáticamente**
```python
# BIEN: SDK se encarga de lo técnico
client.chat.create(model="llama-7b", messages=[...])
```
