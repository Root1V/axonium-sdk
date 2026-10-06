#!/usr/bin/env python3
"""Render docs/*.md into docs/html/*.html.

One source, two outputs. The Markdown is written by a person and is what an agent reads; the HTML
is derived and is what a person reads. Neither is edited by hand -- writing both guarantees they
diverge, and the one that diverges is always the one nobody looks at.

``--check`` re-renders into memory and reports any page whose HTML no longer matches its Markdown,
which is what keeps that guarantee from being a promise.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
from pathlib import Path

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "docs"
OUTPUT = SOURCE / "html"

# The site is published in two languages. English is the source: a page exists in English first, and
# a Spanish page is a TRANSLATION OF ONE, which is why it records which revision it was made from.
#
# The risk a second language adds is not the writing, it is the drift -- a translation that quietly
# stops matching is worse than no translation, because a reader cannot tell. So every Spanish page
# carries the digest of the English file it was translated from, and a test fails the moment the
# English one changes. The translation is then stale ON PURPOSE and says so, instead of being wrong
# in silence.
LOCALES = {
    "en": {"dir": SOURCE, "out": OUTPUT, "label": "English", "other": "es"},
    "es": {"dir": SOURCE / "es", "out": OUTPUT / "es", "label": "Español", "other": "en"},
}

#: Pages that are GENERATED rather than written, and so are not translated.
#:
#: `08-reference.md` is produced by `render_reference.py` from the Python package's own exports. A
#: translation of it would need redoing on every release and would be stale between every two, which
#: is the drift this whole mechanism exists to refuse -- so the Spanish site carries a short page
#: pointing at the English one and saying why, rather than a copy that is wrong most of the time.
GENERATED = {"08-reference.md"}

#: What a non-translated page declares instead of a digest.
NOT_A_TRANSLATION = re.compile(r"^<!-- not-a-translation: (?P<reason>[^>]*[^ >]) -->")

#: The first line of every translated page. The digest is of the English source's bytes.
TRANSLATED_FROM = re.compile(r"^<!-- translated-from: (?P<name>[\w.-]+) sha256:(?P<digest>[0-9a-f]+) -->")

#: Ten hex characters of SHA-256. Long enough that a collision is not a thing that happens, short
#: enough to read in a diff and to retype when updating a translation by hand.
DIGEST_LENGTH = 10


def digest_of(path: Path) -> str:
    """The identity of an English page, as its translation records it."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:DIGEST_LENGTH]


def stale_translations() -> list[str]:
    """Spanish pages whose English source has changed since they were translated.

    Also reports a translation whose source no longer exists, and an English page with no
    translation at all -- a site offering a language switch that lands on a 404 is worse than one
    that does not offer it.
    """
    problems: list[str] = []
    english = {p.name for p in LOCALES["en"]["dir"].glob("*.md")}
    spanish_dir = LOCALES["es"]["dir"]
    if not spanish_dir.exists():
        return [f"{spanish_dir.relative_to(REPO)} does not exist"]

    translated = {p.name for p in spanish_dir.glob("*.md")}
    for name in sorted(english - translated):
        problems.append(f"docs/{name} has no Spanish translation")
    for name in sorted(translated - english):
        problems.append(f"docs/es/{name} translates a page that no longer exists")

    for name in sorted(english & translated):
        header = (spanish_dir / name).read_text(encoding="utf-8").split("\n", 1)[0]
        if name in GENERATED:
            if not NOT_A_TRANSLATION.match(header):
                problems.append(
                    f"docs/es/{name} stands in for a generated page, so its first line must be "
                    f"<!-- not-a-translation: ... --> saying why"
                )
            continue
        match = TRANSLATED_FROM.match(header)
        if not match:
            problems.append(
                f"docs/es/{name}: first line must be "
                f"<!-- translated-from: {name} sha256:{digest_of(LOCALES['en']['dir'] / name)} -->"
            )
            continue
        current = digest_of(LOCALES["en"]["dir"] / name)
        if match["digest"] != current:
            problems.append(
                f"docs/es/{name} was translated from docs/{name} at {match['digest']}, "
                f"which is now {current} -- re-translate, then update the marker"
            )
    return problems

