#!/usr/bin/env python3
"""Structural checks on `spec/`, the contract all five SDKs are built against.

A malformed file here breaks every language at once, which is why these run independently of any
SDK's test suite.

**This file exists because the checks used to live inline in `.github/workflows/spec-lint.yml`, as
heredocs.** `scripts/verify.sh` could not run them -- there was nothing to call -- so they were the
one part of CI with no local equivalent, and on 2026-09-27 manifest v20 added cases carrying a
`responses` SEQUENCE instead of a single `response`. The case check read `case["response"]` and
raised `KeyError`. That job stayed red for **twenty consecutive runs over nine days**, and the three
checks after the failing one never ran at all in that whole time, so nothing was verifying the SSE
framing or the catalog's shape either.

The two checks that were already extracted into `scripts/` -- the catalog-versus-guide check and the
roadmap ids -- were in `verify.sh` and stayed green throughout. That is the whole argument for this
file: a check CI runs and a developer cannot is a check that rots without anybody deciding to let it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "spec"


def every_json_parses() -> str:
    count = 0
    for path in sorted(SPEC.rglob("*.json")):
        json.loads(path.read_text(encoding="utf-8"))
        count += 1
    return f"{count} JSON files parse"


def cases_are_well_formed() -> str:
    """Case ids are unique, every fixture a case names exists, and no fixture is orphaned.

    A case carries either a single ``response`` or a ``responses`` **sequence** -- the second serves
    one reply per attempt, which is how the corpus pins that a stream rejected before it begins is
    retried and one interrupted mid-flight is not. Reading only ``response`` is what broke this
    check, and it is also the reading that would have silently skipped those two cases' fixtures had
    it used ``.get()`` instead of raising. Raising was the lucky part.
    """
    manifest = json.loads((SPEC / "cases" / "manifest.json").read_text(encoding="utf-8"))
    cases = manifest["cases"]

    ids = [case["id"] for case in cases]
    duplicates = sorted({name for name in ids if ids.count(name) > 1})
    assert not duplicates, f"duplicate case ids: {duplicates}"

    referenced: set[str] = set()
    for case in cases:
        replies = case.get("responses") or [case["response"]]
        for reply in replies:
            name = reply.get("body_file") or reply.get("sse_file")
            if name is None:
                # A reply with no body is legitimate -- a 204, or an error whose envelope is inline
                # in the manifest -- so this is not a failure. It is skipped rather than demanded.
                continue
            assert (SPEC / "fixtures" / name).exists(), f"{case['id']}: missing {name}"
            referenced.add(name)

    on_disk = {p.name for p in (SPEC / "fixtures").iterdir() if p.is_file()}
    orphans = sorted(on_disk - referenced)
    assert not orphans, f"fixtures referenced by no case: {orphans}"

    return f"{len(cases)} cases, {len(referenced)} fixtures, no orphans"


def sse_fixtures_keep_their_framing() -> str:
    """These are captures, not documents.

    Reformatting one silently changes what every SDK is tested against, and the change is invisible
    in review because the bytes that matter are blank lines.
    """
    count = 0
    for path in sorted((SPEC / "fixtures").glob("*.sse")):
        raw = path.read_bytes()
        assert b"\r" not in raw, f"{path.name}: CRLF line endings"
        assert raw.endswith(b"\n\n"), f"{path.name}: missing the terminating blank line"
        assert b"data: [DONE]\n" in raw, f"{path.name}: no [DONE] sentinel"
        count += 1
    assert count, "no .sse fixtures found, so this check passed by seeing nothing"
    return f"{count} SSE fixtures keep their wire framing"


def catalog_entries_are_complete() -> str:
    catalog = json.loads((SPEC / "errors.json").read_text(encoding="utf-8"))

    for entry in catalog["gateway_errors"]:
        for field in ("status", "suffix", "meaning", "retryable"):
            assert field in entry, f"{entry.get('suffix')}: missing {field}"
    for entry in catalog["oauth_errors"]:
        for field in ("status", "error", "meaning", "retryable"):
            assert field in entry, f"{entry.get('error')}: missing {field}"

    suffixes = [entry["suffix"] for entry in catalog["gateway_errors"]]
    duplicates = sorted({s for s in suffixes if suffixes.count(s) > 1})
    assert not duplicates, f"duplicate error suffixes: {duplicates}"

    return f"{len(suffixes)} gateway errors, {len(catalog['oauth_errors'])} oauth errors"


CHECKS = (
    every_json_parses,
    cases_are_well_formed,
    sse_fixtures_keep_their_framing,
    catalog_entries_are_complete,
)


def main() -> int:
    # Every check runs even when an earlier one fails. The inline version stopped at the first
    # failure, so for nine days the three checks after it were not reporting green -- they were not
    # reporting at all, which reads identically from outside.
    failures = 0
    for check in CHECKS:
        try:
            print(f"ok   {check.__name__}: {check()}")
        except AssertionError as problem:
            print(f"FAIL {check.__name__}: {problem}", file=sys.stderr)
            failures += 1
        except Exception as problem:  # noqa: BLE001 - a crash here is a failure, not a traceback
            print(f"FAIL {check.__name__}: {type(problem).__name__}: {problem}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
