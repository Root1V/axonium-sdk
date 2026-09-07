"""Structured logging.

The SDK logs under the ``axonium`` logger hierarchy and attaches a ``NullHandler``, so it stays
silent until the host application configures logging. Handlers, levels and formatting are the
application's decision, never the library's.

**Prompts, completions and credentials are never logged, and there is no flag to turn that on.**
Correlating a request with the platform's own traces needs the request and trace IDs, not the
content — and a library that can be configured to log prompts is how that content ends up in a log
aggregator nobody audited. Anyone who needs the payload has it in hand at the call site already.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["request_fields"]

logging.getLogger("axonium").addHandler(logging.NullHandler())


def request_fields(
    *,
    method: str | None = None,
    path: str | None = None,
    model: str | None = None,
    status: int | None = None,
    duration_ms: float | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    attempt: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build the ``extra`` mapping for a log record, dropping anything unset.

    Every field here is metadata about the exchange rather than its content, which is what makes
    the whole set safe to emit at any level.
    """
    fields: dict[str, Any] = {
        "method": method,
        "path": path,
        "model": model,
        "status": status,
        "duration_ms": None if duration_ms is None else round(duration_ms, 2),
        "request_id": request_id,
        "trace_id": trace_id,
        "attempt": attempt,
        **extra,
    }
    return {key: value for key, value in fields.items() if value is not None}
