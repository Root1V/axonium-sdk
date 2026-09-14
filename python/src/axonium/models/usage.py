"""What one of your own requests was charged.

The gateway records one usage row per response it returned — never per attempt, since it pays for
its own internal failover. This is that row, for a single request, readable with any authenticated
token rather than an admin one.
"""

from __future__ import annotations

from axonium.models.common import APIObject, Usage

__all__ = ["RequestUsage"]


class RequestUsage(APIObject):
    """The billing record for one request.

    Every field is optional. The gateway guarantees the row exists for a billed request, not which
    columns a given deployment populates — ``cost_usd`` comes back ``None`` where no price is
    configured, and new columns are appended over time.
    """

    #: The ``request_id`` this row describes, which should match what was asked for.
    request_id: str | None = None
    model: str | None = None
    #: ``"chat"``, ``"embeddings"``, ``"images"`` — what kind of request was billed.
    request_kind: str | None = None

    #: Token counts, mirroring an inference response field for field. ``cache_read_tokens`` is a
    #: subset of ``prompt_tokens``, the same convention as everywhere else, so a row and the
    #: response it describes can be compared without arithmetic.
    usage: Usage | None = None

    image_count: int | None = None

    #: Whether the caller received less than the whole answer. Derived from
    #: :attr:`termination_reason` by the gateway and never assigned separately, so the two cannot
    #: disagree.
    interrupted: bool | None = None

    #: ``"complete"``, ``"upstream_error"`` or ``"client_disconnected"`` today.
    #:
    #: Deliberately a plain string rather than an enum. The platform proposed a fourth value this
    #: week and withdrew it; the next one may not be withdrawn, and a closed enum would turn a new
    #: value into a parse failure for a caller who only wanted the token counts.
    termination_reason: str | None = None

    #: ``None`` where the deployment has no price configured, which is not the same as free.
    cost_usd: float | None = None

    #: Which replica served it. The value to quote when asking the platform team about this row.
    instance_id: str | None = None

    created_at: str | None = None
