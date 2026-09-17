"""CI-callable guardrail: every committed manifest JSON's `manifest_id`
field, AND its filename, must still equal `sha256(canonicalize_manifest(body))`
(T-03-09 gap-closure, 03-VERIFICATION.md finding 1).

This is `check_no_manifest_rewrite.py`'s missing complement, not a
replacement for it: that tool proves a manifest's named PARTITION FILES
still match their recorded hashes, but its `--full` scan can only run
against the real, physically mounted `/Volumes/ProjectsSSD` lake --
structurally impossible on a GitHub Actions runner (`DEFAULT_LAKE_ROOT`
docstring, `data/lake_paths.py`), so its CI leg unconditionally SKIPs.

THIS check needs no mounted lake at all: `mvp/data/lake_registry/manifests/`
is git-committed JSON, so it is present, byte-for-byte, on every CI runner,
on every clone, with zero external dependency. It cannot catch a mutated
PARTITION file (that is `check_no_manifest_rewrite`'s job, and remains a
local-pre-push + per-read `resolve_manifest` guarantee) -- but it DOES
catch a hand-edited or corrupted MANIFEST, a real, CI-checkable class of
tampering that was previously uncovered by any check that runs on every
push.

Reuses `data.store.compute_manifest_id`/`canonicalize_manifest` (the exact
functions `resolve_manifest` already calls on every read) -- never a
reimplementation, so this guardrail and the runtime guarantee it mirrors
cannot silently drift apart.

RESOLVES values, never greps: for every manifest file, parses the JSON,
recomputes the id from the parsed body, and compares both the `manifest_id`
field AND the filename stem against the recomputed value.

Invoked as `uv run --directory mvp python -m tools.check_manifest_id_integrity`
(process cwd = mvp/) by both pre-commit (`pre-commit` stage -- this is
cheap, no sha256 of multi-GB partitions, just small JSON bodies) and GitHub
Actions, byte-identical command string in both callers.
"""

from __future__ import annotations

import json
from pathlib import Path

from data.lake_paths import LAKE_REGISTRY_ROOT
from data.store import compute_manifest_id

PKG_ROOT = Path(__file__).resolve().parents[1]

#: by-date/ index files are `{"manifest_id": ...}` POINTERS, not manifests
#: themselves (no `partitions`/`dataset`/etc body to self-hash) -- same
#: marker and exclusion rule as `check_no_manifest_rewrite._iter_manifest_files`,
#: duplicated here (small, stable constant) rather than importing a
#: leading-underscore name across a module boundary (same precedent as
#: `data/dq/report.py:build_stats_path`'s docstring).
BY_DATE_MARKER = "/by-date/"


def _iter_manifest_files(registry_root: Path) -> list[Path]:
    manifests_dir = Path(registry_root) / "manifests"
    if not manifests_dir.exists():
        return []
    return [
        p
        for p in manifests_dir.glob("**/*.json")
        if BY_DATE_MARKER not in f"/{p.relative_to(manifests_dir)}"
    ]


def check_manifest_file(manifest_file: Path) -> str | None:
    """Return an error string if `manifest_file`'s recomputed id disagrees
    with either its filename stem or its own `manifest_id` field; `None` if
    both agree (all good)."""
    manifest = json.loads(manifest_file.read_text())
    recomputed = compute_manifest_id(manifest)
    stated = manifest.get("manifest_id")
    filename_id = manifest_file.stem

    if stated != recomputed:
        return (
            f"{manifest_file}: manifest_id field {stated!r} != "
            f"recomputed {recomputed!r} (body hand-edited or corrupted)"
        )
    if filename_id != recomputed:
        return (
            f"{manifest_file}: filename stem {filename_id!r} != "
            f"recomputed {recomputed!r} (renamed or misfiled manifest)"
        )
    return None


def main(argv: list[str] | None = None) -> int:
    del argv  # no flags -- this check has no mode/mount dependency at all
    manifest_files = _iter_manifest_files(LAKE_REGISTRY_ROOT)
    if not manifest_files:
        # 03-REVIEW.md WR-07: the committed registry is known to be non-empty;
        # a missing/empty manifests/ directory (or a path bug) must not read
        # as "every manifest self-verified".
        print(
            f"FAIL: found 0 manifest(s) under {Path(LAKE_REGISTRY_ROOT) / 'manifests'} "
            "-- a scan that checked nothing must not pass"
        )
        return 1

    errors = [
        err
        for manifest_file in manifest_files
        if (err := check_manifest_file(manifest_file)) is not None
    ]

    print(f"checked {len(manifest_files)} manifest(s) for id/filename self-consistency")

    if errors:
        print("FAIL: manifest(s) failed to self-verify:")
        for err in errors:
            print(f"  {err}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