SITE = "Axonium"
TAGLINE = "Client SDKs for the Prometheus inference platform"
REPO_URL = "https://github.com/Root1V/axonium-sdk"

# Languages a tab group can contain, in the order a group should list them. Shell is deliberately
# absent: tabs exist for ONE operation expressed in every language, and five different shell
# commands are five different operations that happen to share a syntax -- grouped, they would render
# as "Shell / Shell / Shell / Shell / Shell".
#
# This dict is the only place the set is written down. The grouping regex below derives from it, and
# so does the test that refuses a group speaking fewer languages than the site claims -- that test
# used to keep its own copy, which is how this site documented three SDKs for the two weeks after
# the fourth shipped.
TAB_LANGUAGES = {
    "python": "Python",
    "go": "Go",
    "rust": "Rust",
    "swift": "Swift",
    "typescript": "TypeScript",
}


def pages(directory: Path = SOURCE) -> list[Path]:
    """Source pages in reading order.

    Sorting alone puts index.md *after* 07-, because "i" > "0". The symptom is that "Getting
    started" is the last item in the menu and the pagination runs from the end to the beginning.
    """
    everything = sorted(directory.glob("*.md"))
    return [p for p in everything if p.stem == "index"] + [
        p for p in everything if p.stem != "index"
    ]


def title_of(page: Path) -> str:
    for line in page.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return page.stem


def render_code(code: str, language: str) -> str:
    """Highlight at build time, never in the browser.

    A CDN script means the page needs the network, flashes uncoloured code first, and puts a third
    party inside documentation the reader is trusting.
    """
    try:
        lexer = get_lexer_by_name(language or "text")
    except ClassNotFound:
        lexer = get_lexer_by_name("text")
    formatter = HtmlFormatter(nowrap=True)
    return highlight(code, lexer, formatter).rstrip("\n")


def markdown(text: str) -> str:
    md = MarkdownIt("commonmark", {"typographer": False}).enable("table").enable("strikethrough")

    def fence(tokens, index, options, env):  # noqa: ANN001, ARG001
        token = tokens[index]
        language = (token.info or "").strip().split()[0] if token.info else ""
        body = render_code(token.content, language)
        label = TAB_LANGUAGES.get(language, language or "")
        return (
            f'<div class="code" data-language="{html.escape(language)}"'
            f' data-label="{html.escape(label)}">'
            f'<pre><code class="language-{html.escape(language)}">{body}</code></pre></div>\n'
        )

    md.renderer.rules["fence"] = fence
    return md.render(text)


def group_code_tabs(body: str) -> str:
    """Wrap runs of adjacent blocks in the TAB_LANGUAGES set in a tabset.

    The Markdown carries three plain fenced blocks, one per language, with no custom syntax: that is
    what an agent reads, and it is also what the HTML falls back to when JavaScript is unavailable,
    since the wrapper is inert without it.
    """
    # The inner part is forbidden from containing a block terminator. Written as `.*?</div>` it
    # looks equivalent and is not: `.*?` backtracks, so one "repetition" could swallow the prose
    # between two blocks and end at a later `</div>`. That put a paragraph, a warning and an `<h2>`
    # inside a tabset, and rendered four tabs reading "Python, Python, Go, Rust".
    end = r"</code></pre></div>"
    languages = "|".join(re.escape(name) for name in TAB_LANGUAGES)
    one_block = (
        r'<div class="code" data-language="(?:' + languages + r')"[^>]*>'
        r"<pre><code[^>]*>(?:(?!" + end + r").)*?" + end
    )
    pattern = re.compile(r"(?:" + one_block + r"\s*){2,}", re.S)

    def wrap(match: re.Match[str]) -> str:
        return f'<div class="tabs">{match.group(0)}</div>'

    return pattern.sub(wrap, body)


