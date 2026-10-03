#!/usr/bin/env python3
"""Every AXO id in ROADMAP.md is unique, and every row is shaped like a row.

Written because two rows were both numbered AXO-118 and nothing noticed. The ids are referenced
from commit messages, changelog entries and channel messages to other teams, so a collision cannot
be fixed by renumbering after the fact -- `b2b2691` already carries the wrong id in a published
commit. The only affordable remedy is not making a second one, which is a check rather than care.

It also refuses to pass by finding nothing: a table whose format changes would otherwise report
"all unique" about zero rows, which is the failure this repository keeps finding in its own
instruments.
"""

from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

ROADMAP = Path(__file__).resolve().parent.parent / "ROADMAP.md"

#: A row opens with the id, then the title, the status, and the body.
#:
#: The id accepts a trailing letter so a documented collision stays visible here. Without it,
#: `AXO-118b` matched nothing and the row count silently dropped by one -- this check reporting
#: "all ids unique" about a table it could no longer fully read, which is the exact defect it
#: exists to catch, committed inside the catcher.
ROW = re.compile(r"^\|\s*(AXO-\d+[a-z]?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|", re.M)

#: The line that defines what each status glyph means.
LEGEND = re.compile(r"^\*\*Status:\*\*\s*(.+)$", re.M)

#: An id that collides with another on purpose, because both are already referenced from published
#: commit messages and renumbering either one would orphan that reference. The value is why.
#:
#: This is the only entry, and adding a second one should feel expensive: it means a third id was
#: published before this check existed, which it now prevents.
KNOWN_COLLISIONS = {
    "AXO-118b": "collided with AXO-118; both ids are in published commits (94ae993, c5d6e3b)",
}


def main() -> int:
    if not ROADMAP.exists():
        print(f"{ROADMAP} does not exist", file=sys.stderr)
        return 1

    text = ROADMAP.read_text(encoding="utf-8")
    rows = ROW.findall(text)

    # Read the statuses out of the document's own legend rather than keeping a second copy here.
    # The first version of this script hardcoded them, got one wrong and invented a sixth, and
    # reported ten malformed rows that were fine -- a checker whose false positives are noise is a
    # checker that gets muted.
    legend = LEGEND.search(text)
    if not legend:
        print("ROADMAP.md has no `**Status:**` legend line to read the statuses from", file=sys.stderr)
        return 1
    statuses = set(re.findall(r"([^\sA-Za-z·]+)\s+[a-z]", legend.group(1)))
    if len(statuses) < 3:
        print(f"read only {statuses} from the legend; its format probably changed", file=sys.stderr)
        return 1

    # Guard the instrument before trusting it. The table has well over a hundred rows; a handful
    # means the pattern stopped matching the document rather than the document losing its rows.
    if len(rows) < 50:
        print(
            f"only {len(rows)} rows matched in {ROADMAP.name}. The table's format probably "
            f"changed -- this check would otherwise report every id as unique.",
            file=sys.stderr,
        )
        return 1

    problems: list[str] = []

    # A suffixed id exists only to carry a collision that cannot be renumbered, so it has to be
    # declared. Otherwise the next collision gets "resolved" by quietly appending a letter, and
    # this check passes while the reference it protects is still ambiguous.
    for identifier, title, _ in rows:
        if not identifier[-1].isdigit() and identifier not in KNOWN_COLLISIONS:
            problems.append(
                f"{identifier} ({title}) has a suffix but no entry in KNOWN_COLLISIONS. A suffix "
                f"means two published references claim the same number; say which, or use the next "
                f"free id instead."
            )

    counts = collections.Counter(identifier for identifier, _, _ in rows)
    for identifier, count in sorted(counts.items()):
        if count > 1:
            titles = [title for found, title, _ in rows if found == identifier]
            problems.append(
                f"{identifier} appears {count} times, which breaks every reference to it:\n"
                + "\n".join(f"      - {title}" for title in titles)
            )

    unknown = {
        f"{identifier}: status {status!r}"
        for identifier, _, status in rows
        if status not in statuses
    }
    problems.extend(sorted(unknown))

    if problems:
        print(f"{ROADMAP.name}:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    highest = max(int(re.sub(r"\D", "", identifier.split("-")[1])) for identifier, _, _ in rows)
    noted = f", {len(KNOWN_COLLISIONS)} documented collision" if KNOWN_COLLISIONS else ""
    print(
        f"ROADMAP.md: {len(rows)} rows, {len(statuses)} statuses from the legend, "
        f"ids unique{noted}, highest AXO-{highest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
