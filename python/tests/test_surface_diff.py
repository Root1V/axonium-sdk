"""The comparison that tells a consumer whether a field they read changed type.

`compare` is tested directly rather than end-to-end: the end-to-end path installs a wheel from
PyPI, and a test that needs the network to say whether comparison logic is correct reports red for
reasons that have nothing to do with the code under review.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def surface_diff() -> Any:
    spec = importlib.util.spec_from_file_location("surface_diff", SCRIPTS / "surface_diff.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def surface(names: list[str], models: dict[str, dict[str, str]]) -> dict[str, Any]:
    return {"version": "x", "all": names, "models": models}


class TestWhatCountsAsBreaking:
    def test_a_field_changing_type_is_breaking(self) -> None:
        """The case this exists for, and the only one a tolerant accessor hides.

        `1.0.0rc4` turned `tool_calls` from `list[dict]` into `list[ToolCall]`. A bridge reading
        attribute-or-key kept working and said nothing, so the team consuming it reported that the
        release "did not break" them -- which was true, and left a third party to find out in
        production.
        """
        old = surface(["Message"], {"Message": {"tool_calls": "list[dict[str, Any]] | None"}})
        new = surface(["Message"], {"Message": {"tool_calls": "list[ToolCall] | None"}})

        additive, breaking = surface_diff().compare(old, new)

        assert not additive
        assert breaking == [
            "~ Message.tool_calls: list[dict[str, Any]] | None  ->  list[ToolCall] | None"
        ]

    def test_a_removed_symbol_is_breaking(self) -> None:
        old = surface(["Axonium", "DEFAULT_AUTH_BASE_URL"], {})
        new = surface(["Axonium"], {})

        _, breaking = surface_diff().compare(old, new)

        assert breaking == ["- DEFAULT_AUTH_BASE_URL  (exported symbol removed)"]

    def test_a_removed_field_is_breaking(self) -> None:
        old = surface(["Config"], {"Config": {"auth_base_url": "str", "gateway_base_url": "str"}})
        new = surface(["Config"], {"Config": {"gateway_base_url": "str"}})

        _, breaking = surface_diff().compare(old, new)

        assert breaking == ["- Config.auth_base_url  (field removed)"]


class TestWhatCountsAsAdditive:
    def test_new_symbols_and_fields_are_additive(self) -> None:
        old = surface(["Axonium"], {"ResponseMeta": {"request_id": "str | None"}})
        new = surface(
            ["Axonium", "ToolCall"],
            {"ResponseMeta": {"request_id": "str | None", "waited_s": "<class 'float'>"}},
        )

        additive, breaking = surface_diff().compare(old, new)

        assert not breaking
        assert additive == ["+ ToolCall", "+ ResponseMeta.waited_s: <class 'float'>"]

    def test_an_unchanged_surface_reports_nothing(self) -> None:
        same = surface(["Axonium"], {"Config": {"gateway_base_url": "str"}})

        assert surface_diff().compare(same, same) == ([], [])

    def test_a_whole_new_model_is_additive(self) -> None:
        old = surface(["Axonium"], {})
        new = surface(["Axonium", "RerankResponse"], {"RerankResponse": {"results": "list[Any]"}})

        additive, breaking = surface_diff().compare(old, new)

        assert not breaking
        assert additive == ["+ RerankResponse", "+ RerankResponse.results: list[Any]"]


class TestTheExitCodeIsTheContract:
    """CI reads the exit code, so it is what has to be right."""

    @pytest.mark.parametrize(
        ("breaking", "acknowledged", "expected"),
        [(False, False, 0), (False, True, 0), (True, False, 1), (True, True, 0)],
    )
    def test_breaking_fails_until_acknowledged(
        self, breaking: bool, acknowledged: bool, expected: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = surface_diff()
        old = surface(["A", "B"], {}) if breaking else surface(["A"], {})
        monkeypatch.setattr(module, "surface_of_working_tree", lambda: surface(["A"], {}))
        monkeypatch.setattr(module, "surface_of_published", lambda _version: old)
        monkeypatch.setattr(module, "newest_on_pypi", lambda: "1.0.0")
        monkeypatch.setattr(
            "sys.argv",
            ["surface_diff.py"] + (["--acknowledge-breaking"] if acknowledged else []),
        )

        assert module.main() == expected
