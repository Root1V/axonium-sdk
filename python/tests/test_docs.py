"""The documentation's no-expiry contract.

Six tests, each covering a different way documentation can start lying. The third is the one that
adds the most and the one nobody writes: it is not enough that everything documented exists, every
public symbol has to be documented, or something ends up usable only by reading the source.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

import axonium

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
HTML = DOCS / "html"
SCRIPTS = REPO / "scripts"
REFERENCE = DOCS / "08-reference.md"

# Names cited in prose that are deliberately not ours: standard library, test tools, and the
# gateway's own vocabulary. Anything not here has to exist in one of the three SDKs.
FOREIGN = {
    "Axonium",  # the product name, in prose
    "Copy",
    "Copied",
    "Unavailable",
    "Response",
    "Retry",
    "After",
    "NullHandler",
    "Drop",
    "Duration",
    "Some",
    "None",
    "Err",
    "Ok",
    "String",
    "JSON",
    "SSE",
    "HTTP",
    "TLS",
    "URL",
    "URLs",
    "PII",
    "MIT",
    "RPM",
    "OAuth2",
    "CDN",
    "GitHub",
    "Python",
    "Go",
    "Rust",
    "Shell",
    "LangChain",
    "LangGraph",
    "OpenTelemetry",
    "Pages",
    "Markdown",
    "CLOSED",
    "OPEN",
    "HALF_OPEN",
    "INFO",
    "DEBUG",
    "POST",
    "GET",
    "Authorization",
}


def narrative_pages() -> list[Path]:
    """Every page except the generated reference.

    The reference is generated *from* the symbols, so it cannot name one that does not exist --
    checking it would be checking the generator. It also quotes standard-library types the name
    test would take for ours.
    """
    return [p for p in sorted(DOCS.glob("*.md")) if p != REFERENCE]


def public_names() -> set[str]:
    """Every identifier the three SDKs export, gathered from the sources rather than a list."""
    names = set(axonium.__all__)
    for source in (REPO / "go" / "axonium").glob("*.go"):
        if source.name.endswith("_test.go"):
            continue
        text = source.read_text(encoding="utf-8")
        names |= set(re.findall(r"^(?:type|func) ([A-Z]\w+)", text, re.M))
        names |= set(re.findall(r"^func \([^)]+\) ([A-Z]\w+)", text, re.M))
        names |= set(re.findall(r"^\t([A-Z]\w+)\s+[\w*\[\]]", text, re.M))  # struct fields
    for source in (REPO / "rust" / "src").rglob("*.rs"):
        text = source.read_text(encoding="utf-8")
        names |= set(re.findall(r"pub (?:struct|enum|trait|fn|const) (\w+)", text))
        names |= set(re.findall(r"^\s*pub (\w+):", text, re.M))
    return names


class TestTheTwoOutputsAgree:
    def test_the_html_corresponds_to_the_markdown(self) -> None:
        """One source, two outputs. Editing both by hand guarantees they diverge."""
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "render_docs.py"), "--check"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_the_reference_corresponds_to_the_code(self) -> None:
        """Regenerate, compare, restore -- the check must not leave the tree modified."""
        before = REFERENCE.read_text(encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "render_reference.py")], capture_output=True, text=True
        )
        after = REFERENCE.read_text(encoding="utf-8")
        if after != before:
            REFERENCE.write_text(before, encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        assert after == before, (
            "docs/08-reference.md is out of date. Run: python scripts/render_reference.py"
        )


class TestNothingIsUndocumented:
    def test_every_exported_symbol_appears_in_the_reference(self) -> None:
        """The test that adds the most and that nobody writes.

        Documenting only what someone remembered to document is how a symbol ends up discoverable
        solely by reading the source.
        """
        text = REFERENCE.read_text(encoding="utf-8")
        missing = [name for name in axonium.__all__ if f"`{name}`" not in text]
        assert not missing, f"exported but absent from the reference: {missing}"

    def test_every_page_is_reachable_from_the_index(self) -> None:
        """A page nobody links to is a page nobody reads."""
        index = (DOCS / "index.md").read_text(encoding="utf-8")
        unlinked = [
            page.name
            for page in sorted(DOCS.glob("*.md"))
            if page.stem != "index" and f"({page.name})" not in index
        ]
        assert not unlinked, f"not linked from index.md: {unlinked}"


class TestTheProseHasNotAgedPastTheCode:
    def test_no_cited_name_has_stopped_existing(self) -> None:
        known = public_names() | FOREIGN
        unknown: dict[str, list[str]] = {}
        for page in narrative_pages():
            for token in re.findall(r"`([A-Z][A-Za-z0-9]+)`", page.read_text(encoding="utf-8")):
                if token not in known:
                    unknown.setdefault(page.name, []).append(token)
        assert not unknown, f"named in the docs but exported by nothing: {unknown}"

    def test_no_cited_client_path_has_stopped_existing(self) -> None:
        """`client.chat.completions.create` has to still resolve, one attribute at a time.

        Restricted to Python blocks. The three SDKs deliberately read idiomatically in each
        language -- ``client.chat_stream`` is Rust -- so resolving every block against the Python
        client would report the other two as broken.
        """
        cited: set[str] = set()
        for page in narrative_pages():
            for block in re.findall(r"```python\n(.*?)```", page.read_text(encoding="utf-8"), re.S):
                cited |= set(re.findall(r"\bclient\.((?:[a-z_]+\.)*[a-z_]+)\s*\(", block))
                cited |= set(re.findall(r"\bclient\.([a-z_]+(?:\.[a-z_]+)*)\b(?!\s*[(\w])", block))

        client = axonium.Axonium(
            gateway_base_url="https://gw.test.invalid", client_id="i", client_secret="s"
        )
        try:
            missing = object()  # not None: `last_rate_limit` is legitimately None until a call
            broken = []
            for path in sorted(cited):
                target: object = client
                for part in path.split("."):
                    target = getattr(target, part, missing)
                    if target is missing:
                        broken.append(f"client.{path}")
                        break
        finally:
            client.close()
        assert not broken, f"documented but no longer resolvable: {broken}"


class TestWhatIsCalledPendingIsStillPending:
    """Documenting something as future work that has since shipped is its own kind of lie."""

    def test_failures_still_carry_no_response_metadata(self) -> None:
        # Claimed in 03-failure.md and 07-limits.md. When this starts failing, the limitation was
        # fixed and both pages need the paragraph removed rather than reworded.
        error = axonium.APIError("x", status=500)
        assert not hasattr(error, "meta"), (
            "APIError now carries metadata; 03-failure.md and 07-limits.md say it does not"
        )

    def test_the_second_address_is_still_gone(self) -> None:
        # Claimed in 04-configuration.md. Narrowed to the URL setting itself: matching on "AUTH"
        # would also catch the error classes.
        assert not [n for n in axonium.__all__ if n.endswith("_BASE_URL") and "AUTH" in n]
        assert not hasattr(axonium.AxoniumConfig, "auth_base_url")

    def test_there_is_still_no_flag_to_skip_tls_verification(self) -> None:
        # Claimed in 04-configuration.md, and the reason it is a test rather than a promise: the
        # failure mode of that flag is that it gets set during an incident and never unset.
        fields = set(axonium.AxoniumConfig.model_fields)
        assert not {"verify", "verify_ssl", "insecure", "skip_verify"} & fields


class TestTheRenderedOutputIsSelfContained:
    """Both checks the spec asks for by name, because both failed silently the first time."""

    @pytest.mark.parametrize("page", sorted(HTML.glob("*.html")), ids=lambda p: p.name)
    def test_no_page_loads_anything_from_the_network(self, page: Path) -> None:
        text = page.read_text(encoding="utf-8")
        loader = r'(?:<script[^>]+src|<link[^>]+href|@import|url\()\s*=?\s*["\']?'
        external = re.findall(loader + r'(https?://[^"\')\s>]+)', text)
        assert not external, f"{page.name} loads external resources: {external}"

    def test_every_highlight_class_emitted_has_a_rule(self) -> None:
        # The first version of this stylesheet invented class names the highlighter never emits, so
        # every code block rendered uncoloured and nothing failed.
        style = (SCRIPTS / "render_docs.py").read_text(encoding="utf-8")
        style = style.split("STYLE = ")[1].split('"""')[1]
        styled = set(re.findall(r"\.([a-z][a-z0-9]{0,3})\b(?=[ ,{])", style))
        # Plain identifiers and whitespace inherit the base colour on purpose: colouring every
        # identifier turns highlighting into noise.
        inherits = {"n", "nx", "w", "nl", "py", "nv", "vi"}

        emitted: set[str] = set()
        for page in HTML.glob("*.html"):
            text = page.read_text(encoding="utf-8")
            for group in re.findall(r'<span class="([a-z0-9 ]+)"', text):
                emitted.update(group.split())

        assert emitted, "no highlighted spans at all -- the highlighter stopped running"
        assert not emitted - styled - inherits, (
            f"emitted with no CSS rule: {sorted(emitted - styled - inherits)}"
        )
