<!-- translated-from: 07-limits.md sha256:c40620cfa2 -->
# Lo que no hace

> **¿Dónde se para esto?**

La página más fácil de no escribir y la que más tiempo ahorra. Si alguno de estos límites no te vale,
eso es útil descubrirlo ahora y no tres días después.

## No normaliza respuestas

Las respuestas de inferencia se pasan casi verbatim. Los SDK modelan los campos que el contrato
garantiza, mantienen el resto accesible como `raw`, y **no** reescriben la salida específica de un
backend en un vocabulario propio.

Es deliberado. Los backends son heterogéneos, y una capa de normalización dentro de cinco SDK
distintos son cinco implementaciones de la misma opinión, separándose. La normalización va por encima
del SDK, en el framework que consuma los modelos, donde hay una sola.

## No reintenta streams

Nunca, y no hay bandera. Un reintento después de haber entregado salida parcial es una generación
nueva y facturable, no una reanudación. El gateway tampoco los reintenta.

Si un stream falla, recibes `StreamInterruptedError` con lo que llegó. Decidir si empezar otro es
tuyo, porque solo tú sabes si la salida parcial se usó.

## No corre un cortacircuitos

El gateway corre uno por backend y reporta `503 backend-unavailable` con un `Retry-After` calculado
del tiempo real de recuperación. Un segundo cortacircuitos en el cliente se abriría con señales que
el servidor ya contó, y con peor información.

Lo que sí hay es más pequeño: un registro de enfriamiento que respeta el `Retry-After` y falla rápido
en local hasta que expira. Si quieres `CLOSED`/`OPEN`/`HALF_OPEN` completo, ponlo encima del SDK.

## No registra prompts ni completions

A ningún nivel, y no hay bandera para activarlo. Por ahí se filtran los datos personales, y una
bandera para eso es una bandera que alguien activa durante un incidente y nadie desactiva después.
La cabecera `Authorization` tampoco se registra nunca.

La correlación es por `request_id`, `trace_id` e `instance_id`, que van en cada respuesta.

## No configura tu logging

`NullHandler` en Python, ningún subscriber instalado en Rust, un `slog.Logger` inyectado en Go. Una
biblioteca que llama a `basicConfig()` pisa la configuración de quien la hospeda.

La consecuencia es real y merece decirse: **si no has enganchado un handler, no vas a ver los logs
del SDK** — incluida la línea que dice que está esperando un `429`. Por eso la espera también viaja
como dato en la respuesta. Ver [Fallos](03-failure.md).

## No lee tu `.env`

Cargarlo es decisión de la aplicación. Ver [Configuración](04-configuration.md).

## No reporta metadatos en los fallos

Una llamada que esperó 90 segundos en tres intentos y después falló no reporta nada de eso en Python,
Go, Rust ni Swift: la excepción no lleva `ResponseMeta`. Los éxitos llevan `waited_s` y `attempts`;
los fallos no. Es un hueco conocido en el roadmap, no una decisión de diseño.

**TypeScript sí lo contesta**, porque sus errores ya llevan `meta` — `error.meta.waitedMs` y
`error.meta.attempts` se estampan antes de que el error salga.

## No propaga el contexto de traza W3C

No se manda `traceparent` por defecto. El comportamiento documentado del gateway es que un
`X-Trace-ID` del cliente se ignora o se honra condicionalmente según el modo de despliegue, y la
propagación W3C no está documentada en absoluto. Mandar una cabecera que puede descartarse en
silencio produce trazas con huecos invisibles, que es peor que no propagar.

Los spans de OpenTelemetry están disponibles tras un extra opcional, apagados por defecto, solo
spans.

## No valida tus prompts

Ni filtrado de contenido, ni detección de datos personales, ni conteo de tokens antes de enviar. La
generación anterior de este SDK hacía algo de eso y pertenece a otro sitio: el SDK no puede conocer
tu política, y una implementación parcial de una invita a creer que está completa.

Lo que sí valida es lo que el **contrato** restringe: rangos de parámetros, roles de los mensajes,
longitud de la clave de idempotencia, y URLs de imagen con esquema `http(s)` — esta última rechazada
con un mensaje explícito de política SSRF y no con un error de validación genérico.

## No es un framework

Ni bucle de agente, ni abstracciones de workflow, ni constructores de cadenas, ni puentes a LangChain
o LangGraph. La generación anterior los tenía y fueron lo primero que se pudrió, porque codificaban
opiniones sobre orquestación que cambiaban más rápido que el transporte.

Esto es un cliente de transporte. Construye el bucle que necesites; [Operaciones
compuestas](05-composed.md) enseña cómo se ven las piezas juntas.

Siguiente: [Referencia de la API de Python](08-reference.md).
