"""The result of a pass-through ``predict`` call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from axonium.models.common import ResponseMeta

__all__ = ["PredictResult"]


@dataclass(frozen=True, slots=True)
class PredictResult:
    """Whatever the engine returned, undecoded, and the correlation metadata beside it.

    A dataclass rather than a pydantic model because there is no schema to validate against: the
    body is the engine's and the gateway forwards it verbatim. A model with ``extra="allow"`` would
    still impose that the top level is an *object*, and one of the three live shapes is not.
    """

    #: The decoded body. **Not necessarily a dict** --- measured against a live deployment:
    #:
    #: .. code-block:: text
    #:
    #:     sst2-clf     [{"label": "POSITIVE", "score": 0.978}]   <- a top-level list
    #:     von-decide   {"sequence": ..., "labels": [...], "scores": [...]}
    #:     laya-decide  {"model": ..., "answers": {...}, "usage": {...}, "routing": {...}}
    #:
    #: so annotating this ``dict[str, Any]`` would have been wrong about the first engine the
    #: platform shipped on this route.
    value: Any

    #: Request and trace IDs, and the rate-limit budget, as of this response. The budget here is
    #: ``predict``, shared by all three pass-through modalities rather than one each.
    meta: ResponseMeta
