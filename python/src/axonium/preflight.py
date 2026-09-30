"""Client-side checks run before a request is sent.

The gateway's modality validation is one-directional: calling ``/v1/embeddings`` with a
text model is rejected, but calling ``/v1/chat/completions`` with an *embedding* model is not —
it returns ``200`` with degenerate output, which the caller pays for. A mistyped model ID that
happens to land on the wrong kind of model therefore costs money and produces nonsense instead of
an error.

Since the catalog already says what modality each model has, that mistake can be caught before the
request leaves. Off by default: this is a guard rail, not a correctness requirement, and enabling
it costs one catalog request per client.
"""

from __future__ import annotations

import logging

from axonium.errors import ModalityMismatchError
from axonium.models.catalog import Model, ModelList

__all__ = ["ENDPOINT_MODALITIES", "check_model"]

#: Which model modalities each endpoint can serve. Vision models are called on the chat endpoint
#: like text models, which is why chat accepts both.
ENDPOINT_MODALITIES: dict[str, frozenset[str]] = {
    "chat": frozenset({"text", "vision"}),
    "embeddings": frozenset({"embedding"}),
    "images": frozenset({"image"}),
}


logger = logging.getLogger("axonium.preflight")


def check_model(catalog: ModelList | None, model_id: str, *, endpoint: str) -> None:
    """Raise if ``model_id`` cannot serve ``endpoint``, according to the catalog.

    Silent when there is nothing to check against — no catalog, an unrecognized endpoint, or a
    model whose modality the gateway did not report or that this SDK does not know. A guard rail
    that started rejecting valid requests after the platform added a modality would be worse than
    no guard rail at all.
    """
    if catalog is None:
        return

    allowed = ENDPOINT_MODALITIES.get(endpoint)
    if allowed is None:
        return

    entry: Model | None = catalog.get(model_id)
    if entry is None:
        # **Absence is no longer evidence.** This used to raise UnknownModelError, on the grounds
        # that the gateway reports the same thing and refusing locally only saved the round trip.
        # That was true while the catalog was the platform's full public list. Since PRM-167 it
        # contains only the models this token holds a grant for, so a model missing from it has
        # two possible causes and this SDK cannot tell them apart:
        #
        #   not registered at all   -> the gateway answers 400 unknown-model
        #   registered, not granted -> the gateway answers 403 forbidden
        #
        # Raising unknown-model for the second is worse than spending a request. It tells the
        # caller to check the spelling of a name that is spelled correctly, and it pre-empts the
        # 403 whose whole job is to name the missing scope -- the error this SDK works hardest to
        # make useful, through ForbiddenError's hint.
        #
        # So the round trip is spent, and the gateway answers a question only it can answer.
        logger.debug(
            "Model not in this token's catalog; letting the gateway answer",
            extra={"model": model_id, "granted": len(catalog.ids)},
        )
        return

    modality = entry.modality
    known = frozenset().union(*ENDPOINT_MODALITIES.values())
    if modality is None or modality not in known:
        return

    if modality not in allowed:
        raise ModalityMismatchError(
            f"Model {model_id!r} has modality {modality!r}, which the {endpoint} endpoint cannot "
            f"serve (it needs {' or '.join(sorted(allowed))}). The gateway does not reject this "
            f"combination itself — it would return a billable response containing nonsense.",
            status=400,
            type_suffix="modality-mismatch",
        )
