#!/usr/bin/env bash
# The Go SDK's Version constant must not fall behind the newest published go/v* tag.
#
# Go has no release workflow to hang a tag/version check on: pushing a tag *is* the release, and
# the module proxy serves whatever it points at within minutes. Python and Rust check tag against
# packaged version inside the job that publishes, and refuse to publish on a mismatch. Go has no
# such moment, so the check has to run before the tag exists -- and the only thing checkable then
# is that the constant has not been left behind by the last release.
#
# That is the drift this exists to catch, and it is not hypothetical: go/v0.3.0 shipped with
# Version = "0.2.0", so every request from it identified itself as axonium-go/0.2.0 to the
# platform. Nothing failed. The User-Agent was simply wrong, for a month, in a field used to
# correlate client versions during incidents.
set -eu

root="$(cd "$(dirname "$0")/.." && pwd)"
constant="$(grep -m1 '^const Version = ' "$root/go/axonium/client.go" | cut -d'"' -f2)"

if [ -z "$constant" ]; then
  echo "could not read the Version constant from go/axonium/client.go" >&2
  exit 1
fi

newest="$(git -C "$root" tag --list 'go/v*' | sed 's|^go/v||' | sort -V | tail -1)"

if [ -z "$newest" ]; then
  echo "no go/v* tags yet; Version = $constant"
  exit 0
fi

# sort -V puts the lower version first. If the constant sorts first and differs, it is behind.
lower="$(printf '%s\n%s\n' "$constant" "$newest" | sort -V | head -1)"
if [ "$constant" != "$newest" ] && [ "$lower" = "$constant" ]; then
  cat >&2 <<MSG
The Go Version constant is behind the newest published tag.

  const Version = "$constant"   (go/axonium/client.go)
  newest tag      go/v$newest

Version feeds the User-Agent, so every request this module makes would report
"axonium-go/$constant" to the platform. Bump the constant to match what was released, or past it
if this branch is preparing the next one.
MSG
  exit 1
fi

echo "go: Version = $constant, newest tag go/v$newest"
