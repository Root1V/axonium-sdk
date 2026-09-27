from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "spec"


@pytest.fixture(scope="session")
def spec_dir() -> Path:
    """The shared, language-neutral contract specification directory."""
    return SPEC_DIR


@pytest.fixture(scope="session")
def error_catalog() -> dict[str, Any]:
    """``spec/errors.json``, the cross-language source of truth for the error taxonomy."""
    data: dict[str, Any] = json.loads((SPEC_DIR / "errors.json").read_text())
    return data


def probe_statuses(entry: dict[str, Any]) -> list[int]:
    """The statuses a catalogued error should be exercised at.

    Almost every entry fixes one. ``predict-backend-rejected`` does not -- it keeps whatever the
    engine returned -- so it carries an explicit ``probe_statuses`` list instead, and a guard that
    read ``status`` alone would either crash on the string ``"4xx"`` or, worse, coerce it and test
    a status the platform never sends.
    """
    listed = entry.get("probe_statuses")
    if listed:
        return [int(status) for status in listed]
    return [int(entry["status"])]


def expected_retryable(entry: dict[str, Any], status: int) -> bool:
    """What the catalog says retrying this error at this status should do.

    ``retryable`` is a bool for every entry whose retryability is a property of the error itself.
    ``"by_status"`` marks the one where it is not: the status belongs to an engine this gateway
    only wraps, so the answer has to be read off the status rather than off the name.
    """
    declared = entry["retryable"]
    if declared == "by_status":
        return status in set(entry["retryable_statuses"])
    assert isinstance(declared, bool), f"{entry['suffix']}: unrecognised retryable {declared!r}"
    return declared


@pytest.fixture
def config_kwargs() -> dict[str, str]:
    """Minimal valid configuration, pointing at hosts that are never actually contacted."""
    return {
        "gateway_base_url": "https://gateway.test.invalid",
        "client_id": "test-client",
        "client_secret": "test-secret",
    }


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's real AXONIUM_* environment out of the test run.

    Without this, whether the suite passes depends on what happens to be exported in the shell,
    which is exactly the class of bug the configuration layer exists to prevent.
    """
    import os

    for key in list(os.environ):
        if key.startswith("AXONIUM_"):
            monkeypatch.delenv(key, raising=False)
