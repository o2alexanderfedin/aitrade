#!/usr/bin/env bash
#
# Guardrail: run shellcheck over every *.sh file this repo tracks.
#
# Invoked as `bash tools/check-shell-scripts.sh` -- byte-identically -- by both
# `.pre-commit-config.yaml` and `.github/workflows/ci.yml`. Both callers run
# from the repo root and neither overrides a working directory, so the
# repo-root-relative path resolves the same way in both. The file list comes
# from `git ls-files`, i.e. the COMMITTED set, not whatever is lying around a
# working copy.
#
# Why not `uv run --locked --directory mvp python -m tools.check_...` like
# every other guardrail here: shellcheck is a Haskell binary, so `mvp/uv.lock`
# cannot pin it, and `mvp/` is closed to non-MVP code by the containment rule.
# What replaces the lockfile is the version assertion below plus a pinned
# download in CI.
#
# Why assert the version at all: shellcheck adds and retunes checks between
# releases, so two callers on two versions are no longer one gate -- "passed
# pre-commit, failed CI" is precisely the failure the byte-identity rule in
# both config headers exists to prevent. Compared on the first two components,
# matching `mvp/tools/check_pin_versions.py`'s `_matches_prefix` convention;
# CI installs 0.11.0 exactly.
#
# A missing shellcheck FAILS (127) rather than skipping. A guardrail that goes
# quietly green when its tool is absent is not a guardrail.
#
# KNOWN GAP, named rather than left to be discovered: the pathspec is `*.sh`,
# so an extensionless shell script (`tools/foo` with a bash shebang) is not
# found. Nothing like that is tracked today; add a second pathspec here if one
# ever is.

set -euo pipefail

#: First two components of the required shellcheck version. CI installs
#: 0.11.0 from a sha256-verified release tarball; keep the two in step.
REQUIRED_LINE=0.11

if ! SHELLCHECK_BIN="$(command -v shellcheck)"; then
  cat >&2 <<MSG
error: shellcheck not found on PATH -- this guardrail cannot run.
  macOS  : brew install shellcheck
  Ubuntu : apt-get install shellcheck   (confirm it is on the ${REQUIRED_LINE}.x line)
  pinned : https://github.com/koalaman/shellcheck/releases/tag/v0.11.0
MSG
  exit 127
fi

FOUND_VERSION="$(shellcheck --version | sed -n 's/^version: //p')"
FOUND_LINE="$(printf '%s' "$FOUND_VERSION" | cut -d. -f1,2)"
if [ "$FOUND_LINE" != "$REQUIRED_LINE" ]; then
  cat >&2 <<MSG
error: shellcheck ${REQUIRED_LINE}.x required, found ${FOUND_VERSION:-<unparseable>} at $SHELLCHECK_BIN
       CI installs 0.11.0. A different release line reports different
       findings, which breaks "passed pre-commit" => "passes CI".
MSG
  exit 1
fi

# NUL-delimited and read into an array, so a path with a space or a newline in
# it cannot be split apart. Deliberately no `mapfile`/`readarray`: macOS ships
# bash 3.2 and `bash tools/...` runs whatever bash is first on PATH.
FILES=()
while IFS= read -r -d '' file; do
  FILES+=("$file")
done < <(git ls-files -z -- '*.sh')

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "warning: no tracked *.sh files -- this guardrail checked NOTHING" >&2
  exit 0
fi

# Print what ran before running it: a green check whose log does not name the
# binary, the version and the files it looked at is a claim, not a gate.
echo "shellcheck $FOUND_VERSION ($SHELLCHECK_BIN) over ${#FILES[@]} tracked *.sh file(s):"
printf '  %s\n' "${FILES[@]}"
shellcheck -- "${FILES[@]}"
echo "check-shell-scripts: clean"