def rewrite_links(body: str) -> str:
    """Markdown links point at .md -- correct in the repository and for an agent. HTML needs .html."""
    return re.sub(r'href="([^":/]+)\.md(#[^"]*)?"', r'href="\1.html\2"', body)


STYLE = """
:root {
  --bg: #fbfbfa; --panel: #ffffff; --ink: #1c1d1f; --muted: #6a6d72; --line: #e3e3e0;
  --accent: #1a5fb4; --accent-soft: #eaf1fb; --warn: #8a5a00; --warn-soft: #fdf4e3;
  --code-bg: #f6f6f4; --code-ink: #24292f;
  --c-key: #a3005f; --c-str: #0a6b39; --c-num: #8a4b00; --c-com: #6a6d72;
  --c-fn: #4b3fbb; --c-type: #0b6a72; --c-punc: #4a4d52;
  --radius: 7px; --measure: 41rem;
}
:root:not([data-theme="light"]) { }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #17181a; --panel: #1d1f22; --ink: #e7e8ea; --muted: #9ba0a6; --line: #2e3136;
    --accent: #7eb3ff; --accent-soft: #1b2739; --warn: #e3b566; --warn-soft: #2a2213;
    --code-bg: #151719; --code-ink: #dfe2e6;
    --c-key: #ff8fc4; --c-str: #86d9a4; --c-num: #e8b07a; --c-com: #8b9199;
    --c-fn: #b9aaff; --c-type: #6fd3dd; --c-punc: #aab0b8;
  }
}
:root[data-theme="dark"] {
  --bg: #17181a; --panel: #1d1f22; --ink: #e7e8ea; --muted: #9ba0a6; --line: #2e3136;
  --accent: #7eb3ff; --accent-soft: #1b2739; --warn: #e3b566; --warn-soft: #2a2213;
  --code-bg: #151719; --code-ink: #dfe2e6;
  --c-key: #ff8fc4; --c-str: #86d9a4; --c-num: #e8b07a; --c-com: #8b9199;
  --c-fn: #b9aaff; --c-type: #6fd3dd; --c-punc: #aab0b8;
}

* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.65 ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.shell { display: grid; grid-template-columns: 16rem minmax(0, 1fr); gap: 3rem;
  max-width: 68rem; margin: 0 auto; padding: 0 16px; }

nav { position: sticky; top: 0; align-self: start; height: 100vh; overflow-y: auto;
  padding: 2rem 0; border-right: 1px solid var(--line); }
nav .brand { font-weight: 650; font-size: 1.05rem; letter-spacing: -0.01em; }
nav .brand a { color: var(--ink); text-decoration: none; }
nav .tag { color: var(--muted); font-size: 0.82rem; margin: 0.15rem 0 1.4rem; }
nav ol { list-style: none; margin: 0; padding: 0; }
nav li { margin: 0.1rem 0; }
nav a { display: block; padding: 0.3rem 0.6rem; margin-left: -0.6rem; border-radius: var(--radius);
  color: var(--muted); text-decoration: none; font-size: 0.9rem; }
nav a:hover { color: var(--ink); background: var(--accent-soft); }
nav a[aria-current="page"] { color: var(--accent); background: var(--accent-soft); font-weight: 560; }
/* Both languages are always shown, with the current one marked rather than hidden: a switch that
   removes the language you are reading leaves you guessing which one that is. */
nav .langs { display: flex; gap: 0.4rem; margin-top: 1.4rem; font-size: 0.84rem; }
nav .langs a { padding: 0.15rem 0.5rem; border: 1px solid var(--line); border-radius: 0.3rem;
  color: var(--muted); text-decoration: none; }
nav .langs a:hover { color: var(--fg); border-color: var(--muted); }
nav .langs a[aria-current="true"] { color: var(--accent); border-color: var(--accent);
  background: var(--code-bg); }

nav .ext { margin-top: 1.5rem; padding-top: 1rem; border-top: 1px solid var(--line); }
nav .ext a { font-size: 0.84rem; }

main { padding: 2.6rem 0 5rem; min-width: 0; }
main > * { max-width: var(--measure); }
main > .code, main > .tabs, main > .table-scroll { max-width: min(54rem, 100%); }

h1 { font-size: 2rem; line-height: 1.2; letter-spacing: -0.02em; margin: 0 0 1.2rem; }
h2 { font-size: 1.3rem; letter-spacing: -0.01em; margin: 2.6rem 0 0.8rem;
  padding-top: 1.2rem; border-top: 1px solid var(--line); }
h3 { font-size: 1.05rem; margin: 1.8rem 0 0.6rem; }
p, li { color: var(--ink); }
a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }

blockquote { margin: 1.4rem 0; padding: 0.85rem 1.1rem; border-left: 3px solid var(--warn);
  background: var(--warn-soft); border-radius: 0 var(--radius) var(--radius) 0; }
blockquote p { margin: 0.3rem 0; color: var(--warn); }
blockquote strong { color: var(--warn); }

code { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.875em; }
:not(pre) > code { background: var(--code-bg); padding: 0.12em 0.36em; border-radius: 4px;
  border: 1px solid var(--line); }

.code { position: relative; margin: 1.1rem 0; }
.code pre { margin: 0; overflow-x: auto; background: var(--code-bg); color: var(--code-ink);
  border: 1px solid var(--line); border-radius: var(--radius); padding: 0.9rem 1rem; line-height: 1.55; }
.code .copy { position: absolute; top: 0.5rem; right: 0.5rem; opacity: 0; transition: opacity .12s;
  font: inherit; font-size: 0.75rem; padding: 0.2rem 0.55rem; cursor: pointer;
  color: var(--muted); background: var(--panel); border: 1px solid var(--line);
  border-radius: 5px; }
.code:hover .copy, .copy:focus-visible { opacity: 1; }
@media (hover: none) { .code .copy { opacity: 1; } }

.tabs { margin: 1.1rem 0; }
.tabs .code { margin: 0; }
.tabs .tablist { display: flex; gap: 0.25rem; margin-bottom: -1px; }
.tabs .tablist button { font: inherit; font-size: 0.82rem; cursor: pointer;
  padding: 0.3rem 0.8rem; color: var(--muted); background: transparent;
  border: 1px solid transparent; border-radius: var(--radius) var(--radius) 0 0; }
.tabs .tablist button[aria-selected="true"] { color: var(--accent); background: var(--code-bg);
  border-color: var(--line); border-bottom-color: var(--code-bg); }
.tabs.enhanced .code pre { border-top-left-radius: 0; }

.table-scroll { overflow-x: auto; margin: 1.2rem 0; border: 1px solid var(--line);
  border-radius: var(--radius); }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.5rem 0.8rem; border-bottom: 1px solid var(--line); }
th { font-weight: 600; background: var(--code-bg); }
tr:last-child td { border-bottom: none; }

.pager { display: flex; justify-content: space-between; gap: 1rem; margin-top: 3.5rem;
  padding-top: 1.4rem; border-top: 1px solid var(--line); font-size: 0.9rem; }
.pager a { display: block; text-decoration: none; }
.pager .dir { display: block; color: var(--muted); font-size: 0.78rem; }
.pager .next { text-align: right; margin-left: auto; }
footer { margin-top: 2.5rem; color: var(--muted); font-size: 0.82rem; }

/* Pygments token classes, exactly as the highlighter emits them. Inventing names here is how a
   stylesheet ends up styling nothing: the classes in the HTML come from Pygments, not from us. */
.k, .kc, .kd, .kn, .kp, .kr { color: var(--c-key); }
.s, .s1, .s2, .sa, .sb, .sc, .dl, .sd, .se, .sh, .si, .sx, .sr, .ss { color: var(--c-str); }
.m, .mb, .mf, .mh, .mi, .mo, .il { color: var(--c-num); }
.c, .c1, .cm, .cs, .cp, .cpf, .ch { color: var(--c-com); font-style: italic; }
.nf, .fm, .nd { color: var(--c-fn); }
.kt, .nc, .nn, .nb, .ne, .no, .bp { color: var(--c-type); }
.p, .o, .ow { color: var(--c-punc); }
.err { color: var(--ink); background: none; }
/* Deliberately unstyled, inheriting the base colour: .n and .nx are plain identifiers (every Go
   symbol is .nx), .w is whitespace, .nl/.py/.nv/.vi are names too. Colouring every identifier is
   how highlighting turns into noise. */

@media (max-width: 62rem) {
  .shell { grid-template-columns: 1fr; gap: 0; }
  nav { position: static; height: auto; border-right: none; border-bottom: 1px solid var(--line);
    padding: 1.4rem 0 1rem; }
  nav ol { columns: 2; column-gap: 1rem; }
  /* Without this, a two-line entry is split across the column break and reads as two entries. */
  nav li { break-inside: avoid; page-break-inside: avoid; }
  main { padding-top: 1.8rem; }
}
"""

