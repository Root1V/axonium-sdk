"""Client-side checks run before a request is sent.

The gateway **used to** accept chat on an embedding model and answer ``200`` with degenerate,
billable output, while ``/v1/embeddings`` rejected the wrong modality outright. That asymmetry is
why this check exists, and it is gone: RM-66 made the server-side check hold in every direction, and
measuring all six wrong-modality combinations against a live deployment now returns
``400 modality-mismatch`` for each of them.

So this is a **typo-catcher and a saved round trip**, not a correctness guard, and what it saves is
a request and a rate-limit unit rather than a wasted generation. The spec says as much where it
records the fix: a client-side check of this kind "can stay", because the server-side guarantee now
holds without it. Still off by default, and more clearly so than before: enabling it costs one
catalog request per client to avoid one refused request.

This file is also the standing reminder that a guard whose *premise* expires does not fail — the
code stayed correct and every test stayed green while the reason in the docstring became false. The
guide documenting RM-66 was vendored into this repo and read for its error-catalog rows; this prose
was not re-read against it.
"""

from __future__ import annotations

import logging

from axonium.errors import ModalityMismatchError
from axonium.models.catalog import Model, ModelList

__all__ = ["ENDPOINT_MODALITIES", "check_model"]

#: Which model modalities each endpoint can serve. Vision models are called on the chat endpoint
#: like text models, which is why chat accepts both.
#:
#: **Every endpoint that calls :func:`check_model` must appear here**, and this is not a style
#: rule. The check below ignores a modality it does not recognise, and it recognises exactly the
#: union of these sets -- so an endpoint missing from this table does not get a weaker check, it
#: gets none, and neither does any modality only that endpoint accepts. ``rerank`` shipped that
#: way: :meth:`Rerank.create` has always called ``_preflight(model, "rerank")``, there was no
#: ``"rerank"`` key, and so for months the call could not refuse anything -- including the case its
#: own docstring promised to catch. ``test_preflight.py`` now fails if an endpoint calls in without
#: a row.
ENDPOINT_MODALITIES: dict[str, frozenset[str]] = {
    "chat": frozenset({"text", "vision"}),
    "embeddings": frozenset({"embedding"}),
    "images": frozenset({"image"}),
    "rerank": frozenset({"rerank"}),
    # The three pass-through modalities (spec §3.10). They share one endpoint and one rate-limit
    # budget, and the check here is the useful direction: the gateway refuses a chat model on
    # /predict itself with 400 modality-mismatch, but sends a classification model to the chat
    # endpoint without complaint.
    "predict": frozenset({"classification", "zero_shot", "typed_decision"}),
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
            f"serve (it needs {' or '.join(sorted(allowed))}). The gateway refuses this "
            f"combination too, with the same 400 modality-mismatch; this was refused locally to "
            f"save the request and the rate-limit unit.",
            status=400,
            type_suffix="modality-mismatch",
        )
