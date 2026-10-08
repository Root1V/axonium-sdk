"""Optional OpenTelemetry spans.

Vendor-neutral and off by default. The platform runs its own tracing; this exists only to let a
caller's traces join up with it, so it emits spans and nothing else — no metrics, no exporter
configuration, no global provider setup. Those belong to the application.

Requires the ``axonium[otel]`` extra. OpenTelemetry is imported lazily, so a base install carries
no dependency on it and no import cost.

**Trace context is not propagated outbound.** The gateway never reads W3C ``traceparent`` — under
OTEL mode it deliberately ignores it and always starts a new span, and in legacy mode it only ever
consults ``X-Trace-ID``. Sending it would be noise that implies a link the platform will not make.
Correlation runs the other way: the gateway's ``X-Request-ID`` and ``X-Trace-ID`` come back on
every response and are recorded on the span here.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from typing import Any

__all__ = ["PROVIDER_NAME", "operation_name", "span"]

logger = logging.getLogger("axonium.otel")


#: ``gen_ai.provider.name``, which replaced the deprecated ``gen_ai.system``.
#:
#: The convention's enumeration names model providers -- ``openai``, ``anthropic``, ``groq`` and so
#: on -- and this platform is not one of them, so this is a **custom value**, which the convention
#: permits when no well-known one applies. It deliberately does not name the inference engine.
#: Argus maps this attribute to ``llama.cpp`` / ``vllm`` / ``ollama``, which is correct for the
#: gateway's own spans because the gateway knows which backend served the request. A client does
#: not: no response header carries it. ``server.address`` goes on the span instead, which is what
#: the convention names as the way to identify the actual system behind an OpenAI-compatible
#: endpoint.
PROVIDER_NAME = "prometheus-gateway"

#: Route to ``gen_ai.operation.name``. The shared table is ``spec/otel-genai.json``, and
#: ``tests/test_observability.py`` asserts this map against it -- three SDKs emit spans, and a table
#: copied by hand into three languages is how ``AXO-139`` produced five divergent rate-limit scope
#: lists that nothing could catch.
_OPERATIONS = {
    "/v1/chat/completions": "chat",
    "/v1/embeddings": "embeddings",
    # Not `retrieval`: that well-known value is RAG retrieval and carries `gen_ai.data_source.id`
    # to say which corpus was read. A reranker reads no corpus; it scores documents the caller
    # already holds.
    "/v1/rerank": "rerank",
    # The enumeration has no image value, and `generate_content` means multimodal generation in
    # Gemini-shaped APIs.
    "/v1/images/generations": "image_generation",
    "/v1/predict": "predict",
}


def operation_name(path: str) -> str | None:
    """``gen_ai.operation.name`` for a route, or ``None`` where no GenAI operation happened.

    ``/v1/models`` and ``/v1/models/mine`` return ``None`` on purpose. A catalog listing reaches no
    model, so a ``gen_ai.*`` span for it would carry a null model and an operation name the
    convention does not have -- and until this existed those spans did carry ``gen_ai.system``,
    which put them in GenAI aggregations as inference calls that somehow used no tokens.
    """
    return _OPERATIONS.get(path)


_tracer: Any = None
_unavailable = False


def _get_tracer() -> Any:
    global _tracer, _unavailable
    if _tracer is not None or _unavailable:
        return _tracer

    try:
        from opentelemetry import trace
    except ImportError:
        logger.debug("OpenTelemetry not installed; install axonium[otel] to emit spans")
        _unavailable = True
        return None

    from axonium import __version__

    _tracer = trace.get_tracer("axonium", __version__)
    return _tracer


@contextmanager
def _record(name: str, attributes: dict[str, Any]) -> Iterator[Any]:
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(name) as active:
        for key, value in attributes.items():
            if value is not None:
                active.set_attribute(key, value)
        yield active


def span(name: str, *, enabled: bool, **attributes: Any) -> Any:
    """A span for one operation, or a no-op when tracing is off or unavailable.

    Attribute names follow the OpenTelemetry GenAI semantic conventions where one applies, so the
    spans are readable by tooling that already understands LLM traffic. The shared table of which
    attribute carries what is ``spec/otel-genai.json``.
    """
    if not enabled:
        return nullcontext()
    return _record(name, attributes)


def record_response(active: Any, *, request_id: str | None, trace_id: str | None) -> None:
    """Attach the gateway's correlation IDs to an active span."""
    if active is None:
        return
    if request_id:
        active.set_attribute("prometheus.request_id", request_id)
    if trace_id:
        active.set_attribute("prometheus.trace_id", trace_id)
