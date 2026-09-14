"""The public surface, and the invariants that keep it honest.

Every one of these caught something real. Six error classes added after ``1.0.0rc1`` were exported
from ``axonium.errors`` but never from the package itself, so a caller writing the natural
``from axonium import IdempotencyInProgressError`` got an ImportError. Nothing failed in CI,
because every test imported from the submodule.
"""

from __future__ import annotations

import axonium
from axonium import errors


def test_every_error_is_importable_from_the_package() -> None:
    # A caller should not have to know which submodule an error lives in. This is the invariant
    # that the six missing classes violated.
    declared = {name for name in errors.__all__ if name.endswith(("Error", "Warning"))}
    exported = {name for name in axonium.__all__}

    missing = sorted(declared - exported)
    assert not missing, f"declared in axonium.errors but not importable from axonium: {missing}"


def test_everything_promised_actually_exists() -> None:
    # An __all__ naming something absent breaks `from axonium import *` and every documentation
    # tool, and does so only for the consumer rather than for us.
    for name in axonium.__all__:
        assert hasattr(axonium, name), f"__all__ promises {name}, which does not exist"


def test_the_version_is_a_single_source_of_truth() -> None:
    # The legacy SDK kept the packaged version and the attribute in separate files and they drifted
    # by three releases, so this is pinned rather than assumed.
    #
    # What is deliberately NOT asserted here is importlib.metadata: in an editable install it
    # reports whatever the last sync wrote, which lags a version bump and would fail for a reason
    # that has nothing to do with this package. The build backend reads _version.py directly, and
    # the release workflow refuses to publish a wheel whose version disagrees with its tag -- which
    # is the check that actually protects a consumer.
    from axonium import _version

    assert axonium.__version__ == _version.__version__


def test_the_official_default_is_exported() -> None:
    # Callers pointing at a self-hosted deployment need to be able to compare against, or fall back
    # to, what the SDK would otherwise use.
    assert axonium.DEFAULT_GATEWAY_BASE_URL.startswith("http")


def test_there_is_exactly_one_address_to_configure() -> None:
    # The platform used to run a separate auth-service that every consumer also had to configure.
    # The gateway issues tokens itself now, and this pins that the second address is gone from the
    # public surface rather than merely defaulted -- a caller should never learn it existed.
    urls = [name for name in axonium.__all__ if name.endswith("_BASE_URL")]
    assert urls == ["DEFAULT_GATEWAY_BASE_URL"], f"more than one address is exported: {urls}"
    assert "auth_base_url" not in axonium.AxoniumConfig.model_fields
