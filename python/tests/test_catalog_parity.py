"""The error catalog and this SDK's mapping have to agree, and a test has to say so.

A contract that can drift from the code without anyone noticing is not a contract. Go has had this
guard from the start and it earned its keep immediately: when the platform added two token errors
to ``spec/errors.json``, Go refused the change until both had a mapping, and refused again until
their retryability matched. Python and Rust had no such guard — Rust silently shipped one of them
as not retryable, and only a hand-written test caught it. This is the missing half.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from axonium.errors import _BY_SUFFIX, error_from_response

CATALOG = json.loads((Path(__file__).resolve().parents[2] / "spec" / "errors.json").read_text())
GATEWAY_ERRORS: list[dict[str, Any]] = CATALOG["gateway_errors"]


def test_the_catalog_is_not_empty() -> None:
    # Every assertion below is vacuously true against an empty list, which is exactly how a guard
    # like this stops guarding without failing.
    assert GATEWAY_ERRORS


@pytest.mark.parametrize("entry", GATEWAY_ERRORS, ids=[e["suffix"] for e in GATEWAY_ERRORS])
def test_every_catalogued_error_maps_to_a_class(entry: dict[str, Any]) -> None:
    suffix = entry["suffix"]
    assert suffix in _BY_SUFFIX, f"{suffix} is in spec/errors.json but this SDK maps no class"

    error = error_from_response(
        status=entry["status"],
        body={
            "type": f"https://gateway.example/errors/{suffix}",
            "title": suffix,
            "detail": "something went wrong",
        },
    )

    assert isinstance(error, _BY_SUFFIX[suffix]), f"{suffix} did not build its own class"
    assert error.retryable is entry["retryable"], (
        f"{suffix}: retryable is {error.retryable} here, {entry['retryable']} in the catalog"
    )
