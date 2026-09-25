#!/usr/bin/env bash
# Runs exactly what CI runs, in one command.
#
# This exists because a local check that is *nearly* the CI check is worse than no local check: it
# reports green and hides the difference. Two real failures made the point on one push -- `ruff
# check` passing while `ruff format --check` did not, and `clippy --all-features` passing while
# CI's featureless `clippy -D warnings` found dead code that only exists without the feature.
#
# Keep the commands below identical to the workflow files. If one changes there, change it here in
# the same commit: a drift between them is the failure this script is for.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
failed=()

step() {
  local label="$1"; shift
  printf '\033[1m==> %s\033[0m\n' "$label"
  if ! "$@"; then
    failed+=("$label")
  fi
}

# --- spec-lint.yml ---
# From the repository root, as that workflow does. The first version of this line ran from python/,
# where the relative path does not resolve -- CI would have stayed green while this went red, which
# is the divergence this script exists to prevent, pointing the other way.
cd "$root" || exit 1
step "spec: catalog matches guide" ./scripts/check_catalog_matches_guide.py

# --- python-ci.yml ---
cd "$root/python" || exit 1
step "python: ruff check"        uv run ruff check
step "python: ruff format"       uv run ruff format --check
step "python: mypy"              uv run mypy src/
step "python: pytest"            uv run pytest -q

# --- docs.yml, and the --check python-ci runs ---
step "docs: html matches markdown" uv run python ../scripts/render_docs.py --check

# --- go-ci.yml ---
cd "$root" || exit 1
step "go: version not behind tag" ./scripts/check_go_version.sh
cd "$root/go" || exit 1
step "go: vet"                   go vet ./...
step "go: build"                 go build ./...
# -race, as CI does: a data race the untraced run never sees is the whole reason it is on.
step "go: test -race"            go test -race ./...

# --- rust-ci.yml ---
cd "$root/rust" || exit 1
step "rust: fmt"                 cargo fmt --check
# Without --all-features, exactly as CI: a constant used only behind a feature is dead code here,
# and an --all-features run cannot see it.
step "rust: clippy"              cargo clippy --all-targets -- -D warnings
step "rust: test"                cargo test
# Not in CI, but the feature it gates is shipped, so it is checked too.
step "rust: test --all-features" cargo test --all-features

printf '\n'
if [ ${#failed[@]} -eq 0 ]; then
  printf '\033[1;32mall green\033[0m\n'
  exit 0
fi
printf '\033[1;31mfailed:\033[0m\n'
printf '  %s\n' "${failed[@]}"
exit 1
