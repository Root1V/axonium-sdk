<!-- not-a-translation: generated from the Python package, so a translation would be stale between every two releases -->
# Referencia de la API de Python

Esta página **no está traducida**, y es a propósito.

La genera [`scripts/render_reference.py`](https://github.com/Root1V/axonium-sdk/blob/main/scripts/render_reference.py)
leyendo lo que el paquete de Python exporta de verdad: cada clase, cada firma y cada docstring salen
del código, no de un documento que alguien mantiene al lado. Hay un test que falla si las dos dejan
de coincidir.

Una traducción de esta página habría que rehacerla en cada *release*, y estaría desfasada entre cada
dos. Eso es exactamente la deriva que el resto de este sitio en español evita: cada página traducida
lleva grabado de qué revisión del original salió, y la compilación falla cuando el inglés cambia y la
traducción no. Una página que no puede cumplir esa regla es mejor no tenerla que tenerla mintiendo.

**La referencia está aquí:** [versión en inglés](../08-reference.html).

Las firmas, los nombres de parámetros y los tipos son idénticos en los dos idiomas — el código no se
traduce — así que lo único que se pierde son las descripciones en prosa.

## El resto de la referencia

Los otros cuatro SDK generan la suya con las herramientas de su propio ecosistema, y tampoco están
traducidas:

- **Go** — [pkg.go.dev](https://pkg.go.dev/github.com/Root1V/axonium-sdk/go)
- **Rust** — [docs.rs](https://docs.rs/axonium/latest/axonium/)
- **Swift** — DocC en el [repositorio](https://github.com/Root1V/axonium-sdk-swift)
- **TypeScript** — los ficheros `.d.ts` más
  [`docs/api.md`](https://github.com/Root1V/axonium-sdk/blob/main/typescript/docs/api.md), que
  además incluye una
  [guía de migración desde Python](https://github.com/Root1V/axonium-sdk/blob/main/typescript/docs/migrating-from-python.md)

Lo que sí está traducido por completo es la guía: [Primeros pasos](index.md),
[Conceptos](01-concepts.md), [Hacer llamadas](02-calls.md),
[Fallos](03-failure.md), [Configuración](04-configuration.md),
[Operaciones compuestas](05-composed.md), [Probar](06-testing.md) y
[Lo que no hace](07-limits.md).
