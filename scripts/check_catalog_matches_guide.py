#!/usr/bin/env python3
"""Hold spec/errors.json to the vendored guide it is derived from.

There was a guard from errors.json to the three SDKs -- every catalogued error must map to a class,
sentinel or kind with matching retryability -- and nothing at all from the guide to errors.json. So
the catalog could only go stale in the direction nobody was watching: the platform documents a new
error, we re-vendor the guide, and the catalog silently does not grow.

That is not hypothetical. `400 unknown-parameter` arrived in guide 2026-09-19b, was re-vendored
here, and was found days later by hand rather than by anything failing. The same shape has now bit
three teams in three weeks -- two copies of a header list, a modality accepted by a registry that
priced nothing, an allowlist that outlived what it described. One truth in two places, only one
updated.

What this compares is deliberately narrow: the set of error type suffixes the guide tabulates
against the set the catalog carries, in both directions. Meanings are prose and drift for good
reasons; the set of names that exist does not.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUIDE = REPO / "spec" / "prometheus-gateway.md"
CATALOG = REPO / "spec" / "errors.json"

# The error tables are `| <status> | `<suffix>` | meaning | retryable |`. Anchored on the status so
# that a backticked word elsewhere in the guide -- a header name, a field, a model id -- cannot be
# mistaken for an error type.
TABULATED = re.compile(r"^\|\s*(\d{3})\s*\|\s*`([a-z0-9-]+)`\s*\|", re.M)

# The OAuth2 codes are prose, not a table -- "Possible `error` values: `x` (400), `y` (400)..." --
# so they are read from that sentence rather than from a row. Matching snake_case anywhere would
# also catch `forbidden` and `unauthorized`, which are gateway suffixes that happen to lack a
# hyphen; the first version of this check reported both as missing OAuth codes.
OAUTH_SENTENCE = re.compile(r"Possible `error` values:(.+?)(?:\n\n|\Z)", re.S)


def main() -> int:
    guide = GUIDE.read_text(encoding="utf-8")
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))

    in_guide = {suffix for _, suffix in TABULATED.findall(guide)}
    sentence = OAUTH_SENTENCE.search(guide)
    if sentence is None:
        print(
            "Could not find the sentence listing the OAuth2 error values in the guide. It was "
            "reworded or moved, and this check cannot pass by seeing nothing.",
            file=sys.stderr,
        )
        return 1
    in_guide_oauth = set(re.findall(r"`([a-z_]+)`", sentence.group(1)))
    catalogued = {entry["suffix"] for entry in catalog["gateway_errors"]}
    catalogued_oauth = {entry["error"] for entry in catalog["oauth_errors"]}

    problems: list[str] = []

    for missing in sorted(in_guide - catalogued):
        problems.append(
            f"  {missing}\n"
            f"      tabulated in the guide, absent from spec/errors.json.\n"
            f"      A caller meeting it gets a base class chosen by status, with no name to catch."
        )
    for extra in sorted(catalogued - in_guide):
        problems.append(
            f"  {extra}\n"
            f"      in spec/errors.json, no longer tabulated in the guide.\n"
            f"      Either the platform retired it, or the table moved and this check needs to follow."
        )
    if not in_guide or not in_guide_oauth:
        print(
            "Found no error tables in the guide at all. The format changed; a check that compares "
            "two empty sets passes by seeing nothing.",
            file=sys.stderr,
        )
        return 1

    for missing in sorted(in_guide_oauth - catalogued_oauth):
        problems.append(f"  {missing}\n      OAuth2 code tabulated in the guide, absent from the catalog.")
    for extra in sorted(catalogued_oauth - in_guide_oauth):
        problems.append(f"  {extra}\n      OAuth2 code in the catalog, no longer tabulated in the guide.")

    revision = re.search(r"\*\*Revision\*\*:\s*(\S+)", guide)
    label = revision.group(1) if revision else "unknown"

    if problems:
        print(f"spec/errors.json disagrees with the vendored guide ({label}):\n", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        print(
            "\nThe guide is the source. Add or remove the catalogued entry, and the per-language\n"
            "parity guards will then require each SDK to map it.",
            file=sys.stderr,
        )
        return 1

    print(
        f"guide {label}: {len(in_guide)} gateway error types and "
        f"{len(in_guide_oauth)} OAuth2 codes, all catalogued"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
