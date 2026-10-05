"""The pass-through route: the tasks OpenAI has no shape for.

``POST /v1/models/{model}/predict`` serves three modalities --- ``classification``, ``zero_shot``
and ``typed_decision``. Every other route on this gateway is OpenAI-shaped because every task it
serves has an OpenAI endpoint to be shaped like. These do not, and inventing a body for them would
be the gateway deciding, on the engine's behalf, what the engine's API should look like.

So **the body is forwarded to the engine verbatim and its answer comes back verbatim**, and the
shape is not stable even across engines serving the same modality. That is the cost of pass-through
and it is paid by the caller; :attr:`~axonium.models.catalog.Model.payload_schema` in the catalog is
what identifies the shape.

**Two engines can serve one modality and disagree**, and ``tei.predict.v1`` is the first case where
that is not hypothetical. On ``zero_shot``, ``hf-inference.zero-shot-classification.v1`` answers
``{sequence, labels, scores}`` normalised across the candidate labels *you supplied*, while
``tei.predict.v1`` answers scores across the **model's own** classes and has no notion of candidate
labels at all. Both sum to 1, over different things. A caller dispatching on ``modality`` reads one
as the other; dispatching on ``payload_schema`` is what prevents it, and this is the case that
argument was waiting for.

**And ``tei.predict.v1`` has a trap worth reading before you build a batch helper.** Measured
against a live server by the platform team:

.. code-block:: text

    inputs: "a text"                     -> one flat list of {label, score}
    inputs: ["premise", "hypothesis"]    -> ONE PAIR, not a batch of two texts
    inputs: ["a", "b", "c"]              -> 422
    inputs: [["a"], ["b"]]               -> a batch of two single texts -> two lists
    inputs: [["p1","h1"], ["p2","h2"]]   -> a batch of two pairs -> two lists

A batch is **always** a list of lists. A flat array of two strings is read as a single pair and
answers once, in silence; three or more is a ``422``. So the obvious "send my N texts as an array"
is the one form that quietly returns a single wrong answer, and ``[[t] for t in texts]`` is the form
that batches. The per-request cap is set per instance (64 on the reference deployment).

This is also why :class:`~axonium.models.predict.PredictResult` holds the value undecoded: a single
input returns a flat list and a batch returns a list of lists, from the same model and the same
endpoint, so a type that assumed either one would be wrong half the time.

What does *not* pass through is the policy: the model still resolves, ``inference:read`` plus the
specific ``model:<id>`` scope is still required, a dead replica is still skipped, and the request is
still metered and still counts against a spend cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from axonium.errors import InvalidRequestError
from axonium.models.predict import PredictResult
from axonium.transport import dispatch

if TYPE_CHECKING:
    from axonium.client import AsyncAxonium, Axonium

__all__ = ["AsyncPredict", "Predict"]


def _path(model: str) -> str:
    """Validate the model ID and place it in the path.

    The ID is a path *segment* here rather than a body field, which is new on this route and is why
    it is quoted: a slash in a model ID would otherwise address a different endpoint entirely, and
    the gateway would answer about a route rather than about a model.
    """
    if not model or not model.strip():
        raise InvalidRequestError("model is required.")
    return f"/v1/models/{quote(model, safe='')}/predict"


def _check_body(body: Any) -> dict[str, Any]:
    """Refuse a body that is not a JSON object.

    Every engine seen on this route takes an object. A list or a scalar is not refused because it is
    impossible --- it is refused because nothing in the contract describes one, so sending it would
    be guessing on the caller's behalf, and the engine's own 4xx arrives wrapped as
    ``predict-backend-rejected`` with a status that is the engine's rather than the gateway's.

    Note the asymmetry with the *response*, which genuinely can be a list: the request shape is
    something the contract could describe and does, the response shape is the engine's.
    """
    if not isinstance(body, dict):
        raise InvalidRequestError(
            f"The predict body must be a JSON object; got {type(body).__name__}. The engine's own "
            f"contract is named by payload_schema in the catalog."
        )
    return body


class Predict:
    """Call a model on the pass-through route, interpreting nothing."""

    def __init__(self, client: Axonium) -> None:
        self._client = client

    def create(
        self,
        model: str,
        body: dict[str, Any],
        *,
        timeout: float | None = None,
        instance: str | None = None,
        idempotency_key: str | None = None,
    ) -> PredictResult:
        """Send ``body`` to ``model`` unchanged and return its answer unchanged.

        Deliberately **not** a ``classify(text)`` typed per modality. That would promise a
        stability this endpoint does not offer --- ``sst2-clf`` and ``von-decide`` are both
        classifiers and want different payloads. Dispatch on
        :attr:`~axonium.models.catalog.Model.payload_schema`, not on
        :attr:`~axonium.models.catalog.Model.modality`.

        .. code-block:: python

            result = client.predict.create("sst2-clf", {"inputs": "El servicio ha sido excelente"})
            result.value  # [{"label": "POSITIVE", "score": 0.9783}]

        A model that *has* an OpenAI endpoint is refused here with ``400 modality-mismatch`` --- the
        inverse of every other handler's check. Without it the same model would be reachable two
        ways, with two billing paths and two rate-limit budgets, and the one that billed correctly
        would be whichever the caller did not use.
        """
        path = _path(model)
        payload = _check_body(body)
        self._client._preflight(model, "predict")
        response = self._client._send(
            "POST",
            path,
            json=payload,
            model=model,
            instance=instance,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        value, meta = dispatch.parse_value(response)
        return PredictResult(value=value, meta=meta)


class AsyncPredict:
    """Call a model on the pass-through route, interpreting nothing."""

    def __init__(self, client: AsyncAxonium) -> None:
        self._client = client

    async def create(
        self,
        model: str,
        body: dict[str, Any],
        *,
        timeout: float | None = None,
        instance: str | None = None,
        idempotency_key: str | None = None,
    ) -> PredictResult:
        """See :meth:`Predict.create`."""
        path = _path(model)
        payload = _check_body(body)
        await self._client._preflight(model, "predict")
        response = await self._client._send(
            "POST",
            path,
            json=payload,
            model=model,
            instance=instance,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        value, meta = dispatch.parse_value(response)
        return PredictResult(value=value, meta=meta)