SCRIPT = """
(function () {
  document.querySelectorAll('.code').forEach(function (block) {
    var button = document.createElement('button');
    button.className = 'copy';
    button.type = 'button';
    button.textContent = 'Copy';
    button.addEventListener('click', function () {
      // file:// is not a secure context, so the API is simply absent there. Say so rather than
      // reporting a copy that did not happen.
      if (!navigator.clipboard) { button.textContent = 'Unavailable'; return; }
      navigator.clipboard.writeText(block.querySelector('code').innerText).then(function () {
        button.textContent = 'Copied';
        setTimeout(function () { button.textContent = 'Copy'; }, 1200);
      });
    });
    block.appendChild(button);
  });

  document.querySelectorAll('.tabs').forEach(function (group) {
    var blocks = Array.prototype.slice.call(group.querySelectorAll('.code'));
    if (blocks.length < 2) return;
    var list = document.createElement('div');
    list.className = 'tablist';
    list.setAttribute('role', 'tablist');

    function select(index) {
      blocks.forEach(function (b, i) { b.hidden = i !== index; });
      Array.prototype.forEach.call(list.children, function (t, i) {
        t.setAttribute('aria-selected', String(i === index));
      });
      try { localStorage.setItem('axonium-lang', blocks[index].dataset.language); } catch (e) {}
    }

    blocks.forEach(function (block, index) {
      var tab = document.createElement('button');
      tab.type = 'button';
      tab.setAttribute('role', 'tab');
      tab.textContent = block.dataset.label || block.dataset.language;
      tab.addEventListener('click', function () { select(index); });
      list.appendChild(tab);
    });

    group.insertBefore(list, group.firstChild);
    group.classList.add('enhanced');

    var remembered = 0;
    try {
      var saved = localStorage.getItem('axonium-lang');
      blocks.forEach(function (b, i) { if (b.dataset.language === saved) remembered = i; });
    } catch (e) {}
    select(remembered);
  });
})();
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · {site}</title>
<meta name="description" content="{tagline}">
<style>{style}</style>
</head>
<body>
<div class="shell">
<nav>
  <div class="brand"><a href="index.html">{site}</a></div>
  <div class="tag">{tagline}</div>
  <ol>{menu}</ol>
  <div class="langs">{langs}</div>
  <div class="ext">
    <a href="https://pkg.go.dev/github.com/Root1V/axonium-sdk/go">Go reference ↗</a>
    <a href="https://docs.rs/axonium/latest/axonium/">Rust reference ↗</a>
    <a href="{repo}">Source ↗</a>
  </div>
</nav>
<main>
{body}
{pager}
<footer>{site} · <a href="{repo}">{repo_label}</a> · MIT</footer>
</main>
</div>
<script>{script}</script>
</body>
</html>
"""


