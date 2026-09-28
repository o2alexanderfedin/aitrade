#!/usr/bin/env bash
#
# Stand up an isolated Google TimesFM 3.0 environment: a Python 3.12 venv,
# `timesfm[torch]`, and the 3.0 weights downloaded into a local Hugging Face
# cache. Prints what landed; safe to re-run.
#
# It installs OUTSIDE this git repo, and REFUSES to install inside it. That
# refusal is the point of the script, not a courtesy check. TimesFM pulls
# `torch` and `numpy>=2.5`, while `mvp/.venv` is lockfile-pinned to
# `numpy<2.5` because `numba` requires it; putting TimesFM there breaks every
# `@njit` import and makes the `uv lock --check` and `check_pin_versions`
# pre-commit hooks fail, so no commit succeeds until it is torn out again.
#
# It also lives at the repo root rather than under `mvp/` on purpose: `mvp/`
# is reserved for MVP code by the containment rule, and TimesFM is not MVP
# code -- nothing in this repo imports it.
#
# Usage:
#   tools/setup-timesfm.sh [INSTALL_ROOT]
#   TIMESFM_ROOT=/path/to/root tools/setup-timesfm.sh
#
# Install root, in order: $1, then $TIMESFM_ROOT, then a sibling of the repo
# named `timesfm`. The resolved root is printed before anything is written.
#
# Requires: `uv` on PATH, and Python 3.12 available to it.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

# Repo root from the script's own location, never the caller's cwd -- this is
# runnable from anywhere. If the script has been copied out of a git
# repository there is nothing to protect, so warn and carry on rather than
# refusing: the hazard is specifically this repo's pinned `mvp/.venv`.
if REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)"; then
  REPO_ROOT="$(cd "$REPO_ROOT" && pwd -P)"
else
  REPO_ROOT=""
  echo "warning: not inside a git repository -- skipping the in-repo guard" >&2
fi

if [ -n "${1:-}" ]; then
  REQUESTED_ROOT="$1"; ROOT_SOURCE="argument"
elif [ -n "${TIMESFM_ROOT:-}" ]; then
  REQUESTED_ROOT="$TIMESFM_ROOT"; ROOT_SOURCE="\$TIMESFM_ROOT"
elif [ -n "$REPO_ROOT" ]; then
  REQUESTED_ROOT="$(dirname "$REPO_ROOT")/timesfm"; ROOT_SOURCE="default (sibling of the repo)"
else
  echo "error: pass an install root, or set \$TIMESFM_ROOT" >&2
  exit 2
fi

command -v uv >/dev/null 2>&1 || {
  echo "error: \`uv\` not found on PATH. Install it first:" >&2
  echo "       curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 127
}

# Canonicalise before comparing, so `./x`, `mvp/../mvp/x` and a symlinked
# path cannot slip past the guard. `pwd -P` needs the directory to exist, so
# create it first -- and remove it again if it turns out to be forbidden.
CREATED_ROOT=0
[ -d "$REQUESTED_ROOT" ] || CREATED_ROOT=1
mkdir -p "$REQUESTED_ROOT"
ROOT="$(cd "$REQUESTED_ROOT" && pwd -P)"

if [ -n "$REPO_ROOT" ] && { [ "$ROOT" = "$REPO_ROOT" ] || [ "${ROOT#"$REPO_ROOT"/}" != "$ROOT" ]; }; then
  [ "$CREATED_ROOT" = 1 ] && rmdir "$ROOT" 2>/dev/null || true
  cat >&2 <<MSG
error: refusing to install TimesFM inside the git repo.
  requested : $REQUESTED_ROOT
  resolves  : $ROOT
  repo root : $REPO_ROOT
TimesFM needs torch and numpy>=2.5; mvp/.venv is pinned to numpy<2.5 for numba,
and the uv lock --check and check_pin_versions hooks fail on any drift -- which
blocks every commit until the install is removed. Also, the weights are 1.23 GiB.
Pick a root outside the repo, e.g.:
  tools/setup-timesfm.sh "$(dirname "$REPO_ROOT")/timesfm"
MSG
  exit 3
fi

echo "== install root =="
echo "  $ROOT   [from: $ROOT_SOURCE]"

export HF_HOME="$ROOT/hf-cache"
export HUGGINGFACE_HUB_CACHE="$ROOT/hf-cache"
export TOKENIZERS_PARALLELISM=false
# uv hardlinks from its cache into the venv, which cannot work when the cache
# (~/.cache, internal disk) and $ROOT (typically an external volume) are
# different filesystems. uv already falls back to copying, but it first prints a
# multi-line warning that reads like a failure. Ask for copying up front.
export UV_LINK_MODE=copy
mkdir -p "$HF_HOME" "$ROOT/work"

echo "== venv =="
if [ -d "$ROOT/.venv" ]; then
  echo "  exists, reusing: $ROOT/.venv"
else
  uv venv --python 3.12 "$ROOT/.venv"
fi
export VIRTUAL_ENV="$ROOT/.venv"

echo "== install timesfm[torch] =="
# A failure here naming a missing file under ~/.cache/uv is a CORRUPT CACHE ENTRY,
# not a broken dependency -- measured once on this machine: one entry of 274 had
# lost its METADATA while its siblings were intact. The error uv prints
# ("failed to open file .../<pkg>.dist-info/METADATA") gives no hint of that, so
# say it here rather than leaving the operator to guess.
if ! uv pip install --python "$ROOT/.venv/bin/python" "timesfm[torch]" huggingface_hub 2>&1 | tail -20; then
  cat >&2 <<'HINT'

error: the package install failed.
If the message above names a file that does not exist under ~/.cache/uv, one
cached package is corrupt. Evict just that one and re-run -- do not clear the
whole cache:
  uv cache clean <package-name>        # e.g. uv cache clean setuptools
Re-running this script is safe: it reuses the venv and any weights already
fetched, so nothing is downloaded twice.
HINT
  exit 4
fi

echo "== versions =="
"$ROOT/.venv/bin/python" - <<'PY'
import importlib.metadata as md
for p in ("timesfm", "torch", "numpy", "huggingface-hub"):
    try:
        print(f"  {p:16} {md.version(p)}")
    except Exception as e:
        print(f"  {p:16} MISSING ({e.__class__.__name__})")
import torch
print("  torch    mps available =", torch.backends.mps.is_available(),
      " cuda available =", torch.cuda.is_available())
PY

# snapshot_download verifies the cache and re-fetches only what is missing, so
# a complete cache costs one metadata call rather than 1.23 GiB.
echo "== weights =="
"$ROOT/.venv/bin/python" - <<'PY'
import os, pathlib
from huggingface_hub import snapshot_download
p = snapshot_download("google/timesfm-3.0-pytorch",
                      cache_dir=os.environ["HUGGINGFACE_HUB_CACHE"])
files = [f for f in pathlib.Path(p).rglob("*") if f.is_file()]
total = sum(f.stat().st_size for f in files)
print("  snapshot:", p)
print(f"  files: {len(files)}, {total / 2**30:.2f} GiB")
PY

cat <<MSG
== DONE ==
To use it, run your own scripts under this interpreter:
  $ROOT/.venv/bin/python
with these set, so the loader finds the weights above:
  export HF_HOME="$ROOT/hf-cache"
  export HUGGINGFACE_HUB_CACHE="$ROOT/hf-cache"
  export TOKENIZERS_PARALLELISM=false
Put such scripts in $ROOT/work -- outside the repo, beside the weights.
MSG
