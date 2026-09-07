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

__all__ = ["span"]

logger = logging.getLogger("axonium.otel")

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
    spans are readable by tooling that already understands LLM traffic.
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
