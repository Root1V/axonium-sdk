#!/usr/bin/env python3
"""Generate docs/08-reference.md from what the Python package actually exports.

Introspects ``axonium.__all__`` rather than a hand-kept list, so the reference cannot describe a
symbol that no longer exists, and cannot omit one that was added. Go and Rust have no equivalent
here on purpose: pkg.go.dev and docs.rs generate theirs from the published package, and a second
copy would be a copy that drifts.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "08-reference.md"

sys.path.insert(0, str(REPO / "python" / "src"))
import axonium  # noqa: E402

# Grouped by what the reader is doing, not alphabetically: what you touch writing the first file
# comes first, what you need while operating comes last. Anything not listed falls into "Other",
# which is a note for whoever maintains this, not for whoever reads it.
GROUPS: list[tuple[str, str, list[str]]] = [
    ("Clients", "The one object you construct.", ["Axonium", "AsyncAxonium"]),
    (
        "Configuration",
        "Supplied to the client, or read from `AXONIUM_*` variables.",
        ["AxoniumConfig", "Timeouts", "RetryPolicy", "DEFAULT_GATEWAY_BASE_URL", "__version__"],
    ),
    (
        "Requests",
        "What you send. Validated client-side against what the contract constrains.",
        [
            "ChatCompletionRequest", "EmbeddingsRequest", "ImageGenerationRequest",
            "RerankRequest", "Message", "ContentPart", "ToolCall", "FunctionCall",
        ],
    ),
    (
        "Responses",
        "What comes back. Every one keeps the raw payload, so an unmodelled field stays reachable.",
        [
            "ChatCompletion", "ChatChoice", "CompletionMessage", "CreateEmbeddingResponse",
            "Embedding", "ImagesResponse", "GeneratedImage", "RerankResponse", "RerankResult",
            "ModelList", "Model", "RequestUsage", "Usage", "Timings", "APIObject",
        ],
    ),
    (
        "Streaming",
        "A separate method from `create`, so the return type stays honest.",
        [
            "ChatCompletionStream", "AsyncChatCompletionStream", "ChatCompletionChunk",
            "StreamChoice", "ChoiceDelta",
        ],
    ),
    (
        "Correlation and budget",
        "On every response, successes included.",
        ["ResponseMeta", "RateLimitSnapshot"],
    ),
    (
        "Authentication",
        "Handled for you. Public for the cases where it is not.",
        ["TokenSet", "TokenClaims", "TokenProvider", "AsyncTokenProvider"],
    ),
    (
        "Errors",
        "One class per row of the gateway's catalog. `OAuthError` does not inherit from `APIError`:"
        " different envelope, different semantics.",
        [],  # filled below: everything ending in Error or Warning
    ),
    ("Transport", "Rarely touched directly.", ["CooldownRegistry"]),
]


def signature_of(obj: object) -> str:
    """Render a signature without the quoting ``from __future__ import annotations`` introduces.

    ``inspect.signature`` returns ``task: 'str'`` in a module using postponed evaluation. The reader
    wants the type, not a record of how it was evaluated.
    """
    try:
        raw = str(inspect.signature(obj))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    return re.sub(r"'([\w\[\]., |]+)'", r"\1", raw)


def escape_cell(text: str) -> str:
    """A ``|`` inside a table cell is a column separator. ``int | None`` splits the row in five."""
    return text.replace("|", r"\|")


def first_line(obj: object) -> str:
    doc = inspect.getdoc(obj) or ""
    return escape_cell(doc.strip().split("\n\n")[0].replace("\n", " ").strip())


def render_class(name: str, obj: type) -> list[str]:
    out = [f"### `{name}`", ""]
    doc = inspect.getdoc(obj)
    if doc:
        out += [doc.strip(), ""]

    fields = getattr(obj, "model_fields", None)
    if fields:
        out += ["| Field | Type | Default |", "|---|---|---|"]
        for field, info in fields.items():
            annotation = escape_cell(
                re.sub(r"'([\w\[\]., |]+)'", r"\1", str(info.annotation)).replace(
                    "typing.", ""
                )
            )
            default = "required" if info.is_required() else f"`{info.default!r}`"
            out.append(f"| `{field}` | `{annotation}` | {default} |")
        out.append("")
    else:
        sig = signature_of(obj)
        if sig:
            out += [f"```python\n{name}{sig}\n```", ""]

    methods = [
        (n, m)
        for n, m in sorted(vars(obj).items())
        if not n.startswith("_") and (inspect.isfunction(m) or isinstance(m, property))
    ]
    if methods:
        out += ["| Member | Summary |", "|---|---|"]
        for n, m in methods:
            target = m.fget if isinstance(m, property) else m
            label = f"`{n}`" if isinstance(m, property) else f"`{n}{signature_of(m)}`"
            out.append(f"| {label} | {first_line(target) or '—'} |")
        out.append("")
    return out


def main() -> int:
    exported = list(axonium.__all__)
    errors = sorted(n for n in exported if n.endswith(("Error", "Warning")))
    groups = [(t, d, (errors if t == "Errors" else names)) for t, d, names in GROUPS]

    placed = {n for _, _, names in groups for n in names}
    leftover = sorted(set(exported) - placed)
    if leftover:
        groups.append(
            (
                "Other",
                "Exported but not yet grouped. This heading is a note for whoever maintains this "
                "page: new symbols land here until they are given a home.",
                leftover,
            )
        )

    lines = [
        "# Python API reference",
        "",
        "> **What is the exact name of the thing I need?**",
        "",
        "Generated from `axonium.__all__`. Every symbol the package exports appears here, and "
        "nothing that it does not.",
        "",
        "Go and Rust reference documentation is generated by their own ecosystems — "
        "[pkg.go.dev](https://pkg.go.dev/github.com/Root1V/axonium-sdk/go) and "
        "[docs.rs](https://docs.rs/axonium/latest/axonium/). Duplicating them here would create a "
        "second copy to keep in step.",
        "",
        f"Covers **{len(exported)} exported symbols**.",
        "",
    ]

    for title, blurb, names in groups:
        present = [n for n in names if n in placed or title == "Other"]
        present = [n for n in present if hasattr(axonium, n)]
        if not present:
            continue
        lines += [f"## {title}", "", blurb, ""]

        if title == "Errors":
            lines += ["| Class | Meaning |", "|---|---|"]
            for name in present:
                lines.append(f"| `{name}` | {first_line(getattr(axonium, name)) or '—'} |")
            lines.append("")
            continue

        for name in present:
            obj = getattr(axonium, name)
            if inspect.isclass(obj):
                lines += render_class(name, obj)
            elif inspect.isfunction(obj):
                lines += [f"### `{name}`", "", f"```python\n{name}{signature_of(obj)}\n```", ""]
                doc = inspect.getdoc(obj)
                if doc:
                    lines += [doc.strip(), ""]
            else:
                lines += [f"### `{name}`", "", f"```python\n{name} = {obj!r}\n```", ""]

    OUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)} · {len(exported)} symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
