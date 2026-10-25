#!/usr/bin/env python3
"""Does the README's language matrix agree with what the registries actually serve?

**This exists because the matrix has been wrong four times, and the fourth time it was a lie
rather than a staleness.** The prose under it claims every version was *read back from its own
registry*, so a number bumped by hand without reading anything turns that sentence false. On
2026-10-24 the Rust row was moved to `0.6.5` because the tag had been pushed; `crates.io` was still
serving `0.6.4`, because the publish gate was waiting for a human and had been for nine days.
Pushing a tag is not publishing, and only the registry knows the difference.

Each previous fix was by hand, which is how it came back. This asks the five indexes.

**A network failure SKIPS and says so, loudly, rather than passing.** A check that cannot reach the
registries has not verified anything, and the one thing it must not do is look like it did -- that
is the shape `check_spec.py` was written to kill. Exit 0 with an explicit SKIPPED line, so a
developer offline is not blocked and nobody reads silence as agreement.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
TIMEOUT = 20

#: A User-Agent is not politeness here: crates.io answers an empty one with a body that is not JSON.
HEADERS = {"User-Agent": "axonium-sdk release check (+https://github.com/Root1V/axonium-sdk)"}


def fetch(url: str) -> dict[str, object]:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
        return json.loads(response.read())


def pypi() -> str:
    return str(fetch("https://pypi.org/pypi/axonium/json")["info"]["version"])  # type: ignore[index]


def crates() -> str:
    return str(fetch("https://crates.io/api/v1/crates/axonium")["crate"]["max_version"])  # type: ignore[index]


def npm() -> str:
    return str(fetch("https://registry.npmjs.org/axonium")["dist-tags"]["latest"])  # type: ignore[index]


def go_versions() -> str:
    """The Go module proxy's version list, highest taken.

    `@latest` is served from a cache that lags a fresh tag by minutes, and the list endpoint answers
    plain text rather than JSON. It also OMITS a retracted version, which is the behaviour this
    needs: `v0.6.3` was retracted and must never appear in the matrix.
    """
    request = urllib.request.Request(
        "https://proxy.golang.org/github.com/!root1!v/axonium-sdk/go/@v/list", headers=HEADERS
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
        listed = response.read().decode().split()

    def key(version: str) -> tuple[int, ...]:
        return tuple(int(part) for part in version.lstrip("v").split("."))

    return max(listed, key=key)


def swift() -> str:
    """Swift has no registry, so its newest git tag *is* its published version."""
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "https://github.com/Root1V/axonium-sdk-swift.git"],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=True,
    )
    tags = {
        match.group(1)
        for match in re.finditer(r"refs/tags/(\d+\.\d+\.\d+)$", result.stdout, re.MULTILINE)
    }
    if not tags:
        raise RuntimeError("no release tags on the Swift repository")
    return max(tags, key=lambda v: tuple(int(p) for p in v.split(".")))


#: Row label in the matrix -> how to ask that language's index what it serves.
SOURCES = {
    "Python": pypi,
    "Go": go_versions,
    "Rust": crates,
    "Swift": swift,
    "TypeScript": npm,
}


def claimed() -> dict[str, str]:
    """The version each matrix row claims, read from the row rather than from a second list."""
    text = README.read_text(encoding="utf-8")
    rows = re.findall(r"^\| (Python|Go|Rust|Swift|TypeScript) \| (.+?) \| (.+?) \|$", text, re.M)
    if len(rows) != len(SOURCES):
        raise RuntimeError(f"found {len(rows)} matrix rows, expected {len(SOURCES)}")

    found: dict[str, str] = {}
    for language, _install, version in rows:
        # The cell is prose around a version: `1.0.0rc11` on PyPI, v0.6.7, 0.3.4 — its own
        # repository. Take the first version-shaped token.
        match = re.search(r"(v?\d+\.\d+\.\d+(?:rc\d+)?)", version)
        if match is None:
            raise RuntimeError(f"{language}: no version in {version!r}")
        found[language] = match.group(1)
    return found


def main() -> int:
    try:
        published = {language: source() for language, source in SOURCES.items()}
    except (urllib.error.URLError, TimeoutError, subprocess.SubprocessError, OSError) as problem:
        # Not a failure and not a pass: an unreached registry has verified nothing.
        print(f"SKIPPED: could not reach the registries ({type(problem).__name__}: {problem})")
        print("         The matrix was NOT verified. Re-run with a network connection.")
        return 0

    wrong: list[str] = []
    for language, says in claimed().items():
        serves = published[language]
        if says.lstrip("v") != serves.lstrip("v"):
            wrong.append(f"  {language}: matrix says {says}, the index serves {serves}")

    if wrong:
        print("the README matrix disagrees with what is published:", file=sys.stderr)
        print("\n".join(wrong), file=sys.stderr)
        print(
            "\nA pushed tag is not a published version: the publish gates wait for a human.\n"
            "Fix the matrix, not the registries.",
            file=sys.stderr,
        )
        return 1

    served = ", ".join(f"{name} {version}" for name, version in sorted(published.items()))
    print(f"the matrix matches every index: {served}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
