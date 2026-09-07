"""Turning a bare ``403 forbidden`` into a diagnosis.

The gateway is deny-by-default and its ``detail`` says only that access was refused. The SDK knows
something it does not report: exactly which scopes the current token was granted, read back from
the token response. Comparing the two names the missing scope precisely, which turns the most
common integration mistake into a one-line fix instead of a guessing game.

The two mistakes this catches are the ones the platform's own guide calls out as most frequent:
holding an inference scope but no ``model:<id>`` grant, and holding ``inference:read`` while
attempting a stream, which needs ``inference:stream`` instead.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["explain_forbidden"]

READ = "inference:read"
STREAM = "inference:stream"


def explain_forbidden(granted: Sequence[str], *, model: str | None, streaming: bool) -> str | None:
    """Describe what this token is missing, or ``None`` if nothing can be concluded."""
    if not granted:
        # No granted scope was reported, so anything said here would be a guess.
        return None

    held = set(granted)
    required = STREAM if streaming else READ
    missing: list[str] = []

    if required not in held:
        if streaming and READ in held:
            missing.append(
                f"the token holds {READ} but not {STREAM}; streaming needs its own scope and "
                f"holding one does not grant the other"
            )
        else:
            missing.append(f"the token is missing {required}")

    if model and f"model:{model}" not in held:
        missing.append(
            f"the token has no model:{model} grant; an inference scope conveys no model access "
            f"by itself, and model IDs are case-sensitive"
        )

    if not missing:
        return None
    return f"Scope check: {'; '.join(missing)}."
