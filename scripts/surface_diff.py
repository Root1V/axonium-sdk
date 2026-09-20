#!/usr/bin/env python3
"""Compare this working tree's public Python surface against a published version.

Answers the question a consumer actually has before upgrading, which is not "does it break?" but
**"does any field I read change type?"** -- because a tolerant accessor absorbs a shape change
without breaking and without warning. Synaptum's bridge reads attribute-or-key, so `tool_calls`
going from `list[dict]` to `list[ToolCall]` in `1.0.0rc4` neither failed nor announced itself on
their side. This is what would have said so, in one line, four days earlier.

Python only. Go and Rust have ecosystem tools for this (`gorelease`, `cargo-semver-checks`) and a
third implementation of the same idea here would be one more thing to keep in step.

    python scripts/surface_diff.py                  # against the newest release on PyPI
    python scripts/surface_diff.py --against 1.0.0rc4
    python scripts/surface_diff.py --dump           # just this tree's surface, as JSON

Exits non-zero when a symbol or field is removed or re-typed. Additive change exits zero: what has
to be impossible is publishing a shape change without having looked at it, not making one.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = "axonium"

# Run inside each environment, so the surface is read from the installed package rather than
# reconstructed from source by this script.
DUMP = """
import inspect, json, axonium
surface = {"version": axonium.__version__, "all": sorted(axonium.__all__), "models": {}}
for name in axonium.__all__:
    obj = getattr(axonium, name)
    if inspect.isclass(obj) and hasattr(obj, "model_fields"):
        surface["models"][name] = {
            field: str(info.annotation) for field, info in obj.model_fields.items()
        }
print(json.dumps(surface, sort_keys=True))
"""


def newest_on_pypi() -> str:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{PACKAGE}/json", timeout=30) as response:
        return str(json.load(response)["info"]["version"])


def surface_of_working_tree() -> dict:
    result = subprocess.run(
        ["uv", "run", "python", "-c", DUMP],
        cwd=REPO / "python",
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(json.loads(result.stdout))


def surface_of_published(version: str) -> dict:
    """Install the published wheel in a throwaway environment and ask *it*.

    Deliberately not reconstructed from a git tag: what a consumer upgrades from is what the index
    served them, and a tag can disagree with it -- which is exactly how the Go module shipped
    announcing the wrong version.
    """
    with tempfile.TemporaryDirectory() as scratch:
        env = Path(scratch) / "venv"
        subprocess.run(["uv", "venv", "-q", str(env)], check=True, capture_output=True)
        python = env / "bin" / "python"
        subprocess.run(
            ["uv", "pip", "install", "-q", "--python", str(python), f"{PACKAGE}=={version}"],
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            [str(python), "-c", DUMP], capture_output=True, text=True, check=True
        )
        return dict(json.loads(result.stdout))


def compare(old: dict, new: dict) -> tuple[list[str], list[str]]:
    """Returns (additive, breaking). Breaking is a removal or a change of type."""
    additive, breaking = [], []

    for name in sorted(set(new["all"]) - set(old["all"])):
        additive.append(f"+ {name}")
    for name in sorted(set(old["all"]) - set(new["all"])):
        breaking.append(f"- {name}  (exported symbol removed)")

    for model in sorted(set(old["models"]) | set(new["models"])):
        before, after = old["models"].get(model, {}), new["models"].get(model, {})
        for field in sorted(set(after) - set(before)):
            additive.append(f"+ {model}.{field}: {after[field]}")
        for field in sorted(set(before) - set(after)):
            breaking.append(f"- {model}.{field}  (field removed)")
        for field in sorted(set(before) & set(after)):
            if before[field] != after[field]:
                breaking.append(f"~ {model}.{field}: {before[field]}  ->  {after[field]}")

    return additive, breaking


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--against", help="published version to compare with (default: newest)")
    parser.add_argument("--dump", action="store_true", help="print this tree's surface and stop")
    parser.add_argument(
        "--acknowledge-breaking",
        action="store_true",
        help="exit zero despite a removal or re-type, once it has been announced",
    )
    args = parser.parse_args()

    new = surface_of_working_tree()
    if args.dump:
        print(json.dumps(new, indent=1, sort_keys=True))
        return 0

    version = args.against or newest_on_pypi()
    old = surface_of_published(version)
    additive, breaking = compare(old, new)

    print(f"{PACKAGE} {version} (published)  ->  {new['version']} (this tree)\n")
    if not additive and not breaking:
        print("no change to the public surface")
        return 0

    if additive:
        print("additive:")
        for line in additive:
            print(f"  {line}")
    if not breaking:
        print("\nno symbol or field removed, and nothing changed type.")
        print("Safe for a consumer reading through a tolerant accessor.")
        return 0

    print("\nbreaking -- a caller reading these will not be told:")
    for line in breaking:
        print(f"  {line}")
    print(
        "\nAnnounce these before publishing. A tolerant accessor absorbs a type change without\n"
        "failing and without warning, so the consumer finds out from behaviour rather than from\n"
        "an error. Re-run with --acknowledge-breaking once they have been."
    )
    return 0 if args.acknowledge_breaking else 1


if __name__ == "__main__":
    raise SystemExit(main())
