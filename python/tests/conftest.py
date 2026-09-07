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


@pytest.fixture
def config_kwargs() -> dict[str, str]:
    """Minimal valid configuration, pointing at hosts that are never actually contacted."""
    return {
        "auth_base_url": "https://auth.test.invalid",
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
