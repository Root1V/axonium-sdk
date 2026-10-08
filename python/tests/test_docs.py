"""The documentation's no-expiry contract.

Six tests, each covering a different way documentation can start lying. The third is the one that
adds the most and the one nobody writes: it is not enough that everything documented exists, every
public symbol has to be documented, or something ends up usable only by reading the source.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import axonium

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
HTML = DOCS / "html"
SCRIPTS = REPO / "scripts"
REFERENCE = DOCS / "08-reference.md"


def _load_renderer() -> Any:
    """Import the renderer by path: scripts/ is a directory of tools, not an importable package."""
    spec = importlib.util.spec_from_file_location("render_docs", SCRIPTS / "render_docs.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Names cited in prose that are deliberately not ours: standard library, platform APIs, test tools,
# and the gateway's own vocabulary. Anything not here has to exist in one of the four in-repo SDKs.
FOREIGN = {
    "AbortSignal",  # the Web platform's, which the TypeScript SDK accepts rather than defines
    "URLProtocol",  # Foundation's, how a Swift caller fakes the transport
    "AutoProcessor",  # the inference platform's vocabulary, named in a payload_schema discussion
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
    """Every identifier the four in-repo SDKs export, gathered from the sources rather than a list.

    Swift lives in its own repository and is not checkable from here. Its names are covered by the
    mirror of this test in that repository, which reads the same pages through the corpus submodule
    -- rather than by a hand-kept list here, which would be a fifth copy of a thing that drifts.
    """
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
    for source in (REPO / "typescript" / "src").rglob("*.ts"):
        text = source.read_text(encoding="utf-8")
        names |= set(
            re.findall(r"export (?:class|interface|type|function|const|abstract class) (\w+)", text)
        )
        names |= set(re.findall(r"^\s*(?:readonly )?(\w+)[?]?:", text, re.M))  # interface fields
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


def code_groups(page: Path) -> list[list[str]]:
    """Runs of fenced blocks separated by nothing but blank lines -- what the renderer tabs.

    A block preceded by ``<!-- one-language: X -->`` is left out: it is a section about one SDK and
    says so. The marker exists so that the exception is *written down* -- the alternative is hiding
    such a block from this guard by formatting it as a blockquote, which works by accident and reads
    as an oversight.
    """
    lines = page.read_text(encoding="utf-8").splitlines()
    exempt = {
        n + 1 for n, line in enumerate(lines) if line.strip().startswith("<!-- one-language:")
    }
    fences, index = [], 0
    while index < len(lines):
        opening = re.match(r"^```(\w*)", lines[index])
        if opening and index in exempt:
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                index += 1
        elif opening:
            start, language = index, opening.group(1)
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                index += 1
            fences.append((start, language, index))
        index += 1

    groups: list[list[tuple[int, str, int]]] = []
    current: list[tuple[int, str, int]] = []
    for fence in fences:
        if current and all(not line.strip() for line in lines[current[-1][2] + 1 : fence[0]]):
            current.append(fence)
        else:
            if current:
                groups.append(current)
            current = [fence]
    if current:
        groups.append(current)
    return [[language for _, language, _ in group] for group in groups]


#: The languages a tab group must speak, READ FROM THE RENDERER rather than written again here.
#: The previous version kept its own ``{"python", "go", "rust"}`` in two places, so when a fourth
#: and then a fifth SDK shipped, this guard went on certifying three-language groups as complete --
#: a test that agreed with the site about a number both of them had got wrong.
SDK_LANGUAGES = set(_load_renderer().TAB_LANGUAGES)


class TestEveryExampleSpeaksEveryLanguage:
    """One site for every SDK, so an example in one language is an example missing the rest.

    All of these come from real defects. A group rendered as four tabs reading
    "Python, Python, Go, Rust" with two of them empty; most groups showed Python alone; and the
    whole site spoke three languages while the matrix on its own front page listed five.
    """

    @pytest.mark.parametrize("page", narrative_pages(), ids=lambda p: p.name)
    def test_a_group_showing_one_sdk_language_shows_them_all(self, page: Path) -> None:
        incomplete = [
            group
            for group in code_groups(page)
            if set(group) & SDK_LANGUAGES and not set(group) >= SDK_LANGUAGES
        ]
        assert not incomplete, (
            f"{page.name}: {len(incomplete)} group(s) missing a language. "
            f"Expected all of {sorted(SDK_LANGUAGES)}; got: {incomplete}"
        )

    @pytest.mark.parametrize("page", narrative_pages(), ids=lambda p: p.name)
    def test_no_group_repeats_a_language(self, page: Path) -> None:
        repeated = [
            group
            for group in code_groups(page)
            if set(group) & SDK_LANGUAGES and len(group) != len(set(group))
        ]
        assert not repeated, f"{page.name}: a tabset would show duplicate tabs: {repeated}"


class TestTabsWrapCodeAndNothingElse:
    """The wrapper must contain code blocks only.

    Written as `.*?</div>` the grouping regex looked right and was not: `.*?` backtracks, so one
    repetition could swallow the prose between two blocks and close at a later `</div>`. A
    paragraph, a warning and an `<h2>` ended up inside a tabset, hidden along with the tabs.
    """

    @pytest.mark.parametrize("page", sorted(HTML.glob("*.html")), ids=lambda p: p.name)
    def test_no_prose_is_swallowed_into_a_tabset(self, page: Path) -> None:
        html_text = page.read_text(encoding="utf-8")
        for start in (m.start() for m in re.finditer(r'<div class="tabs">', html_text)):
            depth = 0
            for token in re.finditer(r"<div\b[^>]*>|</div>", html_text[start:]):
                depth += 1 if token.group(0) != "</div>" else -1
                if depth == 0:
                    group = html_text[start : start + token.end()]
                    break
            else:
                pytest.fail(f"{page.name}: unbalanced tabs wrapper")
            prose = re.findall(r"<(p|h1|h2|h3|blockquote|ul|ol|table)\b", group)
            assert not prose, f"{page.name}: prose inside a tabset: {sorted(set(prose))}"


class TestTheGroupingFunctionItself:
    """Tested directly, because the corpus can stop being able to trigger the bug.

    The page test above checks the rendered output, which is the invariant that matters. But it can
    only catch a grouping bug that today's pages happen to provoke: once every group is a run of
    adjacent blocks, a regex that backtracks across prose never needs to, and the fault sits latent
    until someone writes a lone block followed by prose and another block. Verified by restoring
    the greedy version -- the page tests stayed green and this one does not.
    """

    @staticmethod
    def block(language: str) -> str:
        return (
            f'<div class="code" data-language="{language}" data-label="{language}">'
            f"<pre><code>x</code></pre></div>"
        )

    def test_prose_between_blocks_ends_the_group(self) -> None:
        render_docs = _load_renderer()
        html_in = (
            self.block("python")
            + "\n<p>prose</p>\n<h2>A heading</h2>\n"
            + self.block("python")
            + "\n"
            + self.block("go")
        )

        grouped = render_docs.group_code_tabs(html_in)

        opened = grouped.index('<div class="tabs">')
        assert "<p>prose</p>" not in grouped[opened:], (
            "the tabset swallowed the prose that separates two groups"
        )
        assert grouped.count('<div class="tabs">') == 1, (
            "only the adjacent pair is a tabset; the lone block before the prose is not"
        )

    def test_adjacent_blocks_are_grouped(self) -> None:
        render_docs = _load_renderer()
        html_in = self.block("python") + "\n" + self.block("go") + "\n" + self.block("rust")

        grouped = render_docs.group_code_tabs(html_in)

        assert grouped.startswith('<div class="tabs">')
        assert grouped.count('<div class="tabs">') == 1


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


class TestEveryPackageLinksToTheDocumentation:
    """What a registry shows is the README and the module doc, and none of them named the site.

    Measured 2026-10-06: `pypi`, `crates.io`, `npm`, `pkg.go.dev` and Swift's GitHub landing page
    had **zero** links to https://root1v.github.io/axonium-sdk/ between them. PyPI and crates.io
    carried a `Documentation` URL in their metadata, which is a small link in a sidebar; the page a
    reader actually lands on said nothing. npm's `homepage` pointed at the repository.

    Swift is in its own repository and has the mirror of this test there.
    """

    SITE = "https://root1v.github.io/axonium-sdk/"

    #: Every file a package registry renders, or that a language's doc tooling publishes.
    SURFACES = (
        "python/README.md",
        "go/README.md",
        "rust/README.md",
        "typescript/README.md",
        "python/src/axonium/__init__.py",
        "go/axonium/client.go",
        "rust/src/lib.rs",
    )

    @pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s)
    def test_the_published_surface_names_the_documentation(self, surface: str) -> None:
        text = (REPO / surface).read_text(encoding="utf-8")
        assert self.SITE in text, (
            f"{surface} is rendered by a package registry or doc tool and does not link to "
            f"{self.SITE}. A reader who arrives there has no route to the guide."
        )

    def test_npms_homepage_is_the_documentation_rather_than_the_repository(self) -> None:
        # The one field npm shows most prominently, and the repository is already in `repository`.
        manifest = json.loads((REPO / "typescript" / "package.json").read_text(encoding="utf-8"))
        assert manifest["homepage"] == self.SITE


SPANISH = DOCS / "es"


def spanish_pages() -> list[Path]:
    return sorted(SPANISH.glob("*.md"))


class TestTheSpanishSiteHasNotDrifted:
    """A translation that quietly stops matching is worse than no translation.

    A reader cannot tell. They read a page that was true once, act on it, and the mistake surfaces
    somewhere unrelated — so the whole mechanism here exists to make falling behind **loud** rather
    than to prevent it: every Spanish page records the digest of the English revision it was made
    from, and these tests fail the moment that revision moves.

    The translation is then stale *on purpose* and says so, which is a position you can be in.
    Silently wrong is not.
    """

    def test_no_translation_has_fallen_behind_its_source(self) -> None:
        problems = _load_renderer().stale_translations()
        assert not problems, "\n".join(["translations need attention:", *problems])

    @pytest.mark.parametrize("page", spanish_pages(), ids=lambda p: p.name)
    def test_the_code_blocks_are_byte_identical_to_the_english(self, page: Path) -> None:
        """Prose is translated; code never is.

        This is the guard that matters most, because it is the one failure a reader cannot survive:
        a translated identifier does not compile, and a reordered block is a snippet that belongs to
        a different paragraph. It is also not hypothetical — writing these pages produced exactly
        that, a Spanish page with the right *number* of blocks in the wrong *places*, which read as
        correct until the two sequences were compared.
        """
        if page.name in _load_renderer().GENERATED:
            pytest.skip("stands in for a generated page rather than translating it")

        fences = re.compile(r"```.*?```", re.S)
        english = fences.findall((DOCS / page.name).read_text(encoding="utf-8"))
        spanish = fences.findall(page.read_text(encoding="utf-8"))

        assert spanish == english, (
            f"docs/es/{page.name}: the code blocks differ from docs/{page.name}. "
            f"English has {len(english)}, Spanish has {len(spanish)}. Code is never translated: "
            f"splice the English blocks in rather than retyping them."
        )

    @pytest.mark.parametrize("page", spanish_pages(), ids=lambda p: p.name)
    def test_a_group_showing_one_sdk_language_shows_them_all(self, page: Path) -> None:
        # The same guard the English pages get. It cannot fail while the test above passes, and it
        # is here anyway: the day somebody adds a Spanish-only example, this is what catches it.
        incomplete = [
            group
            for group in code_groups(page)
            if set(group) & SDK_LANGUAGES and not set(group) >= SDK_LANGUAGES
        ]
        assert not incomplete, f"es/{page.name}: groups missing a language: {incomplete}"


class TestEveryLinkResolves:
    """Both of these are bugs a reader hit, and neither was visible to any test here.

    A link to `es/index.md` shipped because the rewriter's pattern excluded `/`, so it rewrote only
    links in the same directory and had nothing to rewrite until a second directory existed. And the
    404 page was a copy of the English index, which turned that one dead link into a loop: served at
    `/es/index.md`, the copy's relative links resolve against `/es/`, so each click added another
    `es/` — `/es/es/es/index.html`.
    """

    @pytest.mark.parametrize("page", sorted(HTML.rglob("*.html")), ids=lambda p: str(p.name))
    def test_no_page_links_to_a_markdown_file_on_this_site(self, page: Path) -> None:
        # An external `.md` (a file on GitHub) is fine and common; one of ours is a link to a raw
        # file the browser will download or 404 on.
        ours = [
            href
            for href in re.findall(r'href="([^"]+)"', page.read_text(encoding="utf-8"))
            if href.endswith(".md") and not re.match(r"\w+:", href)
        ]
        assert not ours, f"{page.name} links to Markdown rather than to a page: {ours}"

    @pytest.mark.parametrize("page", sorted(HTML.rglob("*.html")), ids=lambda p: str(p.name))
    def test_every_internal_link_points_at_a_file_that_exists(self, page: Path) -> None:
        missing = []
        for href in re.findall(r'href="([^"]+)"', page.read_text(encoding="utf-8")):
            if re.match(r"\w+:", href) or href.startswith("#"):
                continue
            target = href.split("#")[0]
            if not target:
                continue
            # Root-relative means from the site root, which is HTML/ here.
            resolved = (
                (HTML / target[len("/axonium-sdk/") :])
                if target.startswith("/")
                else (page.parent / target)
            )
            if not resolved.exists():
                missing.append(href)
        assert not missing, f"{page.name} links to files that do not exist: {missing}"

    def test_the_404_page_uses_only_absolute_links(self) -> None:
        """It is reached from a path nobody chose, so relative links on it resolve against nonsense.

        This is the property the previous 404 lacked, and lacking it is what turned one dead link
        into `/es/es/es/`.
        """
        page = HTML / "404.html"
        relative = [
            href
            for href in re.findall(r'href="([^"]+)"', page.read_text(encoding="utf-8"))
            if not re.match(r"\w+:", href) and not href.startswith("/")
        ]
        assert not relative, (
            f"404.html has relative links, which break from any other depth: {relative}"
        )

    def test_the_404_page_is_not_a_copy_of_the_index(self) -> None:
        # The shape of the original bug: a friendlier page that silently claims the request worked.
        assert (HTML / "404.html").read_text(encoding="utf-8") != (HTML / "index.html").read_text(
            encoding="utf-8"
        )


class TestNoPublishedPageDeniesACapabilityTheSdkHas:
    """A front page that denies a feature is worse than one that omits it.

    Measured 2026-10-07: a sentence denying that this API has any idempotency mechanism at all was
    on the published `pypi` and
    `pkg.go.dev` front pages, in `python/src/axonium/transport/retry.py` and `go/axonium/retry.go`
    — **the two files whose job is to explain why a timeout is not retried** — and in a Go test's
    header comment. All five SDKs sent `Idempotency-Key`, the error catalog carried four
    idempotency error types, and both of those retry modules sat a few hundred lines from code
    that read the key and retried on it. Rust's copy of the paragraph was correct, and nothing
    could tell the three apart, because the claim was prose.

    It was found the way a false sentence in a README gets found: a consumer read it and asked
    whether to pass a key at all (Veritium, `VRT-AXO-001` question 4). That is six places and
    eighteen days of a published page telling people not to use a feature that works.

    So this asserts the **positive** claim rather than banning the old sentence. A forbidden-phrase
    check would have been defeated by the correction itself, which quotes what it is correcting;
    and more importantly, a page can deny a capability in words no blocklist anticipated. What
    cannot be faked is saying the true thing.
    """

    #: Surface → the capability its retry section must not leave a reader guessing about.
    SURFACES = (
        "python/README.md",
        "go/README.md",
        "rust/README.md",
        "python/src/axonium/transport/retry.py",
        "go/axonium/retry.go",
        "rust/src/retry.rs",
    )

    @pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s)
    def test_the_retry_prose_states_what_a_key_does(self, surface: str) -> None:
        text = (REPO / surface).read_text(encoding="utf-8").lower()

        # TWO checks, because the first one I wrote was the weaker-than-its-name kind this whole
        # class is about. It asserted that "idempotency" and "timeout" appear near each other --
        # which the FALSE paragraph also satisfied, since it denied the mechanism and ruled out
        # retrying a timeout in consecutive sentences. Restoring the old prose left it green.
        # Measured, which is the only reason I know.
        for denial in self.DENIALS:
            assert denial not in text, (
                f"{surface} says {denial!r}, and the SDK has had an idempotency key since before "
                f"that sentence was written. This is the exact regression this class exists for"
            )

        # Scoped to the neighbourhood of an `idempotency` mention, not the whole file. Searching
        # the file was the second version of this test and it was ALSO weaker than its name: Go's
        # README says "is retried" about rate limits several paragraphs away, so deleting the
        # sentence about keys entirely left it green. Measured by deleting it.
        windows = [
            text[max(0, i - self.WINDOW) : i + self.WINDOW]
            for i in range(len(text))
            if text.startswith("idempotency", i)
        ]
        assert windows, f"{surface} explains the retry policy without mentioning idempotency at all"
        assert any(
            "timeout" in window and any(claim in window for claim in self.CLAIMS)
            for window in windows
        ), (
            f"{surface} mentions idempotency but never near a statement that a key makes a "
            f"timed-out request retryable -- the one branch a key changes and the whole reason to "
            f"pass one. One of {self.CLAIMS} has to sit within {self.WINDOW} characters of it"
        )

    #: Phrases that assert the absence of what the SDK implements. Lowercased before matching.
    #:
    #: A blocklist is the weaker half of this test on purpose: it catches the regression that
    #: actually happened and nothing more. `CLAIMS` is the half that catches a page which simply
    #: stops mentioning the feature. Neither alone is enough -- the first version of this test was
    #: the first half done badly, and it passed against the text it was written to forbid.
    DENIALS = (
        "no idempotency mechanism",
        "no idempotency-key mechanism",
        "idempotency is not supported",
        "does not support idempotency",
    )

    #: How far from an `idempotency` mention the positive claim may sit. A paragraph's reach: wide
    #: enough that the claim and the word need not share a sentence, narrow enough that prose about
    #: something else entirely cannot satisfy it.
    WINDOW = 500

    #: A page is correct when it says the positive thing. Any one of these does it.
    CLAIMS = (
        "becomes retryable",
        "is retried",
        "retried under",
        "changes that for timeouts",
        "makes the repeat free",
    )

    def test_every_sdk_that_sends_the_header_has_a_test_that_it_retries_on_it(self) -> None:
        """The prose is now checked; this checks the behaviour it describes exists to be described.

        Without this the test above could be satisfied by six pages correctly describing a feature
        that had been removed.
        """
        covered = {
            "python": REPO / "python" / "tests" / "test_idempotency.py",
            "go": REPO / "go" / "axonium" / "idempotency_test.go",
            # Written 2026-10-07. Rust had the behaviour and no test of it, which this check found
            # on its first run -- the branch is three tokens long and the SDK whose README
            # described the feature correctly was the one not exercising it.
            "rust": REPO / "rust" / "tests" / "idempotency.rs",
        }

        missing = [name for name, path in covered.items() if not path.exists()]
        assert not missing, f"missing an idempotency test suite for: {missing}"

        for name, path in covered.items():
            text = path.read_text(encoding="utf-8").lower()
            assert "timeout" in text, (
                f"{name}'s idempotency suite never mentions a timeout, so the one branch a key "
                f"changes is the one branch it does not cover"
            )


class TestTheClientLifetimeAdviceIsQualified:
    """ "A client per process" was advice that breaks the shape it was written for.

    It is right for a CLI and for a sync client. An async client holds a connection pool bound to
    the event loop it was built in, so a process with an API and a worker pool — more than one
    loop — cannot share one. Measured on Python 3.13, reusing one `AsyncAxonium` across two
    `asyncio.run()` calls: `RuntimeError: Event loop is closed`, raised from inside asyncio, so the
    message does not name the cause.

    Found by a consumer asking whether a long-lived service was a supported shape at all
    (Veritium, `VRT-AXO-001` question 1). The page said yes and gave a rule that would have failed
    on their second worker.
    """

    @pytest.mark.parametrize("page", ["01-concepts.md", "es/01-concepts.md"], ids=lambda p: p)
    def test_the_page_says_an_async_client_belongs_to_its_loop(self, page: str) -> None:
        text = (DOCS / page).read_text(encoding="utf-8")

        assert "event loop" in text or "bucle de eventos" in text, (
            f"docs/{page} advises on client lifetime without mentioning the event loop, so a "
            f"reader building a service with a worker pool gets a rule that breaks on their "
            f"second loop and an error from inside asyncio that does not say why"
        )
        # The unqualified form is what was wrong. Caught as a phrase because the advice is a
        # phrase, and because "per process" with the qualification nearby is exactly right.
        for unqualified in ("a client per process, not per request", "Uno por proceso."):
            assert unqualified not in text, (
                f"docs/{page} gives the unqualified rule again: {unqualified!r}"
            )


class TestNoSurfaceRecommendsTheRemedyThatCostsMoney:
    """`409 idempotency-key-reuse` does not always mean the caller reused the key.

    The fingerprint is taken over the **gateway's** parsed request model including its defaults,
    not over the bytes the client sent, so an additive change to that model invalidates every key
    stored before it. Veritium measured it on 2026-10-08: `PRM-235` added two optional fields
    defaulting to null, and from that deploy a client resending a byte-identical request got this
    error. The contract's own remedy — *"resend the original request unchanged"* — is exactly what
    fails, and all five SDKs had copied the contract faithfully.

    That made our documented remedy the expensive one: *"use a fresh key per logical request"*. In
    this case the body did not change, so a fresh key buys a **second billable generation** for
    work the first request may already have finished — the precise harm a key exists to prevent.

    The guard is the positive form, which is what yesterday's `AXO-156` taught: a blocklist on
    *"fresh key"* would be wrong, because a fresh key **is** the right answer when the body really
    changed. What must be present is the qualification.
    """

    #: Every surface that documents this error for a reader. Swift's is in its own repository and
    #: cannot be checked from here; its own suite has the mirror of this test.
    SURFACES = (
        "spec/errors.json",
        "python/src/axonium/errors.py",
        "go/axonium/errors.go",
        "rust/src/error.rs",
        "typescript/src/errors.ts",
        "docs/03-failure.md",
        "docs/es/03-failure.md",
    )

    #: The cost, per language, and **keyed by surface rather than pooled**.
    #:
    #: Pooling them is how this test gave a false green within the hour it was written: with the
    #: Spanish phrases accepted everywhere, `docs/03-failure.md` passed an English-page assertion
    #: *by having been overwritten with the Spanish page*. `AXO-163` has the full story. An
    #: English surface must say it in English.
    #:
    #: Specific phrases, not the stem. A bare `factura` was the first attempt and it matched
    #: unrelated prose about a DIFFERENT error ("ya está facturada", about a timeout) sitting
    #: inside the same window, so the Spanish page survived a mutation that stripped the claim.
    #: Both narrowings are measured; neither was foreseen.
    COST_EN = ("second billable", "double-bill")
    COST_ES = ("segunda generación facturable", "facturaría doble")

    @classmethod
    def _cost_for(cls, surface: str) -> tuple[str, ...]:
        return cls.COST_ES if "/es/" in surface else cls.COST_EN

    @pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s)
    def test_the_qualification_sits_with_the_error(self, surface: str) -> None:
        text = (REPO / surface).read_text(encoding="utf-8")

        # Both spellings, because the documentation does not always sit next to the wire name.
        # In `rust/src/error.rs` the hyphenated string appears only in the suffix mapping, ninety
        # lines from the enum variant that carries the doc comment -- which is how the first run
        # of this test reported Rust as undocumented when it was not.
        markers = ("idempotency-key-reuse", "IdempotencyKeyReuse", "idempotencyKeyReuse")
        positions = [i for i in range(len(text)) if any(text.startswith(m, i) for m in markers)]
        assert positions, f"{surface} no longer documents idempotency-key-reuse"

        # Scoped to the error's own neighbourhood. Searching the whole file is the mistake that
        # made the first version of AXO-156's guard pass against the text it was written to
        # forbid: these files discuss billing in several places.
        windows = [text[max(0, i - 2200) : i + 2200] for i in positions]
        expected = self._cost_for(surface)
        assert any(any(cost in w for cost in expected) for w in windows), (
            f"{surface} documents idempotency-key-reuse without saying what a fresh key costs "
            f"when the body did not change. That is the remedy the contract gives, and it is the "
            f"one that buys a duplicate generation"
        )


class TestAnEnglishPageIsNotTheSpanishOne:
    """Nothing checked that an English page was in English, and one was not.

    On 2026-10-08 `docs/03-failure.md` was overwritten with the contents of `docs/es/03-failure.md`
    and shipped: committed, merged, pushed and served from the published site. The cause was a
    backup loop in a mutation test that keyed its copies by `basename`, and the two files share one
    — so restoring put the Spanish text at the English path.

    **Three tests should have caught it and each had a reason not to:**

    - The code-block identity test passed because the blocks *are* identical. That is the design.
    - The stale-translation check **did fire**. It reported that the English source had moved, which
      was exactly true and exactly the bug. I recomputed the digest to silence it instead of asking
      why it had changed — the detector worked and the person reading it did not.
    - The guard written that same hour passed because it looks for the cost of a fresh key in any
      of four phrases, two of them Spanish so the Spanish page could satisfy it. The English page
      satisfied an English-page assertion **by being Spanish**.

    So the check here is the cheapest possible and it is the one that was missing: an English page
    carries no translation marker, and is not byte-identical to its translation.
    """

    @pytest.mark.parametrize("page", [p.name for p in sorted(DOCS.glob("*.md"))], ids=lambda p: p)
    def test_an_english_page_carries_no_translation_marker(self, page: str) -> None:
        text = (DOCS / page).read_text(encoding="utf-8")
        assert not text.startswith("<!-- translated-from:"), (
            f"docs/{page} begins with a translated-from marker, so it is a translation sitting at "
            f"an English path. That marker is how `docs/es/` records its source; an English page "
            f"has no source"
        )

    @pytest.mark.parametrize(
        "page",
        [p.name for p in sorted(DOCS.glob("*.md")) if (DOCS / "es" / p.name).exists()],
        ids=lambda p: p,
    )
    def test_an_english_page_is_not_its_own_translation(self, page: str) -> None:
        english = (DOCS / page).read_bytes()
        spanish = (DOCS / "es" / page).read_bytes()
        assert english != spanish, (
            f"docs/{page} and docs/es/{page} are byte-identical, so one of them overwrote the "
            f"other. The pages share every code block by construction, which is why a test "
            f"comparing only those cannot see this"
        )
