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

SITE = "Axonium"
TAGLINE = "Client SDKs for the Prometheus Gateway"
REPO_URL = "https://github.com/Root1V/axonium-sdk"

# Languages a tab group can contain. Shell is deliberately absent: tabs exist for one operation
# expressed in three languages, and three different shell commands are three different operations
# that happen to share a syntax -- grouped, they render as "Shell / Shell / Shell".
TAB_LANGUAGES = {"python": "Python", "go": "Go", "rust": "Rust"}


def pages() -> list[Path]:
    """Source pages in reading order.

    Sorting alone puts index.md *after* 07-, because "i" > "0". The symptom is that "Getting
    started" is the last item in the menu and the pagination runs from the end to the beginning.
    """
    everything = sorted(SOURCE.glob("*.md"))
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
    """Wrap runs of adjacent python/go/rust blocks in a tabset.

    The Markdown carries three plain fenced blocks, one per language, with no custom syntax: that is
    what an agent reads, and it is also what the HTML falls back to when JavaScript is unavailable,
    since the wrapper is inert without it.
    """
    pattern = re.compile(
        r'(?:<div class="code" data-language="(?:python|go|rust)"[^>]*>.*?</div>\s*){2,}',
        re.S,
    )

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
<html lang="en">
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


def build_page(page: Path, index: int, ordered: list[Path]) -> str:
    body = markdown(page.read_text(encoding="utf-8"))
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

    ordered = pages()
    if not ordered:
        print("no pages in docs/", file=sys.stderr)
        return 1

    if args.check:
        stale = []
        for index, page in enumerate(ordered):
            target = OUTPUT / f"{page.stem}.html"
            if not target.exists():
                stale.append(f"{target.relative_to(REPO)} is missing")
            elif target.read_text(encoding="utf-8") != build_page(page, index, ordered):
                stale.append(f"{target.relative_to(REPO)} does not match {page.name}")
        if stale:
            print("The rendered documentation is out of date:", file=sys.stderr)
            for line in stale:
                print(f"  {line}", file=sys.stderr)
            print("\nRun: python scripts/render_docs.py", file=sys.stderr)
            return 1
        print(f"up to date · {len(ordered)} pages")
        return 0

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for index, page in enumerate(ordered):
        (OUTPUT / f"{page.stem}.html").write_text(
            build_page(page, index, ordered), encoding="utf-8"
        )
    # GitHub Pages serves 404.html for unknown paths; the index is a better landing than a bare 404.
    (OUTPUT / "404.html").write_text(
        (OUTPUT / "index.html").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (OUTPUT / ".nojekyll").write_text("", encoding="utf-8")
    print(f"wrote {len(ordered)} pages to {OUTPUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