def language_switch(locale: str, stem: str) -> str:
    """Both languages, always, with the current one marked rather than removed.

    A switch that hides the language you are reading leaves a reader guessing which one that is.
    """
    links = []
    for code, meta in LOCALES.items():
        href = f"{stem}.html" if code == locale else (
            f"es/{stem}.html" if code == "es" else f"../{stem}.html"
        )
        current = ' aria-current="true"' if code == locale else ""
        links.append(f'<a href="{href}" hreflang="{code}"{current}>{meta["label"]}</a>')
    return "".join(links)


def build_page(page: Path, index: int, ordered: list[Path], locale: str = "en") -> str:
    source = page.read_text(encoding="utf-8")
    # The translation marker is machinery, not content: it identifies which English revision this
    # page was made from, and the reader has no use for it.
    source = TRANSLATED_FROM.sub("", source, count=1)
    source = NOT_A_TRANSLATION.sub("", source, count=1).lstrip("\n")
    body = markdown(source)
    body = rewrite_links(body)
    body = group_code_tabs(body)
    body = re.sub(r"<table>", '<div class="table-scroll"><table>', body)
    body = re.sub(r"</table>", "</table></div>", body)

    menu = "".join(
        f'<li><a href="{p.stem}.html"'
        + (' aria-current="page"' if p == page else "")
        + f">{html.escape(title_of(p))}</a></li>"
        for p in ordered
    )

    links = []
    if index > 0:
        previous = ordered[index - 1]
        links.append(
            f'<a class="prev" href="{previous.stem}.html">'
            f'<span class="dir">Previous</span>{html.escape(title_of(previous))}</a>'
        )
    if index < len(ordered) - 1:
        following = ordered[index + 1]
        links.append(
            f'<a class="next" href="{following.stem}.html">'
            f'<span class="dir">Next</span>{html.escape(title_of(following))}</a>'
        )
    pager = f'<div class="pager">{"".join(links)}</div>' if links else ""

    return TEMPLATE.format(
        lang=locale,
        langs=language_switch(locale, page.stem),
        title=html.escape(title_of(page)),
        site=SITE,
        tagline=html.escape(TAGLINE),
        style=STYLE,
        script=SCRIPT,
        menu=menu,
        body=body,
        pager=pager,
        repo=REPO_URL,
        repo_label=REPO_URL.replace("https://", ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report pages whose HTML no longer matches their Markdown, and write nothing",
    )
    args = parser.parse_args()

    if not pages():
        print("no pages in docs/", file=sys.stderr)
        return 1

    drift = stale_translations()

    if args.check:
        stale = list(drift)
        for locale, meta in LOCALES.items():
            ordered = pages(meta["dir"])
            for index, page in enumerate(ordered):
                target = meta["out"] / f"{page.stem}.html"
                if not target.exists():
                    stale.append(f"{target.relative_to(REPO)} is missing")
                elif target.read_text(encoding="utf-8") != build_page(page, index, ordered, locale):
                    stale.append(f"{target.relative_to(REPO)} does not match {page.name}")
        if stale:
            print("The rendered documentation is out of date:", file=sys.stderr)
            for line in stale:
                print(f"  {line}", file=sys.stderr)
            print("\nRun: python scripts/render_docs.py", file=sys.stderr)
            return 1
        print(f"up to date · {sum(len(pages(m['dir'])) for m in LOCALES.values())} pages")
        return 0

    # A translation that has fallen behind still RENDERS -- taking the page down would punish the
    # reader for a maintenance failure -- but it cannot be written without the build saying so.
    if drift:
        print("Translations need attention:", file=sys.stderr)
        for line in drift:
            print(f"  {line}", file=sys.stderr)

    written = 0
    for locale, meta in LOCALES.items():
        ordered = pages(meta["dir"])
        meta["out"].mkdir(parents=True, exist_ok=True)
        for index, page in enumerate(ordered):
            (meta["out"] / f"{page.stem}.html").write_text(
                build_page(page, index, ordered, locale), encoding="utf-8"
            )
        written += len(ordered)
    # GitHub Pages serves 404.html for unknown paths; the index is a better landing than a bare 404.
    (OUTPUT / "404.html").write_text(
        (OUTPUT / "index.html").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (OUTPUT / ".nojekyll").write_text("", encoding="utf-8")
    print(f"wrote {written} pages to {OUTPUT.relative_to(REPO)} in {len(LOCALES)} languages")
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
